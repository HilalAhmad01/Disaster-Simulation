"""Neighbourhood names for priority areas via OpenStreetMap's public Nominatim service (light, cached use only)."""
import json, time, threading, urllib.request, urllib.parse

UA = "quake-damage-map/1.0 (local research project)"
_cache, _lock, _last = {}, threading.Lock(), [0.0]
_failed = {}                      # key -> time of a failed lookup; retried only after FAIL_TTL (keeps reports fast when offline)
_down = [0.0]                     # time the service last failed to answer at all
FAIL_TTL = 600.0

def pick_name(addr):
    local = next((addr[k] for k in ("neighbourhood", "suburb", "quarter", "residential", "village", "hamlet",
                                    "city_district", "road") if addr.get(k)), "")
    town = next((addr[k] for k in ("city", "town", "county", "state_district") if addr.get(k)), "")
    if local and town and local != town:
        return f"{local}, {town}"
    return local or town

def reverse_name(lat, lon):
    """Return a short place name, or '' if the service is unreachable. Max one request per 1.1 s, results cached."""
    key = (round(lat, 3), round(lon, 3))
    if key in _cache:
        return _cache[key]
    now = time.time()
    if now - _failed.get(key, -FAIL_TTL) < FAIL_TTL or now - _down[0] < 60.0:
        return ""
    with _lock:
        if key in _cache:                       # another thread looked it up while this one waited
            return _cache[key]
        pause = 1.1 - (time.time() - _last[0])
        if pause > 0:
            time.sleep(pause)
        name = ""
        try:
            q = urllib.parse.urlencode({"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 16, "addressdetails": 1})
            req = urllib.request.Request("https://nominatim.openstreetmap.org/reverse?" + q, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=6) as r:
                name = pick_name(json.load(r).get("address", {}))
        except OSError:                         # unreachable or timed out: do not try again for a minute
            _down[0] = time.time()
        except Exception:
            pass
        _last[0] = time.time()
    if name:
        _cache[key] = name
    else:
        _failed[key] = time.time()
    return name
