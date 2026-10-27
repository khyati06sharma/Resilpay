
from collections import OrderedDict
from threading import RLock
from time import monotonic
from copy import deepcopy

_LOCK = RLock()
_ITEMS = OrderedDict()
_MAX_ITEMS = 64
_DEFAULT_TTL = 300


def set_value(key, value, ttl=_DEFAULT_TTL):
    now = monotonic()
    with _LOCK:
        _ITEMS[key] = {"value": deepcopy(value), "stored_at": now, "expires_at": now + max(1, ttl)}
        _ITEMS.move_to_end(key)
        while len(_ITEMS) > _MAX_ITEMS:
            _ITEMS.popitem(last=False)


def get_value(key, allow_expired=True):
    now = monotonic()
    with _LOCK:
        item = _ITEMS.get(key)
        if not item:
            return None
        if not allow_expired and now > item["expires_at"]:
            return None
        _ITEMS.move_to_end(key)
        result = deepcopy(item["value"])
        result["_cache"] = {"cached": True, "stale": now > item["expires_at"], "age_seconds": round(now - item["stored_at"], 1)}
        return result


def status():
    with _LOCK:
        return {"items": len(_ITEMS), "max_items": _MAX_ITEMS, "ttl_seconds": _DEFAULT_TTL, "scope": "read-only last-known-good snapshots"}
