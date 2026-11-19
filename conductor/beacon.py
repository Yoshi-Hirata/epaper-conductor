"""Wire format and read-only UDP sender for the one-way beacon."""

import hashlib
import hmac
import ipaddress
import itertools
import math
import socket
import struct
import threading
from urllib.parse import urlsplit

from .fleet import pc_clock


_HEADER = struct.Struct("!4sBBBBIII")
_ENTRY = struct.Struct("!BBddd")
_OVERHEAD = _HEADER.size + hashlib.sha256().digest_size
_LIMIT = 1200


def beacon_key(token):
    return hashlib.sha256(b"epaper-beacon-v1\0" + (token or "").encode("utf-8")).digest()


def _encode(entry):
    host = entry["host"].encode("utf-8")
    show = entry["show"].encode("ascii")
    if not (1 <= len(host) <= 32 and 1 <= len(show) <= 32):
        raise ValueError("beacon host and show must each occupy 1–32 bytes")
    state, flags = entry["state"], entry["flags"]
    times = entry["now"], entry["t0"], entry["pos"]
    if state not in (1, 2, 3) or flags not in (0, 1) or not all(map(math.isfinite, times)):
        raise ValueError("invalid beacon entry")
    return bytes([len(host)]) + host + bytes([len(show)]) + show + _ENTRY.pack(
        state, flags, *times)


def pack(cid, gen, seq, entries, key):
    payload = b"".join(map(_encode, entries))
    if not entries or len(payload) + _OVERHEAD > _LIMIT:
        raise ValueError("invalid beacon datagram size")
    body = _HEADER.pack(b"EPBC", 1, 0, len(entries), 0, cid, gen & 0xFFFFFFFF, seq) + payload
    return body + hmac.digest(key, body, "sha256")


def split(entries):
    chunks = []
    size = _LIMIT
    for entry in entries:
        length = len(_encode(entry))
        if size + length > _LIMIT:
            chunks.append([])
            size = _OVERHEAD
        chunks[-1].append(entry)
        size += length
    return chunks


def _unit_ip(address):
    host = urlsplit(address if "://" in address else "//" + address).hostname
    if not host:
        raise ValueError(f"invalid unit address {address!r}")
    try:
        return ipaddress.IPv4Address(host)
    except ValueError:
        return ipaddress.IPv4Address(socket.gethostbyname(host))


def _network(ip):
    return ipaddress.IPv4Network((ip, 24), strict=False)


def _unicast_target(ip):
    return "127.0.0.1" if ip.is_loopback else str(ip)


def _routes(ips, local, override):
    if local is None:
        try:
            local = set(socket.gethostbyname_ex(socket.gethostname())[2])
        except OSError:
            local = set()
        representatives = {_network(ip): ip for ip in ips if not ip.is_loopback}
        for ip in representatives.values():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                    probe.connect((str(ip), 8788))
                    local.add(probe.getsockname()[0])
            except OSError:
                continue
    locals_v4 = sorted({ipaddress.IPv4Address(addr) for addr in local})
    prefixes = {_network(ip) for ip in ips if not ip.is_loopback}
    if override is None:
        broadcasts = [(str(addr), str(_network(addr).broadcast_address), True)
                      for addr in locals_v4 if _network(addr) in prefixes]
    else:
        broadcasts = []
        for target in dict.fromkeys(map(ipaddress.IPv4Address, override)):
            sources = [str(addr) for addr in locals_v4 if _network(addr) == _network(target)]
            broadcasts.extend((source, str(target), True) for source in sources or [""])
    unicast = [("", target, False) for target in sorted({_unicast_target(ip) for ip in ips})]
    missing = prefixes - {_network(addr) for addr in locals_v4}
    return broadcasts + unicast, missing if override is None else set()


def destinations(addresses, local=None, override=None):
    """Return (bind address, target IPv4 address, broadcast) routes."""
    if hasattr(addresses, "values"):
        addresses = addresses.values()
    return _routes([_unit_ip(addr) for addr in addresses], local, override)[0]


