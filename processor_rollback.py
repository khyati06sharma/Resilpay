"""SQLite-backed automatic processor rollback controller for ResilPay."""
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import processor_versions

_DB_PATH = Path(__file__).resolve().parent / "resilpay.db"
_LOCK = threading.RLock()
_STATE = {
    "deployment_status": "stable",
    "active_version": "1.0",
    "health": {"healthy": True, "version": "1.0", "checked_at": None, "detail": "Waiting for first health check."},
    "current_incident_id": None,
    "last_recovery_seconds": None,
    "pending_jobs": 0,
    "last_updated": None,
}
_CONTROLLER_RUNNING = False
_PROCESS_JOB_CALLBACK = None


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _connect():
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_storage():
    conn = _connect()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS processor_incidents (
            id TEXT PRIMARY KEY,
            failure_type TEXT NOT NULL,
            diagnosis TEXT NOT NULL,
            failed_version TEXT NOT NULL,
            restored_version TEXT,
            status TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            recovered_at TEXT,
            recovery_seconds REAL,
            final_health TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS processor_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            version TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(incident_id) REFERENCES processor_incidents(id)
        );
        CREATE TABLE IF NOT EXISTS processor_jobs (
            id TEXT PRIMARY KEY,
            employee_id INTEGER NOT NULL,
            payroll_period TEXT NOT NULL,
            amount REAL NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            payment_id INTEGER,
            message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_processor_jobs_status ON processor_jobs(status);
        """)
        conn.commit()
    finally:
        conn.close()


def _event(incident_id, event_type, message, version=None, status="info"):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO processor_events (incident_id,event_type,message,version,status,created_at) VALUES (?,?,?,?,?,?)",
            (incident_id, event_type, message, version, status, now()),
        )
        conn.commit()
    finally:
        conn.close()


def _set_state(**kwargs):
    with _LOCK:
        _STATE.update(kwargs)
        _STATE["last_updated"] = now()


def configure_job_processor(callback):
    global _PROCESS_JOB_CALLBACK
    _PROCESS_JOB_CALLBACK = callback


def enqueue_job(employee_id, payroll_period, amount):
    """Persist an idempotent pending job; repeated requests return the same job."""
    key = f"resilpay-{payroll_period}-{int(employee_id)}"
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM processor_jobs WHERE idempotency_key=?", (key,)).fetchone()
        if row:
            return dict(row), False
        job_id = str(uuid.uuid4())
        timestamp = now()
        conn.execute(
            """INSERT INTO processor_jobs
               (id,employee_id,payroll_period,amount,idempotency_key,status,message,created_at,updated_at)
               VALUES (?,?,?,?,?,'Pending','Waiting for a healthy payment processor.',?,?)""",
            (job_id, int(employee_id), payroll_period, float(amount), key, timestamp, timestamp),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM processor_jobs WHERE id=?", (job_id,)).fetchone()), True
    finally:
        conn.close()


def pending_jobs_count():
    conn = _connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM processor_jobs WHERE status IN ('Pending','Processing','Needs reconciliation')").fetchone()[0]
    finally:
        conn.close()


def list_jobs(limit=100):
    conn = _connect()
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM processor_jobs ORDER BY created_at DESC LIMIT ?", (limit,))]
    finally:
        conn.close()


def _resume_pending_jobs(incident_id):
    callback = _PROCESS_JOB_CALLBACK
    if callback is None:
        _event(incident_id, "jobs_waiting", "Jobs remain pending because the payroll callback is not configured.", "1.0", "warning")
        return
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM processor_jobs WHERE status='Pending' ORDER BY created_at,id").fetchall()
    finally:
        conn.close()

    for job in rows:
        # Mark Processing transactionally before invoking the callback. The
        # callback checks payments.idempotency_key before creating any record.
        conn = _connect()
        try:
            cur = conn.execute(
                "UPDATE processor_jobs SET status='Processing',updated_at=?,message=? WHERE id=? AND status='Pending'",
                (now(), "Resuming after verified processor rollback.", job["id"]),
            )
            conn.commit()
            if cur.rowcount != 1:
                continue
        finally:
            conn.close()
        try:
            outcome = callback(int(job["employee_id"]), str(job["payroll_period"]), float(job["amount"]))
            status = outcome.get("status", "UNKNOWN")
            if status == "PAID":
                final_status, message = "Completed", "Payment is confirmed in the payments table."
            elif status in ("FAILED", "UNKNOWN", "PROCESSING"):
                # Never auto-retry an uncertain/failed transaction. Keep it
                # visible for reconciliation instead of risking a duplicate.
                final_status, message = "Needs reconciliation", f"Existing transaction status is {status}; no blind retry was attempted."
            else:
                final_status, message = "Failed", f"Processor returned status {status}."
            conn = _connect()
            try:
                conn.execute("UPDATE processor_jobs SET status=?,payment_id=?,message=?,updated_at=? WHERE id=?",
                             (final_status, outcome.get("id"), message, now(), job["id"]))
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            conn = _connect()
            try:
                conn.execute("UPDATE processor_jobs SET status='Needs reconciliation',message=?,updated_at=? WHERE id=?",
                             (f"Outcome could not be confirmed: {exc}", now(), job["id"]))
                conn.commit()
            finally:
                conn.close()
    _event(incident_id, "jobs_resumed", f"Pending payroll jobs checked after rollback; {pending_jobs_count()} remain pending or need reconciliation.", "1.0", "success")


def deploy_faulty_version():
    """Activate v2 and start an independent background health/rollback controller."""
    init_storage()
    with _LOCK:
        if _STATE.get("deployment_status") in ("deploying", "detecting", "diagnosing", "rolling_back", "verifying"):
            return {"started": False, "message": "A processor deployment or rollback is already in progress."}, 409

    incident_id = str(uuid.uuid4())
    detected_at = now()
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO processor_incidents
               (id,failure_type,diagnosis,failed_version,status,detected_at,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (incident_id, "Payment processor regression", "Pending real health-check verification.", "2.0", "Deploying", detected_at, detected_at),
        )
        conn.commit()
    finally:
        conn.close()

    processor_versions.activate_version("2.0")
    processor_versions.set_deployment_status("deploying")
    _set_state(deployment_status="deploying", active_version="2.0", current_incident_id=incident_id,
               last_recovery_seconds=None, health=processor_versions.get_last_health_check())
    _event(incident_id, "deployed", "Faulty processor version 2.0 activated in the running application.", "2.0", "warning")
    worker = threading.Thread(target=_controller, args=(incident_id, detected_at), daemon=True, name="resilpay-processor-rollback")
    worker.start()
    return {"started": True, "incident_id": incident_id, "message": "Faulty processor v2.0 deployed; automatic health checking and rollback started."}, 202


def _controller(incident_id, detected_at):
    global _CONTROLLER_RUNNING
    _CONTROLLER_RUNNING = True
    try:
        processor_versions.set_deployment_status("detecting")
        _set_state(deployment_status="detecting")
        _event(incident_id, "health_check", "Running active processor behavior self-test.", "2.0", "info")
        # Small demo window lets the UI show v2 as active and allows a payroll
        # request to be safely queued while the faulty implementation is active.
        # Short demonstration window: gives the admin time to submit a payroll
        # run while v2 is active so those jobs can be observed as pending.
        time.sleep(4.0)
        check = processor_versions.health_check()
        _set_state(health=check, active_version=check["version"])
        if check["healthy"]:
            processor_versions.set_deployment_status("stable")
            _set_state(deployment_status="stable")
            _event(incident_id, "health_passed", check["detail"], check["version"], "success")
            conn = _connect()
            try:
                conn.execute("UPDATE processor_incidents SET status='Healthy',diagnosis=?,final_health=? WHERE id=?",
                             (check["detail"], "healthy", incident_id))
                conn.commit()
            finally:
                conn.close()
            return

        failure_detected_at = now()
        diagnosis = check["detail"]
        _event(incident_id, "health_failed", diagnosis, "2.0", "failed")
        _event(incident_id, "diagnosed", "Health contract validation failed; automatic rollback selected.", "2.0", "warning")
        conn = _connect()
        try:
            conn.execute("UPDATE processor_incidents SET detected_at=?,diagnosis=?,status='Rolling back' WHERE id=?",
                         (failure_detected_at, diagnosis, incident_id))
            conn.commit()
        finally:
            conn.close()
        processor_versions.set_deployment_status("rolling_back")
        _set_state(deployment_status="rolling_back")
        _event(incident_id, "rollback_started", "Restoring last known-good processor version 1.0 automatically.", "1.0", "warning")
        processor_versions.activate_version("1.0")
        check_after = processor_versions.health_check()
        _set_state(health=check_after, active_version="1.0", deployment_status="verifying")
        _event(incident_id, "rollback_restored", "Version 1.0 is active; verifying processor behavior.", "1.0", "info")
        _event(incident_id, "health_check", check_after["detail"], "1.0", "info")

        elapsed = round(max(0.0, (datetime_from_iso(check_after["checked_at"]) - datetime_from_iso(failure_detected_at)).total_seconds()), 3)
        if check_after["healthy"]:
            recovered_at = now()
            processor_versions.set_deployment_status("stable")
            _set_state(deployment_status="stable", last_recovery_seconds=elapsed)
            conn = _connect()
            try:
                conn.execute(
                    """UPDATE processor_incidents SET restored_version='1.0',status='Recovered',
                       recovered_at=?,recovery_seconds=?,final_health=? WHERE id=?""",
                    (recovered_at, elapsed, "healthy", incident_id),
                )
                conn.commit()
            finally:
                conn.close()
            _event(incident_id, "health_passed", "Version 1.0 passed post-rollback health verification.", "1.0", "success")
            _event(incident_id, "recovered", "Incident marked recovered after verified rollback.", "1.0", "success")
            _resume_pending_jobs(incident_id)
        else:
            processor_versions.set_deployment_status("failed")
            _set_state(deployment_status="failed", health=check_after)
            conn = _connect()
            try:
                conn.execute("UPDATE processor_incidents SET restored_version='1.0',status='Unrecovered',recovered_at=?,recovery_seconds=?,final_health=? WHERE id=?",
                             (now(), elapsed, "unhealthy", incident_id))
                conn.commit()
            finally:
                conn.close()
            _event(incident_id, "rollback_failed", "Version 1.0 failed verification; payroll jobs remain pending.", "1.0", "failed")
    except Exception as exc:
        processor_versions.set_deployment_status("failed")
        _set_state(deployment_status="failed")
        try:
            _event(incident_id, "controller_error", f"Rollback controller error: {exc}", None, "failed")
            conn = _connect()
            try:
                conn.execute("UPDATE processor_incidents SET status='Unrecovered',diagnosis=? WHERE id=?",
                             (f"Rollback controller error: {exc}", incident_id))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass
    finally:
        _CONTROLLER_RUNNING = False


def datetime_from_iso(value):
    return datetime.fromisoformat(value)


def get_status():
    init_storage()
    with _LOCK:
        state = dict(_STATE)
    state["active_version"] = processor_versions.get_active_version()
    state["deployment_status"] = processor_versions.get_deployment_status()
    state["health"] = processor_versions.get_last_health_check()
    state["pending_jobs"] = pending_jobs_count()
    state["jobs"] = list_jobs(50)
    conn = _connect()
    try:
        incident = conn.execute("SELECT * FROM processor_incidents ORDER BY detected_at DESC LIMIT 1").fetchone()
        state["latest_incident"] = dict(incident) if incident else None
        state["timeline"] = [dict(row) for row in conn.execute(
            "SELECT * FROM processor_events WHERE incident_id=? ORDER BY id",
            (incident["id"],),
        )] if incident else []
    finally:
        conn.close()
    state["last_updated"] = now()
    return state


def get_incidents(limit=30):
    init_storage()
    conn = _connect()
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM processor_incidents ORDER BY detected_at DESC LIMIT ?", (limit,))]
    finally:
        conn.close()


def get_events(limit=100):
    init_storage()
    conn = _connect()
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM processor_events ORDER BY id DESC LIMIT ?", (limit,))]
    finally:
        conn.close()


def get_payments(limit=200):
    init_storage()
    conn = _connect()
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM payments ORDER BY id DESC LIMIT ?", (limit,))]
    finally:
        conn.close()
