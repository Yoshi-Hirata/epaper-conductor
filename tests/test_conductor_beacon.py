"""Wire compatibility, fleet projection, and sender timing without UDP I/O."""

import http.client
import json
import struct
import threading
from pathlib import Path

import pytest

from conductor import beacon
from conductor.fleet import Fleet, UnitLink
from conductor.server import make_server


GOLDEN = json.loads((Path(__file__).parent / "goldens" / "beacon_v1.json").read_text())


def test_golden_keys_and_datagrams():
    key = beacon.beacon_key(GOLDEN["token"])
    assert key.hex() == GOLDEN["key_hex"]
    assert beacon.beacon_key(None).hex() == GOLDEN["key_hex_no_token"]
    assert beacon.beacon_key("").hex() == GOLDEN["key_hex_no_token"]
    for datagram in GOLDEN["datagrams"]:
        assert beacon.pack(datagram["cid"], datagram["gen"], datagram["seq"],
                           datagram["entries"], key).hex() == datagram["hex"]


def test_split_at_1200():
    base = GOLDEN["datagrams"][0]["entries"][0]
    full = dict(base, host="é" * 16, show="a" * 32)  # 92 bytes per entry
    # 12 * 92 + 44 + 52 = 1200, with one more entry in the next packet.
    exact = dict(base, host="x" * 15, show="b")
    first, second = beacon.split([full] * 12 + [exact, full])
    assert len(beacon.pack(1, 1, 1, first, beacon.beacon_key(None))) == 1200
    assert second == [full]
    assert beacon.split([]) == []


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def fleet_clock():
    clock = Clock()
    fleet = Fleet({"alias": "127.0.0.2:8787", "other": "127.0.0.3:8787"}, clock=clock)
    fleet.links["alias"].status = {"host": "actual-host"}
    fleet.links["alias"]._samples.append((0.01, 10.0))
    fleet.links["other"].status = {"host": "other-host"}
    fleet.shows = {"alias": {"id": "abc", "duration": 100},
                   "other": {"id": "def", "duration": 200}}
    fleet.run = {"state": "running", "t0": 105.0, "forced": ["alias"]}
    fleet._run_gen = 7
    return fleet, clock


def test_fleet_view(fleet_clock):
    fleet, clock = fleet_clock
    running = fleet.beacon_view()
    assert running["state"] == "running" and running["gen"] == 7
    assert running["entries"] == {
        "alias": dict(host="actual-host", show="abc", state=1, flags=1,
                      now=110.0, t0=115.0, pos=-5.0)}
    assert running["skipped"] == {"other": "no offset"}
    fleet.links["other"]._samples.append((0.01, 20.0))
    fleet.links["other"].status = {}
    assert "host" in fleet.beacon_view()["skipped"]["other"]
    fleet.run.update(state="holding", held_at=111.0, force=True, forced=[])
    clock.now = 120.0
    held = fleet.beacon_view()["entries"]["alias"]
    assert (held["state"], held["pos"], held["flags"], held["now"]) == (2, 6, 1, 130)
    fleet.run.update(state="running", held_at=None)
    clock.now = 236.0
    assert fleet.beacon_view()["skipped"]["alias"] == "show over"
    fleet._staging = {"state": "preset"}
    assert fleet.beacon_view()["state"] is None
    fleet._staging = None
    fleet.run = None
    assert fleet.beacon_view()["state"] is None
    fleet._stopped = True
    stopped = fleet.beacon_view()["entries"]["alias"]
    assert stopped == dict(host="actual-host", show="abc", state=3, flags=0,
                           now=246.0, t0=0.0, pos=0.0)


