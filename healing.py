
import threading
import time
from datetime import datetime

import requests


SERVICE_URL = "http://127.0.0.1:5001/health"
REQUEST_TIMEOUT = 1
RETRY_DELAYS = [1, 2, 4, 4]

_state = {
    "status": "checking",
    "message": "Waiting for payroll service health check.",
    "attempts": 0,
    "detected_at": None,
    "recovered_at": None,
    "recovery_time": 0.0,
}

_lock = threading.Lock()
_healing_lock = threading.Lock()


def _timestamp():
    return datetime.now().isoformat(timespec="milliseconds")


def _check_service():
    """Make a real HTTP request to the separate service."""
    response = requests.get(
        SERVICE_URL,
        timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    return response.json().get("status") == "healthy"


def get_network_status():
    """Check the real dependency and return the latest status."""
    try:
        if _check_service():
            with _lock:
                if _state["status"] == "unhealthy":
                    _state["status"] = "healthy"
                    _state["message"] = (
                        "Connection restored and verified."
                    )
                elif _state["status"] not in ("recovering",):
                    _state["status"] = "healthy"
                    _state["message"] = "Payroll service is reachable."
    except requests.RequestException as exc:
        with _lock:
            if _state["status"] == "healthy":
                _state["detected_at"] = _timestamp()
            if _state["status"] != "recovering":
                _state["status"] = "unhealthy"
                _state["message"] = (
                    f"Payroll service connection failed: "
                    f"{type(exc).__name__}"
                )

    with _lock:
        return dict(_state)


def _heal_network():
    started = time.monotonic()

    try:
        for attempt in range(1, len(RETRY_DELAYS) + 2):
            with _lock:
                _state["attempts"] = attempt
                _state["status"] = "recovering"
                _state["message"] = (
                    f"Checking payroll service, attempt {attempt}."
                )

            try:
                if _check_service():
                    with _lock:
                        _state["status"] = "healthy"
                        _state["message"] = (
                            "Connection restored and verified."
                        )
                        _state["recovered_at"] = _timestamp()
                        _state["recovery_time"] = round(
                            time.monotonic() - started, 3
                        )
                    return
            except requests.RequestException as exc:
                with _lock:
                    _state["message"] = (
                        f"Attempt {attempt} failed: "
                        f"{type(exc).__name__}"
                    )

            if attempt <= len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[attempt - 1])

        with _lock:
            _state["status"] = "unhealthy"
            _state["message"] = (
                "Retries exhausted. Payroll service is still unavailable."
            )
            _state["recovery_time"] = round(
                time.monotonic() - started, 3
            )
    finally:
        _healing_lock.release()



def trigger_network_failure():
    if not _healing_lock.acquire(blocking=False):
        return {
            "started": False,
            "message": "A recovery process is already running."
        }

    try:
        response = requests.post(
            "http://127.0.0.1:5001/chaos/outage",
            json={"seconds": 6},
            timeout=2
        )
        response.raise_for_status()

    except requests.RequestException as exc:
        _healing_lock.release()
        return {
            "started": False,
            "message": (
                "Could not inject chaos. Check that payroll_service.py "
                f"is running. Error: {type(exc).__name__}"
            )
        }

    with _lock:
        _state["status"] = "unhealthy"
        _state["message"] = "Chaos injected! Payroll service is unavailable."
        _state["attempts"] = 0
        _state["detected_at"] = _timestamp()
        _state["recovered_at"] = None
        _state["recovery_time"] = 0.0

    threading.Thread(
        target=_heal_network,
        daemon=True,
        name="ResilPay-Network-Healer"
    ).start()

    return {
        "started": True,
        "message": "Chaos injected. Automatic recovery has started."
    }
