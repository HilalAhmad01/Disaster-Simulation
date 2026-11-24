"""Pairing a pilot tablet, sessions, and the control lease. Pure Python, no web framework.

  pairing : the commander asks for a 6-digit code (single use, 2 minutes); the tablet enters it and gets a revocable session token.
  roles   : commander (the dashboard: implicit, no token), pilot (may hold a lease), observer (watch only).
  lease   : at most one holder per drone; renewed by every control frame; expires after `lease_ttl_s` of silence. A holder that drops off
            keeps its lease for `lease_grace_s` so a Wi-Fi blip does not lose control. The commander can always take it.

SIMPLIFICATION: the dashboard API has no authentication, like the rest of the app; pairing only gates the pilot panel, on a local network.
"""
import secrets, time

COMMANDER = "commander"


class PairError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class Sessions:
    def __init__(self, cfg, now=time.time):
        self.cfg, self.now = cfg, now
        self.codes: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}              # token -> session
        self.leases: dict[str, dict] = {}                # drone id -> {holder, expires, grace_until}
        self._fail: dict[str, list[float]] = {}          # client -> failed claim times

    # ------------------------------------------------------------------ pairing
    def new_code(self, role: str = "pilot") -> dict:
        if role not in ("pilot", "observer"):
            raise PairError("bad_role", "Role must be pilot or observer.")
        self._expire()
        code = f"{secrets.randbelow(10**6):06d}"
        while code in self.codes:
            code = f"{secrets.randbelow(10**6):06d}"
        self.codes[code] = {"role": role, "expires": self.now() + self.cfg.pair_code_ttl_s}
        return {"code": code, "role": role, "expires_in": self.cfg.pair_code_ttl_s}

    def claim(self, code: str, name: str = "tablet", ua: str = "", client: str = "?") -> dict:
        self._expire()
        recent = [t for t in self._fail.get(client, []) if self.now() - t < 60]
        if len(recent) >= 8:
            raise PairError("rate_limited", "Too many wrong codes. Wait a minute.")
        c = self.codes.pop(str(code).strip(), None)
        if c is None:
            self._fail[client] = recent + [self.now()]
            raise PairError("bad_code", "That code is wrong or expired.")
        token = secrets.token_urlsafe(24)
        s = {"id": secrets.token_hex(4), "token": token, "role": c["role"], "name": (name or "tablet")[:40], "ua": ua[:120], "created": self.now(),
             "last_seen": self.now(), "connected": False}
        self.sessions[token] = s
        return s

    def _expire(self):
        t = self.now()
        self.codes = {k: v for k, v in self.codes.items() if v["expires"] > t}

    def authenticate(self, token: str | None) -> dict | None:
        s = self.sessions.get(token or "")
        if s:
            s["last_seen"] = self.now()
        return s

    def public(self, s: dict) -> dict:
        return {"id": s["id"], "role": s["role"], "name": s["name"], "connected": s["connected"], "age_s": round(self.now() - s["created"]),
                "seen_s": round(self.now() - s["last_seen"])}

    def list(self) -> list[dict]:
        return [self.public(s) for s in self.sessions.values()]

    def revoke(self, sid: str) -> bool:
        for tok, s in list(self.sessions.items()):
            if s["id"] == sid:
                del self.sessions[tok]
                for d, l in list(self.leases.items()):
                    if l["holder"] == tok:
                        del self.leases[d]
                return True
        return False

    def connected(self, token: str, on: bool):
        s = self.sessions.get(token)
        if not s:
            return
        s["connected"] = on
        t = self.now()
        for did in [d for d, l in self.leases.items() if l["holder"] == token]:
            l = self.leases[did]
            if on:
                if self.holder(did) == token:                  # came back inside the grace period: the lease is alive again
                    l["expires"], l["grace_until"] = t + self.cfg.lease_ttl_s, None
            else:
                l["grace_until"] = t + self.cfg.lease_grace_s

    # ------------------------------------------------------------------ lease
    def holder(self, did: str) -> str | None:
        """Token (or COMMANDER) holding the lease now, or None. A holder whose heartbeat stopped loses it after the TTL, or after the
        grace period when its connection dropped."""
        l = self.leases.get(did)
        if not l:
            return None
        t = self.now()
        alive = t < l["expires"] or (l.get("grace_until") is not None and t < l["grace_until"])
        if not alive:
            del self.leases[did]
            return None
        return l["holder"]

    def acquire(self, did: str, who: str, force: bool = False) -> bool:
        """who: a session token or COMMANDER. Observers cannot hold a lease."""
        if who != COMMANDER:
            s = self.sessions.get(who)
            if s is None or s["role"] != "pilot":
                return False
        cur = self.holder(did)
        if cur not in (None, who) and not (force or who == COMMANDER):
            return False
        self.leases[did] = {"holder": who, "expires": self.now() + self.cfg.lease_ttl_s, "grace_until": None}
        return True

    def renew(self, did: str, who: str) -> bool:
        if self.holder(did) != who:
            return False
        l = self.leases[did]
        l["expires"] = self.now() + self.cfg.lease_ttl_s
        l["grace_until"] = None
        return True

    def release(self, did: str, who: str) -> bool:
        if self.holder(did) == who:
            del self.leases[did]
            return True
        return False

    def clear_drone(self, did: str):
        self.leases.pop(did, None)

    def lease_info(self, did: str) -> dict:
        h = self.holder(did)
        if h is None:
            return {"drone": did, "holder": None, "name": None}
        if h == COMMANDER:
            return {"drone": did, "holder": COMMANDER, "name": "Commander"}
        s = self.sessions.get(h)
        return {"drone": did, "holder": s["id"] if s else None, "name": s["name"] if s else None}
