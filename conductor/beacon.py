"""Broadcast the fleet's run to units whose HTTP replies cannot get back.

The sender reads fleet state and uses measured unit clocks. See
docs/BEACON.md sections 2, 4 and 7 for the wire, cadence and HTTP ordering.
"""

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


# Keep in step with ui/beacon.py; tests/goldens/beacon_v1.json pins the wire.
_HEADER = struct.Struct("!4sBBBBIII")
_ENTRY = struct.Struct("!BBddd")
_OVERHEAD = _HEADER.size + hashlib.sha256().digest_size
_LIMIT = 1200              # Fit below the MTU (docs/BEACON.md §2).
_PORT = 8788               # The units' listening port (docs/BEACON.md §2).
_FAST_TICK_S = 0.2         # Repeat throughout countdown for units that miss a packet (§4).
_FAST_WINDOW_S = 20        # Repeat changes quickly while units catch up (§4).
_STOP_FAST_S = 5           # Repeat STOP quickly when first observed (§4).
_STOP_TAIL_S = 60          # Give units that missed STOP time to hear it (§4).
_ROUTE_REFRESH_S = 10      # Pick up changes to the show's Wi-Fi routes (§4).
_NO_OFFSET = "clock not measured yet - needs one good /status reply first"


def beacon_key(token):
    # Keep in step with ui/beacon.py; tests/goldens/beacon_v1.json pins the wire.
    # An unset token hashes as an empty string (docs/BEACON.md §7).
    return hashlib.sha256(b"epaper-beacon-v1\0" + (token or "").encode("utf-8")).digest()


def _encode(entry):
    host = entry["host"].encode("utf-8")
    try:
        show = entry["show"].encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("show ID must be ASCII") from exc
    if not (1 <= len(host) <= 32 and 1 <= len(show) <= 32):
        raise ValueError("beacon host and show must each occupy 1–32 bytes")
    state, flags = entry["state"], entry["flags"]
    times = entry["now"], entry["t0"], entry["pos"]
    if not all(map(math.isfinite, times)):
        raise ValueError("non-finite beacon time")
    if state not in (1, 2, 3) or flags not in (0, 1):
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
    # The show Wi-Fi uses /24; beacon_to can supply other broadcast addresses (§4).
    return ipaddress.IPv4Network((ip, 24), strict=False)


def _unicast_target(ip):
    return "127.0.0.1" if ip.is_loopback else str(ip)


def _routes(ips, local, override, port=_PORT):
    if local is None:
        try:
            local = set(socket.gethostbyname_ex(socket.gethostname())[2])
        except OSError:
            local = set()
        representatives = {_network(ip): ip for ip in ips if not ip.is_loopback}
        for ip in representatives.values():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                    probe.connect((str(ip), port))
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
    def __init__(self, fleet, token=None, enabled=False, port=_PORT,
                 destinations=None, clock=pc_clock, tick_s=_FAST_TICK_S, sock_factory=None,
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
        self._refreshing = None
        self._forget()

    def _forget(self):
        self._signature = None
        self._changed_at = self._last_send = -math.inf
        self._interval = None
        # Tell STOP only to units told the run, so a stale STOP is never broadcast (§4).
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
        self._refreshing = None

    def set_enabled(self, on):
        with self._lock:
            if self._enabled != on:
                self._enabled = on
                self._forget()
                self._skipped = {}
                self._network_skipped = {}
                self._close_sockets()
            if on:
                self._sent = self._errors = 0
                self._last_error = None

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

    def _resolve_routes(self):
        ips, skipped = {}, {}
        for name, link in self._fleet.links.items():
            try:
                ips[name] = _unit_ip(link.address)
            except (OSError, ValueError) as exc:
                skipped[name] = f"unit address: {exc}"
        routes, missing = _routes(ips.values(), None, self._override, self._port)
        for name, ip in ips.items():
            if _network(ip) in missing:
                skipped[name] = f"this computer is not on {_network(ip)} - join the show Wi-Fi"
        return ([route for route in routes if route[2] or self._unicast],
                {name: _unicast_target(ip) for name, ip in ips.items()}, skipped)

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
            if not self._enabled or self._refreshing is not None:
                return 0
            refresh = None
            try:
                if (self._clock() if now is None else now) >= self._refresh_at:
                    refresh = self._refreshing = object()
            except Exception as exc:
                self._error(exc)
                return 0
        if refresh is not None:
            # Resolve first so DNS cannot age the clock sample in beacon_view().
            try:
                routes = self._resolve_routes()
            except Exception as exc:
                with self._lock:
                    if self._refreshing is refresh:
                        self._refreshing = None
                        self._error(exc)
                return 0
        with self._lock:
            # Disabling the sender invalidates any routes still resolving.
            if not self._enabled or self._refreshing is not refresh:
                return 0
            sent = self._sent
            try:
                now = self._clock() if now is None else now
                if refresh is not None:
                    self._close_sockets()
                    self._routes, self._unit_targets, self._network_skipped = routes
                    self._refresh_at = now + _ROUTE_REFRESH_S
                self._tick(now)
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
            if elapsed >= _STOP_TAIL_S:
                self._forget()
                return
            interval = self._tick_s if elapsed < _STOP_FAST_S else 1.0
            entries = {}
            duplicate_units = view["duplicate_units"]
            duplicate_hosts = set(duplicate_units.values())
            for name, entry in self._announced.items():
                host = duplicate_units.get(name, entry["host"])
                if host in duplicate_hosts:
                    self._skipped[name] = f"two units report the hostname {host}"
                    continue
                offset = view["offsets"].get(name)
                if offset is None:
                    self._skipped[name] = _NO_OFFSET
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
        valid = {}
        for name, entry in entries.items():
            try:
                _encode(entry)
            except ValueError as exc:
                self._skipped[name] = str(exc)
            else:
                valid[name] = entry
        if state != "stopped":
            fast = (now - self._changed_at < _FAST_WINDOW_S
                    or any(e["pos"] < 0 for e in valid.values()))
            interval = self._tick_s if fast else 1.0
        changed = signature != self._signature or interval != self._interval
        self._signature = signature
        self._interval = interval
        if changed:
            self._last_send = -math.inf
        if not valid or now - self._last_send + 1e-9 < interval:
            return
        self._last_send = now
        self._transmit(gen, valid, state)

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