def test_show_post_serial_is_current_and_limited_to_show_posts(fleet_clock, monkeypatch):
    fleet, clock = fleet_clock
    fleet.shows.pop("other")
    link = fleet.links["alias"]
    link.last_seen = clock.now
    link.status["show"] = {"id": "abc"}
    requests = []

    class Connection:
        status = 200

        def request(self, method, path, body, headers):
            requests.append((method, path, body, headers))

        def getresponse(self):
            return self

        def read(self):
            return b"{}"

        def close(self):
            pass

    monkeypatch.setattr(UnitLink, "_connect", lambda *_: Connection())
    assert 0 < fleet.beacon_cid <= 0xFFFFFFFF
    fleet.run = None
    assert fleet.start_show()["alias"]["ok"]
    start_gen = fleet._run_gen
    assert fleet.hold()["alias"]["ok"]
    hold_gen = fleet._run_gen
    assert hold_gen > start_gen
    assert fleet.stop_show()["alias"]["ok"]
    stop_gen = fleet._run_gen
    fleet._run_gen = (1 << 32) + 23
    link.post("/show/load", {"show": "abc"})
    link.post("/prepare", {})
    link.post("/status", {})
    assert link.poll()
    UnitLink("standalone", "127.0.0.1").post("/show/run", {})

    serial_posts = [("POST", "/show/run", start_gen), ("POST", "/show/hold", hold_gen),
                    ("POST", "/show/stop", stop_gen), ("POST", "/show/load", 23)]
    assert len(requests) == 8
    for (method, path, _, headers), (expected_method, expected_path, gen) in zip(
            requests, serial_posts):
        assert (method, path) == (expected_method, expected_path)
        assert headers["X-Beacon-Cid"] == str(fleet.beacon_cid)
        assert headers["X-Run-Gen"] == str(gen)
    assert json.loads(requests[3][2]) == {"show": "abc"}
    assert [(method, path) for method, path, _, _ in requests[4:]] == [
        ("POST", "/prepare"), ("POST", "/status"), ("GET", "/status"), ("POST", "/show/run")]
    for _, _, _, headers in requests[4:]:
        assert "X-Beacon-Cid" not in headers and "X-Run-Gen" not in headers


def test_beacon_rejoin_force_reads_without_recording(fleet_clock):
    fleet, clock = fleet_clock
    link = fleet.links["alias"]
    link.last_seen = clock.now
    burn = {"state": "failed", "failed": [[1, 2]], "total": 4}
    link.status["show"] = {"id": "abc", "burn": burn}
    fleet.run["forced"] = []
    assert fleet.beacon_view()["entries"]["alias"]["flags"] == 0
    fleet.start_present = True
    assert fleet.beacon_view()["entries"]["alias"]["flags"] == 1
    assert fleet._waved == {} and fleet._failed_said == {}
    fleet.start_present = False
    fleet.run["loops"] = 1
    fleet._waved["alias"] = fleet._failure_signature("alias")
    assert fleet.beacon_view()["entries"]["alias"]["flags"] == 1
    burn["failed"] = [[2, 1]]
    assert fleet.beacon_view()["entries"]["alias"]["flags"] == 0


class Socket:
    def __init__(self, packets, fail=False):
        self.packets = packets
        self.fail = fail

    def setsockopt(self, *_):
        pass

    def bind(self, address):
        self.local = address

    def sendto(self, data, address):
        if self.fail:
            raise OSError("fake send failure")
        self.packets.append((data, address))

    def close(self):
        pass


@pytest.fixture
def sender_setup(fleet_clock, monkeypatch):
    fleet, clock = fleet_clock
    routes = beacon._routes
    monkeypatch.setattr(beacon, "_routes", lambda ips, local, override: routes(ips, [], override))
    packets = []
    sender = beacon.BeaconSender(fleet, clock=clock, sock_factory=lambda *_: Socket(packets))
    return sender, fleet, clock, packets


def tick_at(sender, clock, now):
    clock.now = now
    return sender.tick()


def wire_entry(data):
    i = 20
    names = []
    for _ in range(2):
        length = data[i]
        names.append(data[i + 1:i + 1 + length].decode())
        i += length + 1
    return (*names, *struct.unpack_from("!BBddd", data, i))


