"""Persistent queue for safe employee writes during transient injected faults.

Payments are deliberately excluded: uncertain payment outcomes require reconciliation,
not blind replay. The queue file is separate from SQLite so DB outages don't block it.
"""
import json, os, threading, uuid
from datetime import datetime, timezone
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "resilpay_recovery_queue.json"
_LOCK = threading.RLock()
_MAX_ITEMS = 500
_ALLOWED = {("POST", "/add-employee"), ("PUT", "salary")}


def _now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _read():
    if not _PATH.exists(): return []
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError): return []

def _write(items):
    temp = _PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, _PATH)

def eligible(method, path):
    method = method.upper()
    return (method == "POST" and path == "/add-employee") or (method == "PUT" and path.startswith("/employees/") and path.endswith("/salary"))

def enqueue(method, path, payload):
    if not eligible(method, path): return None
    with _LOCK:
        items = _read()
        pending = sum(x.get("status") in ("Queued", "Processing") for x in items)
        if pending >= _MAX_ITEMS: return {"full": True, "queue_id": None}
        item = {"queue_id": str(uuid.uuid4()), "method": method.upper(), "path": path,
                "payload": payload or {}, "status": "Queued", "created_at": _now(),
                "updated_at": _now(), "result": None}
        items.append(item); _write(items)
        return {"full": False, "queue_id": item["queue_id"]}

def process_pending(processor):
    processed = 0
    with _LOCK:
        items = _read()
    for item in items:
        if item.get("status") != "Queued": continue
        with _LOCK:
            current = _read()
            target = next((x for x in current if x.get("queue_id") == item["queue_id"]), None)
            if not target or target.get("status") != "Queued": continue
            target["status"] = "Processing"; target["updated_at"] = _now(); _write(current)
        try:
            result = processor(item["method"], item["path"], item.get("payload") or {})
            status_code, body = result
            final_status = "Completed" if 200 <= status_code < 300 and not body.get("queued") else "Failed"
            result_data = {"http_status": status_code, "message": body.get("message") or body.get("error") or ""}
        except Exception as exc:
            final_status = "Failed"; result_data = {"message": str(exc)}
        with _LOCK:
            current = _read()
            target = next((x for x in current if x.get("queue_id") == item["queue_id"]), None)
            if target:
                target["status"] = final_status; target["updated_at"] = _now(); target["result"] = result_data; _write(current)
        processed += 1
    return processed

def list_items():
    with _LOCK: items = _read()
    return list(reversed(items[-100:]))

def summary():
    items = list_items()
    return {"pending": sum(x["status"] in ("Queued", "Processing") for x in items),
            "queued": sum(x["status"] == "Queued" for x in items),
            "processing": sum(x["status"] == "Processing" for x in items),
            "completed": sum(x["status"] == "Completed" for x in items),
            "failed": sum(x["status"] == "Failed" for x in items), "capacity": _MAX_ITEMS}
