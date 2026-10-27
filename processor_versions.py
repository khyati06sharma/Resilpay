
import threading
from datetime import datetime, timezone

_LOCK = threading.RLock()
_ACTIVE_VERSION = "1.0"
_DEPLOYMENT_STATUS = "stable"
_LAST_CHECK = {"healthy": True, "version": "1.0", "checked_at": None, "detail": "Version 1.0 self-test passed."}


def utc_now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def get_active_version():
    with _LOCK:
        return _ACTIVE_VERSION


def get_deployment_status():
    with _LOCK:
        return _DEPLOYMENT_STATUS


def activate_version(version):
    """Switch the in-process implementation; no Flask restart is needed."""
    global _ACTIVE_VERSION, _DEPLOYMENT_STATUS
    if version not in ("1.0", "2.0"):
        raise ValueError("Unsupported processor version.")
    with _LOCK:
        _ACTIVE_VERSION = version
        _DEPLOYMENT_STATUS = "deploying" if version == "2.0" else "rollback"
        _LAST_CHECK = {
            "healthy": None,
            "version": version,
            "checked_at": None,
            "detail": f"Version {version} activated; awaiting a behavioral health check.",
        }


def set_deployment_status(status):
    global _DEPLOYMENT_STATUS
    with _LOCK:
        _DEPLOYMENT_STATUS = status


def health_check():
    """Exercise the active implementation's real self-test, not just its label."""
    global _LAST_CHECK
    with _LOCK:
        version = _ACTIVE_VERSION

    if version == "2.0":
        # Deliberate defect: v2 returns a malformed settlement result from its
        # self-test. The health checker validates the result contract and fails.
        result = {"status": "ok", "settlement_total": "NOT_A_NUMBER"}
        try:
            float(result["settlement_total"])
            healthy, detail = True, "Version 2.0 settlement self-test passed."
        except (TypeError, ValueError):
            healthy, detail = False, "Version 2.0 defect: settlement_total is not numeric."
    else:
        result = {"status": "ok", "settlement_total": 1.0}
        try:
            healthy = result.get("status") == "ok" and float(result["settlement_total"]) > 0
            detail = "Version 1.0 processor self-test passed." if healthy else "Version 1.0 self-test failed."
        except (TypeError, ValueError):
            healthy, detail = False, "Version 1.0 self-test returned an invalid settlement result."

    check = {"healthy": bool(healthy), "version": version, "checked_at": utc_now(), "detail": detail}
    with _LOCK:
        _LAST_CHECK = check
    return dict(check)


def get_last_health_check():
    with _LOCK:
        return dict(_LAST_CHECK)


def process_payment(amount):
    """Run a real processor contract check before returning a simulated result."""
    check = health_check()
    if not check["healthy"]:
        raise RuntimeError(check["detail"])
    amount = float(amount)
    if amount <= 0:
        raise ValueError("Payment amount must be positive.")
    return {"approved": True, "processor_version": check["version"], "reference": f"SIM-{utc_now()}"}
