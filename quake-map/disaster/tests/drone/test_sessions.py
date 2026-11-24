"""Pairing codes, sessions and the control lease."""
import pytest
from drone.config import Config
from drone.sessions import Sessions, PairError, COMMANDER


class T:
    """A controllable clock."""
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


@pytest.fixture()
def s():
    clock = T()
    ss = Sessions(Config(), now=clock)
    ss.clock = clock
    return ss


def pair(ss, role="pilot", name="tab"):
    return ss.claim(ss.new_code(role)["code"], name)


def test_code_is_six_digits_single_use_and_expires(s):
    c = s.new_code()
    assert len(c["code"]) == 6 and c["code"].isdigit() and c["expires_in"] == 120
    sess = s.claim(c["code"], "Nexus")
    assert sess["role"] == "pilot" and s.authenticate(sess["token"]) is sess
    with pytest.raises(PairError) as e:
        s.claim(c["code"])
    assert e.value.code == "bad_code"
    c2 = s.new_code()
    s.clock.t += 121
    with pytest.raises(PairError):
        s.claim(c2["code"])

def test_wrong_codes_are_rate_limited(s):
    for _ in range(8):
        with pytest.raises(PairError):
            s.claim("000000", client="1.2.3.4")
    good = s.new_code()
    with pytest.raises(PairError) as e:
        s.claim(good["code"], client="1.2.3.4")
    assert e.value.code == "rate_limited"
    assert s.claim(good["code"], client="5.6.7.8")                      # another device is unaffected

def test_bad_role_and_unknown_token(s):
    with pytest.raises(PairError):
        s.new_code("admin")
    assert s.authenticate("nope") is None and s.authenticate(None) is None

def test_revoke_removes_session_and_its_lease(s):
    a = pair(s)
    assert s.acquire("d1", a["token"])
    assert s.revoke(a["id"]) and s.authenticate(a["token"]) is None and s.holder("d1") is None
    assert not s.revoke("zzz")

def test_only_one_pilot_holds_the_lease(s):
    a, b = pair(s, name="a"), pair(s, name="b")
    assert s.acquire("d1", a["token"])
    assert not s.acquire("d1", b["token"])
    assert s.acquire("d1", a["token"])                                   # the holder can re-acquire
    assert s.lease_info("d1")["name"] == "a"

def test_observers_cannot_fly(s):
    o = pair(s, "observer")
    assert not s.acquire("d1", o["token"]) and s.holder("d1") is None

def test_lease_expires_without_heartbeat_and_renew_extends(s):
    a, b = pair(s), pair(s)
    s.acquire("d1", a["token"])
    s.clock.t += 1.0
    assert s.renew("d1", a["token"]) and s.holder("d1") == a["token"]
    s.clock.t += 1.4
    assert s.holder("d1") == a["token"]
    s.clock.t += 0.2
    assert s.holder("d1") is None and not s.renew("d1", a["token"])
    assert s.acquire("d1", b["token"])

def test_disconnect_keeps_the_lease_for_the_grace_period(s):
    a, b = pair(s), pair(s)
    s.acquire("d1", a["token"]); s.connected(a["token"], True)
    s.connected(a["token"], False)
    s.clock.t += 4.0
    assert s.holder("d1") == a["token"] and not s.acquire("d1", b["token"])
    s.clock.t += 1.5
    assert s.holder("d1") is None
    s.acquire("d1", a["token"]); s.connected(a["token"], False)
    s.clock.t += 3.0
    s.connected(a["token"], True)                                         # came back inside the grace period
    s.clock.t += 0.5
    assert s.renew("d1", a["token"])

def test_commander_can_always_take_and_release(s):
    a = pair(s)
    s.acquire("d1", a["token"])
    assert s.acquire("d1", COMMANDER)
    assert s.holder("d1") == COMMANDER and not s.renew("d1", a["token"])
    assert s.release("d1", COMMANDER) and s.holder("d1") is None

def test_list_shows_connection_state(s):
    a = pair(s, name="tablet 1")
    s.connected(a["token"], True)
    row = s.list()[0]
    assert row["name"] == "tablet 1" and row["connected"] and "token" not in row