class BeaconSender:
    def __init__(self, fleet, token=None, enabled=False, port=8788,
                 destinations=None, clock=pc_clock, tick_s=0.2, sock_factory=None,
                 *, unicast=True):
        self._fleet = fleet
        self._token = token
        self._key = None
        self._port = port
        self._override = destinations
        self._unicast = unicast
        self._clock = clock
        self._tick_s = tick_s
        self._sock_factory = sock_factory or socket.socket
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread = None
        self._enabled = enabled
        self._sequences = itertools.count(1)
        self._sent = self._errors = 0
        self._last_error = None
        self._routes = []
        self._unit_targets = {}
        self._sockets = {}
        self._network_skipped = {}
        self._skipped = {}
        self._refresh_at = -math.inf
        self._forget()

    def _forget(self):
        self._signature = None
        self._changed_at = self._last_send = -math.inf
        self._interval = None
        self._announced = {}
        self._stop_at = None

    def start(self):
        with self._lock:
            if self._thread is None:
                self._done.clear()
                self._thread = threading.Thread(target=self._loop, name="beacon", daemon=True)
                self._thread.start()

    def stop(self):
        with self._lock:
            thread = self._thread
            self._done.set()
        if thread is not None:
            thread.join()
        with self._lock:
            self._thread = None
            self._forget()
            self._close_sockets()

    def _loop(self):
        while not self._done.is_set():
            self.tick()
            self._done.wait(self._tick_s)

    def _close_sockets(self):
        for sock in self._sockets.values():
            if sock is None:
                continue
            try:
                sock.close()
            except Exception as exc:
                self._error(exc)
        self._sockets.clear()
        self._routes = []
        self._unit_targets = {}
        self._refresh_at = -math.inf

    def set_enabled(self, on):
        with self._lock:
            if self._enabled != on:
                self._enabled = on
                self._forget()
                self._skipped = {}
                self._network_skipped = {}
                self._close_sockets()

    def snapshot(self):
        with self._lock:
            return dict(enabled=self._enabled, sent=self._sent, errors=self._errors,
                        last_error=self._last_error, destinations=[dict(
                            local=local, address=target, port=self._port, broadcast=broadcast)
                            for local, target, broadcast in self._routes],
                        skipped={**self._network_skipped, **self._skipped})

    def _error(self, exc):
        self._errors += 1
        self._last_error = str(exc)

    def _refresh(self, now):
        self._close_sockets()
        self._refresh_at = now + 10
        ips, self._network_skipped = {}, {}
        for name, link in self._fleet.links.items():
            try:
                ips[name] = _unit_ip(link.address)
            except (OSError, ValueError) as exc:
                self._network_skipped[name] = f"unit address: {exc}"
        routes, missing = _routes(ips.values(), None, self._override)
        for name, ip in ips.items():
            if _network(ip) in missing:
                self._network_skipped[name] = f"no local address on {_network(ip)}"
        self._routes = [route for route in routes if route[2] or self._unicast]
        self._unit_targets = {name: _unicast_target(ip) for name, ip in ips.items()}

    def _send(self, data, route):
        local, target, broadcast = route
        socket_id = (local if broadcast else target), broadcast
        if self._sockets.get(socket_id, False) is None:
            return False
        try:
            if socket_id not in self._sockets:
                sock = self._sock_factory(socket.AF_INET, socket.SOCK_DGRAM)
                self._sockets[socket_id] = sock
                if broadcast:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    sock.bind((local, 0))
            self._sockets[socket_id].sendto(data, (target, self._port))
        except Exception as exc:
            self._error(exc)
            sock = self._sockets.pop(socket_id, None)
            if sock is not None:
                try:
                    sock.close()
                except Exception as close_error:
                    self._error(close_error)
            # A failed socket is rebuilt at the next route refresh.
            self._sockets[socket_id] = None
            return False
        self._sent += 1
        return True

    def tick(self, now=None):
        with self._lock:
            sent = self._sent
            try:
                if self._enabled:
                    self._tick(self._clock() if now is None else now)
            except Exception as exc:
                self._error(exc)
            return self._sent - sent

    def _tick(self, now):
        view = self._fleet.beacon_view()
        state, gen = view["state"], view["gen"]
        self._skipped = dict(view["skipped"])
        if state is None:
            self._forget()
            return
        entries = view["entries"]
        signature = gen, state, frozenset(view["shows"].items())
        if state == "stopped":
            if not self._announced:
                return
            if self._stop_at is None:
                self._stop_at = now
            elapsed = now - self._stop_at
            if elapsed >= 60:
                self._forget()
                return
            interval = self._tick_s if elapsed < 5 else 1.0
            entries = {}
            for name, entry in self._announced.items():
                offset = view["offsets"].get(name)
                if offset is None:
                    self._skipped[name] = "no offset"
                    continue
                entries[name] = dict(entry, state=3, flags=0, now=view["now"] + offset,
                                     t0=0.0, pos=0.0)
            # The current show list can change while the STOP tail is active.
            signature = None, state, None
        else:
            if self._stop_at is not None:
                self._announced = {}
            self._stop_at = None
            if signature != self._signature:
                self._changed_at = now
            fast = now - self._changed_at < 20 or any(e["pos"] < 0 for e in entries.values())
            interval = self._tick_s if fast else 1.0
        changed = signature != self._signature or interval != self._interval
        self._signature = signature
        self._interval = interval
        if changed:
            self._last_send = -math.inf
        if not entries or now - self._last_send + 1e-9 < interval:
            return
        self._last_send = now
        if now >= self._refresh_at:
            self._refresh(now)
        self._transmit(gen, entries, state)

    def _transmit(self, gen, entries, state):
        if self._key is None:
            self._key = beacon_key(self._token)
        names = iter(entries)
        for chunk in split(entries.values()):
            named = dict(zip(itertools.islice(names, len(chunk)), chunk))
            data = pack(self._fleet.beacon_cid, gen, next(self._sequences), chunk, self._key)
            targets = {self._unit_targets.get(name) for name in named}
            sent_broadcasts = False
            sent_targets = set()
            for local, target, broadcast in self._routes:
                if (broadcast or target in targets) and self._send(data, (local, target, broadcast)):
                    if broadcast:
                        sent_broadcasts = True
                    else:
                        sent_targets.add(target)
            if state != "stopped":
                self._announced.update(
                    (name, entry) for name, entry in named.items()
                    if sent_broadcasts or self._unit_targets.get(name) in sent_targets)
