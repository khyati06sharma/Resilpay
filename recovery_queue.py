

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "resilpay_recovery_queue.json"
_LOCK = threading.RLock()
_MAX_ITEMS = 500


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read():
    if not _PATH.exists():
        return []
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write(items):
    """Persist atomically so a partial write does not corrupt the queue."""
    temp = _PATH.with_suffix(".tmp")
    temp.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, _PATH)


def eligible(method, path):
    """Only allow employee mutations to be replayed after DB recovery.

    Payments are intentionally excluded. An interrupted payment must be
    reconciled, not blindly retried.
    """
    import re

    method = (method or "").upper()
    path = (path or "").split("?", 1)[0]
    if path.startswith("/api/"):
        path = path[4:]
    path = path.rstrip("/") or "/"

    if method == "POST" and path == "/add-employee":
        return True
    if method == "PUT" and re.fullmatch(r"/employees/[1-9]\d*/salary", path):
        return True
    if method == "PUT" and re.fullmatch(r"/employees/[1-9]\d*", path):
        return True
    if method == "DELETE" and re.fullmatch(r"/employees/[1-9]\d*", path):
        return True
    return False


def enqueue(method, path, payload):
    method = (method or "").upper()
    path = (path or "").split("?", 1)[0]
    if path.startswith("/api/"):
        path = path[4:]

    if not eligible(method, path):
        return None

    with _LOCK:
        items = _read()
        pending = sum(
            item.get("status") in ("Queued", "Processing")
            for item in items
        )
        if pending >= _MAX_ITEMS:
            return {"full": True, "queue_id": None}

        item = {
            "queue_id": str(uuid.uuid4()),
            "method": method,
            "path": path,
            "payload": payload or {},
            "status": "Queued",
            "created_at": _now(),
            "updated_at": _now(),
            "result": {
                "message": (
                    "Accepted into the durable queue; "
                    "not saved to SQLite yet."
                )
            },
        }
        items.append(item)
        _write(items)
        return {"full": False, "queue_id": item["queue_id"]}


def process_pending(processor):
    """Replay queued employee operations after the caller verifies recovery."""
    processed = 0

    with _LOCK:
        snapshot = list(_read())

    for item in snapshot:
        if item.get("status") != "Queued":
            continue

        # Recheck eligibility before replay in case the queue file was edited.
        if not eligible(item.get("method"), item.get("path")):
            with _LOCK:
                items = _read()
                target = next(
                    (
                        x for x in items
                        if x.get("queue_id") == item.get("queue_id")
                    ),
                    None,
                )
                if target:
                    target["status"] = "Failed"
                    target["updated_at"] = _now()
                    target["result"] = {
                        "message": "Operation is not eligible for replay."
                    }
                    _write(items)
            continue

        with _LOCK:
            items = _read()
            target = next(
                (
                    x for x in items
                    if x.get("queue_id") == item.get("queue_id")
                ),
                None,
            )
            if not target or target.get("status") != "Queued":
                continue

            target["status"] = "Processing"
            target["updated_at"] = _now()
            _write(items)

        try:
            status_code, body = processor(
                item["method"],
                item["path"],
                item.get("payload") or {},
            )
            body = body if isinstance(body, dict) else {}

            if 200 <= status_code < 300 and not body.get("queued"):
                final_status = "Completed"
                message = body.get(
                    "message",
                    "Operation saved successfully.",
                )
            elif body.get("queued"):
                final_status = "Queued"
                message = "Still waiting for service recovery."
            else:
                final_status = "Failed"
                message = (
                    body.get("message")
                    or body.get("error")
                    or f"Operation returned HTTP {status_code}."
                )

            result = {
                "http_status": status_code,
                "message": message,
            }

        except Exception as exc:
            final_status = "Failed"
            result = {"message": str(exc)}

        with _LOCK:
            items = _read()
            target = next(
                (
                    x for x in items
                    if x.get("queue_id") == item.get("queue_id")
                ),
                None,
            )
            if target:
                target["status"] = final_status
                target["updated_at"] = _now()
                target["result"] = result
                _write(items)

        processed += 1

    return processed


def list_items():
    with _LOCK:
        items = _read()
    return list(reversed(items[-200:]))


def summary():
    items = list_items()
    return {
        "pending": sum(
            item.get("status") in ("Queued", "Processing")
            for item in items
        ),
        "queued": sum(item.get("status") == "Queued" for item in items),
        "processing": sum(
            item.get("status") == "Processing" for item in items
        ),
        "completed": sum(
            item.get("status") == "Completed" for item in items
        ),
        "failed": sum(item.get("status") == "Failed" for item in items),
        "capacity": _MAX_ITEMS,
    }