def test_off_countdown_and_steady_cadence(sender_setup):
    sender, fleet, clock, packets = sender_setup
    fleet.run["t0"] = 125.0
    assert sender.tick() == 0
    sender.set_enabled(True)
    for step in range(125):  # countdown stays fast past the 20-second change window
        assert tick_at(sender, clock, 100 + step / 5) == 1
    assert tick_at(sender, clock, 125) == 1
    assert tick_at(sender, clock, 125.2) == 0
    assert tick_at(sender, clock, 126) == 1
    fleet._run_gen += 1
    assert tick_at(sender, clock, 126.2) == 1
    assert tick_at(sender, clock, 126.4) == 1
    assert packets[-1][1] == ("127.0.0.1", 8788)


def stop_fleet(fleet):
    fleet.run = None
    fleet._stopped = True
    fleet._run_gen += 1


def test_observed_stop_cadence_and_remembered_entries(sender_setup):
    sender, fleet, clock, packets = sender_setup
    sender.set_enabled(True)
    assert sender.tick() == 1
    stop_fleet(fleet)
    fleet.shows = {}
    fleet.links["alias"]._samples.clear()
    fleet.links["alias"]._samples.append((0.01, 30.0))
    for step in range(300):
        expected = int(step < 25 or step % 5 == 0)
        assert tick_at(sender, clock, 101 + step / 5) == expected
        if expected:
            assert wire_entry(packets[-1][0]) == (
                "actual-host", "abc", 3, 0, clock.now + 30, 0, 0)
    assert tick_at(sender, clock, 161) == 0
    assert tick_at(sender, clock, 200) == 0


def test_enable_while_already_stopped(sender_setup):
    sender, fleet, clock, packets = sender_setup
    stop_fleet(fleet)
    sender.set_enabled(True)
    assert sender.tick() == 0
    assert tick_at(sender, clock, 101) == 0
    assert packets == []


def test_disable_forgets_stop_tail(sender_setup):
    sender, fleet, clock, packets = sender_setup
    sender.set_enabled(True)
    assert sender.tick() == 1
    stop_fleet(fleet)
    assert tick_at(sender, clock, 101) == 1
    sender.set_enabled(False)
    sender.set_enabled(True)
    assert tick_at(sender, clock, 102) == 0
    assert len(packets) == 2


def test_new_start_replaces_stop(sender_setup):
    sender, fleet, clock, packets = sender_setup
    sender.set_enabled(True)
    assert sender.tick() == 1
    stop_fleet(fleet)
    assert tick_at(sender, clock, 101) == 1
    fleet.run = {"state": "running", "t0": 110.0}
    fleet._stopped = False
    fleet._run_gen += 1
    fleet.shows["alias"] = {"id": "new", "duration": 100}
    assert tick_at(sender, clock, 101.1) == 1
    assert wire_entry(packets[-1][0])[:3] == ("actual-host", "new", 1)


def test_raising_socket_is_counted_and_rebuilt(sender_setup):
    _, fleet, clock, packets = sender_setup
    made = []

    def factory(*_):
        made.append(Socket(packets, fail=not made))
        return made[-1]

    sender = beacon.BeaconSender(fleet, enabled=True, clock=clock, sock_factory=factory)
    assert sender.tick() == 0
    assert sender.snapshot()["errors"] == 1
    assert sender.snapshot()["last_error"] == "fake send failure"
    assert tick_at(sender, clock, 100.2) == 0
    assert len(made) == 1
    assert tick_at(sender, clock, 110) == 1
    assert len(made) == 2
    sender.stop()


def test_destination_selection():
    units = ["192.168.1.9:8787", "192.168.2.9", "127.2.3.4", "192.168.1.10"]
    local = ["192.168.1.10", "192.168.1.20", "10.0.0.3", "127.0.0.1"]
    assert beacon.destinations(units, local) == [
        ("192.168.1.10", "192.168.1.255", True),
        ("192.168.1.20", "192.168.1.255", True),
        ("", "127.0.0.1", False), ("", "192.168.1.10", False),
        ("", "192.168.1.9", False), ("", "192.168.2.9", False),
    ]
    override = beacon.destinations(units, local, override=["10.0.0.255"])
    assert override[0] == ("10.0.0.3", "10.0.0.255", True)
    assert override[1:] == beacon.destinations(units, local)[2:]


@pytest.mark.parametrize("unicast", [True, False])
def test_broadcast_and_optional_identical_unicast(fleet_clock, monkeypatch, unicast):
    fleet, clock = fleet_clock
    fleet.links["alias"].address = "192.168.1.9:8787"
    del fleet.links["other"]
    del fleet.shows["other"]
    routes = beacon._routes
    monkeypatch.setattr(beacon, "_routes",
                        lambda ips, local, override: routes(ips, ["192.168.1.4"], override))
    packets = []
    sender = beacon.BeaconSender(fleet, enabled=True, clock=clock, unicast=unicast,
                                 sock_factory=lambda *_: Socket(packets))
    assert sender.tick() == 1 + unicast
    assert packets[0][1] == ("192.168.1.255", 8788)
    if unicast:
        assert packets[1] == (packets[0][0], ("192.168.1.9", 8788))


def test_split_broadcasts_all_chunks_but_unicasts_only_the_matching_chunk(monkeypatch):
    clock = Clock()
    units = {f"unit-{i}": f"192.168.1.{i + 10}" for i in range(30)}
    fleet = Fleet(units, clock=clock)
    fleet.run = {"state": "running", "t0": 105.0}
    fleet._run_gen = 11
    for i, (name, link) in enumerate(fleet.links.items()):
        link.status = {"host": f"host-{i:02d}".ljust(32, "x")}
        link._samples.append((0.01, 10.0))
        fleet.shows[name] = {"id": "a" * 32, "duration": 100}
    routes = beacon._routes
    monkeypatch.setattr(beacon, "_routes",
                        lambda ips, local, override: routes(ips, ["192.168.1.4"], override))
    packets = []
    sender = beacon.BeaconSender(fleet, enabled=True, clock=clock,
                                 sock_factory=lambda *_: Socket(packets))
    assert sender.tick() == 33
    broadcasts = [data for data, address in packets if address[0] == "192.168.1.255"]
    assert len(broadcasts) == 3
    assert [struct.unpack_from("!III", data, 8) for data in broadcasts] == [
        (fleet.beacon_cid, fleet._run_gen, seq) for seq in (1, 2, 3)]
    for name, target in units.items():
        host = fleet.links[name].status["host"].encode("utf-8")
        matching = [data for data in broadcasts if host in data]
        assert len(matching) == 1
        assert [data for data, address in packets if address == (target, 8788)] == matching


def test_server_toggle(tmp_path):
    path = tmp_path / "fleet.json"
    path.write_text('{"beacon_unicast": false}', encoding="utf-8")
    server = make_server(tmp_path, port=0, fleet=Fleet({}))
    sender = beacon.BeaconSender(server.RequestHandlerClass.fleet)
    server.RequestHandlerClass.beacon = sender
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = http.client.HTTPConnection(*server.server_address, timeout=5)

    def request(method, path, body=None):
        conn.request(method, path, body, {"Content-Type": "application/json"})
        response = conn.getresponse()
        return response.status, json.loads(response.read())

    try:
        assert request("GET", "/api/fleet")[1]["beacon"]["enabled"] is False
        for value in (True, False):
            status, body = request("POST", "/api/fleet/beacon", json.dumps({"on": value}))
            assert status == 200 and body["beacon"]["enabled"] is value
            assert sender.snapshot()["enabled"] is value
            assert json.loads(path.read_text()) == {"beacon_unicast": False, "beacon": value}
        assert request("POST", "/api/fleet/beacon", '{"on": 1}')[0] == 400
        assert sender.snapshot()["enabled"] is False
    finally:
        conn.close()
        server.shutdown()
        thread.join()
        server.server_close()
