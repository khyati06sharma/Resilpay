from datetime import datetime
import sqlite3
import threading
import time
import uuid
from flask import Flask, request, jsonify, render_template
import processor_versions
import processor_rollback

from cache import (
    get_value as cache_get,
    set_value as cache_set,
    status as cache_status,
)

from recovery_queue import (
    enqueue as queue_enqueue,
    list_items as queue_items,
    summary as queue_summary,
    process_pending as queue_process_pending,
)

from database import (
    get_db,
    init_db,
    activate_database_fault,
    deactivate_database_fault,
    get_database_fault_status,
    wait_for_database_fault_observed,
)

app = Flask(__name__)
init_db()

_state_lock = threading.Lock()
_chaos = {"api": False, "payment": False}

_network_fault_lock = threading.Lock()
_network_fault_active = False
_network_fault_started = None
_network_fault_duration = None

_network_fault_observed = threading.Event()
_api_fault_observed = threading.Event()
_payment_fault_observed = threading.Event()

_recovery_status_lock = threading.Lock()
_recovery_status = {}

_RECOVERY_STAGES = (
    ("detecting", "Detecting error"),
    ("analyzing", "Analyzing issue"),
    ("working", "Working on recovery"),
    ("verifying", "Verifying health"),
    ("recovered", "Recovered"),
)

RECOVERY_BACKOFF_SECONDS = (1, 1, 1)
RECOVERY_DEMO_TARGET_SECONDS = 20

# Operations allowed to wait in the database recovery queue.
# The recovery_queue.py eligible() function must allow these same routes.
QUEUEABLE_WRITES = {
    ("POST", "/add-employee"),
}

QUEUEABLE_PATH_PATTERNS = (
    ("PUT", r"/employees/[1-9]\d*/salary"),
    ("PUT", r"/employees/[1-9]\d*"),
    ("DELETE", r"/employees/[1-9]\d*"),
)

def now():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def normalize_path(path):
    path = (path or "").split("?", 1)[0]
    if path.startswith("/api/"):
        path = path[4:]
    return path.rstrip("/") or "/"


def is_queueable_write(method, path):
    """Match only explicitly supported employee and payment operations."""
    import re

    method = (method or "").upper()
    path = normalize_path(path)

    if (method, path) in QUEUEABLE_WRITES:
        return True

    return any(
        method == allowed_method and re.fullmatch(pattern, path)
        for allowed_method, pattern in QUEUEABLE_PATH_PATTERNS
    )


def set_recovery_stage(
    kind, stage, message, incident_id=None, started_at=None
):
    stage_index = next(
        (
            i for i, item in enumerate(_RECOVERY_STAGES)
            if item[0] == stage
        ),
        0,
    )

    with _recovery_status_lock:
        old = _recovery_status.get(kind, {})

        _recovery_status[kind] = {
            "kind": kind,
            "stage": stage,
            "stage_label": _RECOVERY_STAGES[stage_index][1],
            "stage_index": stage_index,
            "message": message,
            "incident_id": (
                incident_id
                if incident_id is not None
                else old.get("incident_id")
            ),
            "started_at": (
                started_at
                if started_at is not None
                else old.get("started_at")
            ),
            "updated_at": now(),
            "active": stage not in ("recovered", "failed"),
            "finished_at": (
                now() if stage in ("recovered", "failed") else None
            ),
            "target_duration_seconds": RECOVERY_DEMO_TARGET_SECONDS,
            "minimum_demo_window_seconds": 15,
            "maximum_demo_window_seconds": 30,
            "history": (
                old.get("history", [])
                + [{
                    "stage": stage,
                    "label": _RECOVERY_STAGES[stage_index][1],
                    "at": now(),
                    "message": message,
                }]
            )[-12:],
        }


def set_recovery_failure(kind, message):
    with _recovery_status_lock:
        old = _recovery_status.get(kind, {})

        _recovery_status[kind] = {
            **old,
            "kind": kind,
            "stage": "failed",
            "stage_label": "Recovery needs attention",
            "message": message,
            "active": False,
            "finished_at": now(),
            "updated_at": now(),
            "history": (
                old.get("history", [])
                + [{
                    "stage": "failed",
                    "label": "Recovery needs attention",
                    "at": now(),
                    "message": message,
                }]
            )[-12:],
        }


def wait_for_minimum_demo_window(started_at, minimum_seconds=15):
    """Presentation pacing only; this never declares recovery successful."""
    try:
        start = datetime.fromisoformat(started_at)
        elapsed = (
            datetime.now().astimezone() - start
        ).total_seconds()
    except (TypeError, ValueError):
        elapsed = 0

    remaining = max(0.0, minimum_seconds - elapsed)
    if remaining:
        threading.Event().wait(remaining)


def recovery_backoff_probe(probe, label):
    """Collect bounded diagnostic probe results before corrective action."""
    attempts = []
    kind = label.split()[0].lower()

    if kind not in ("network", "api", "payment", "database"):
        kind = label.lower()

    set_recovery_stage(
        kind,
        "analyzing",
        f"Checking {label} availability and collecting diagnostic evidence.",
    )

    for attempt, delay in enumerate(RECOVERY_BACKOFF_SECONDS, start=1):
        if attempt >= 3:
            set_recovery_stage(
                kind,
                "working",
                f"Applying recovery strategy; diagnostic retry "
                f"{attempt} of {len(RECOVERY_BACKOFF_SECONDS)}.",
            )

        threading.Event().wait(delay)

        try:
            healthy, detail = probe()
        except Exception as exc:
            healthy, detail = False, str(exc)

        attempts.append({
            "attempt": attempt,
            "backoff_seconds": delay,
            "healthy": bool(healthy),
            "detail": str(detail),
        })

        if healthy:
            break

    return attempts


def database_fault_active():
    """Read the injected SQLite fault state without opening a DB connection."""
    try:
        state = get_database_fault_status()
        return (
            bool(state.get("active", False))
            if isinstance(state, dict)
            else False
        )
    except Exception:
        app.logger.exception("Could not read database fault status")
        # Fail closed: do not allow writes if the fault state is unknown.
        return True


def _queue_response(method, path, payload, fault_name):
    """Queue an allowlisted operation without invoking its route handler."""
    if not is_queueable_write(method, path):
        return jsonify({
            "success": False,
            "queued": False,
            "error": f"{fault_name.upper()}_FAULT",
            "message": (
                f"{fault_name.capitalize()} failure is active. This operation "
                "is not eligible for automatic replay and was not performed."
            ),
        }), 503

    queued = queue_enqueue(method, normalize_path(path), payload)

    if queued and not queued.get("full"):
        return jsonify({
            "success": True,
            "queued": True,
            "queue_id": queued["queue_id"],
            "status": "Queued",
            "message": (
                f"{fault_name.capitalize()} failure is active. The operation "
                "is waiting for verified recovery; nothing has been saved yet."
            ),
        }), 202

    if queued and queued.get("full"):
        return jsonify({
            "success": False,
            "queued": False,
            "error": "RECOVERY_QUEUE_FULL",
            "message": "The recovery queue is full. No operation was accepted.",
        }), 429

    return jsonify({
        "success": False,
        "queued": False,
        "error": "OPERATION_NOT_QUEUEABLE",
        "message": (
            "This operation is not eligible for automatic replay and "
            "was not performed."
        ),
    }), 503


@app.before_request
def inject_real_network_error():
    """
    Enforce simulated outages in the backend before any write route executes.

    During a database fault, supported writes are durably queued.
    During a network fault, writes are blocked until recovery is verified.
    Monitoring and chaos-control endpoints remain available.
    """
    normalized_path = normalize_path(request.path)

    always_available = {
        "/",
        "/health",
        "/api-status",
        "/chaos/api",
        "/chaos/network",
        "/chaos/deploy-faulty",
        "/network-status",
        "/database-status",
        "/recovery-status",
        "/recovery-queue",
        "/cache-status",
    }

    if (
        normalized_path in always_available
        or request.method == "OPTIONS"
        or request.method == "GET"
        or request.method == "HEAD"
    ):
        return None

    is_write = request.method in ("POST", "PUT", "PATCH", "DELETE")
    if not is_write:
        return None

    payload = request.get_json(silent=True) or {}

    # Highest priority: the database fault blocks every route mutation.
    # Supported operations wait in the queue; unsupported operations fail.
    # Payments must NEVER be queued or replayed during a database outage.
    is_payment_request = (
        normalized_path == "/payments"
        or normalized_path.startswith("/payments/")
    )

    if database_fault_active():
        if is_payment_request:
            return jsonify({
                "success": False,
                "queued": False,
                "error": "DATABASE_UNAVAILABLE",
                "message": (
                    "Database is unavailable. Payment was not processed. "
                    "Verify payment status before attempting again."
                ),
                "recovery": "Automatic database recovery is in progress."
            }), 503, {"Retry-After": "2"}

        # Only eligible employee operations may be queued.
        return _queue_response(
            request.method, normalized_path, payload, "database"
        )
    with _network_fault_lock:
        network_active = _network_fault_active

    # Network outage: reject writes. The UI should disable its buttons too,
    # but this backend guard protects direct requests as well.
    if network_active:
        _network_fault_observed.set()
        return jsonify({
            "success": False,
            "queued": False,
            "error": "NETWORK_FAULT",
            "message": (
                "Network failure is active. Payroll operations are disabled "
                "until the connection has recovered and passed verification."
            ),
            "recovery": "Automatic recovery is in progress.",
        }), 503, {"Retry-After": "2"}


    with _state_lock:
        api_active = bool(_chaos.get("api", False))

    # Block employee mutations during an API outage.
    api_mutation = (
        normalized_path == "/add-employee"
        or normalized_path.startswith("/employees/")
        or normalized_path.startswith("/payments/employee/")
        or normalized_path.startswith("/payments/") and normalized_path.endswith("/verify")
        or normalized_path == "/payments/run"
        or normalized_path == "/payroll/process"
    )

    if api_active and api_mutation:
        _api_fault_observed.set()
        return _queue_response(
            request.method, normalized_path, payload, "api"
        )

    return None



@app.errorhandler(sqlite3.OperationalError)
def handle_sqlite_operational_error(exc):
    """Handle real SQLite errors without claiming a write succeeded."""
    active = database_fault_active()

    if active and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        return _queue_response(
            request.method,
            normalize_path(request.path),
            request.get_json(silent=True) or {},
            "database",
        )

    app.logger.exception("SQLite operation failed: %s", exc)

    return jsonify({
        "success": False,
        "error": (
            "DATABASE_UNAVAILABLE" if active else "DATABASE_ERROR"
        ),
        "message": (
            "SQLite is temporarily unavailable. The operation was not "
            "confirmed as completed."
            if active
            else "A database operation failed. Check backend logs before retrying."
        ),
    }), (503 if active else 500)


def employee_dict(row):
    item = dict(row)
    basic = float(item.get("basic_salary") or 0)
    hra = float(item.get("hra") or 0)
    bonus = float(item.get("bonus") or 0)
    allowances = float(item.get("allowances") or 0)
    deductions = float(item.get("deductions") or 0)

    item["gross_salary"] = round(
        basic + hra + bonus + allowances, 2
    )
    item["net_salary"] = round(
        item["gross_salary"] - deductions, 2
    )
    return item


def incident_start(kind, message):
    detected = now()
    start = time.monotonic()
    conn = get_db()

    try:
        cur = conn.execute(
            """INSERT INTO incidents
               (failure_type, message, recovery_status, detected_at,
                diagnosed_at, action_taken, detection_time)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                kind,
                message,
                "Detected",
                detected,
                detected,
                "Automatic recovery initiated",
                0.001,
            ),
        )
        conn.commit()
        return cur.lastrowid, start
    finally:
        conn.close()


def incident_finish(incident_id, start, status, message, action):
    duration = round(time.monotonic() - start, 3)
    recovered = now() if status == "Recovered" else None
    conn = get_db()

    try:
        conn.execute(
            """UPDATE incidents
               SET message=?, recovery_status=?, recovery_time=?,
                   action_taken=?, recovered_at=?
               WHERE id=?""",
            (
                message,
                status,
                duration,
                action,
                recovered,
                incident_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return duration


@app.route("/")
def dashboard():
    conn = get_db()
    try:
        employees = conn.execute(
            "SELECT * FROM employees ORDER BY id"
        ).fetchall()
        incidents = conn.execute(
            "SELECT * FROM incidents ORDER BY id DESC LIMIT 10"
        ).fetchall()
        total = conn.execute(
            "SELECT COALESCE(SUM(basic_salary), 0) FROM employees"
        ).fetchone()[0]

        return render_template(
            "index.html",
            employees=employees,
            incidents=incidents,
            total_salary=total,
        )
    finally:
        conn.close()


@app.route("/health", methods=["GET"])
def health():
    with _network_fault_lock:
        network_active = _network_fault_active

    try:
        conn = get_db()
        try:
            conn.execute("SELECT 1").fetchone()
            conn.execute("SELECT COUNT(*) FROM employees").fetchone()
        finally:
            conn.close()
        db_connected = True
        db_message = None
    except sqlite3.OperationalError as exc:
        db_connected = False
        db_message = str(exc)

    with _state_lock:
        api_active = bool(_chaos.get("api", False))

    healthy = db_connected and not network_active and not api_active

    payload = {
        "status": "healthy" if healthy else "degraded",
        "database": "connected" if db_connected else "unavailable",
        "network": "unavailable" if network_active else "healthy",
        "api": "unavailable" if api_active else "healthy",
    }
    if db_message:
        payload["message"] = db_message

    return jsonify(payload), (200 if healthy else 503)


@app.route("/dashboard-data")
def dashboard_data():
    """Serve live data or a last-known-good cached snapshot for reads."""
    with _network_fault_lock:
        network_active = _network_fault_active

    if network_active or database_fault_active():
        cached = cache_get("dashboard-data", allow_expired=True)
        if cached:
            cached["data_source"] = "cache"
            cached["warning"] = (
                "A service fault is active. Showing last-known-good data; "
                "it may be stale."
            )
            return jsonify(cached), 200

        return jsonify({
            "employees": [],
            "incidents": [],
            "payments": [],
            "total_salary": 0,
            "data_source": "unavailable",
            "warning": (
                "A service fault is active and no cached snapshot is available."
            ),
        }), 503

    conn = None
    try:
        conn = get_db()

        employees = [
            employee_dict(row)
            for row in conn.execute(
                "SELECT * FROM employees ORDER BY id"
            )
        ]
        incidents = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM incidents ORDER BY id DESC LIMIT 50"
            )
        ]
        payments = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM payments ORDER BY id DESC LIMIT 100"
            )
        ]
        total = conn.execute(
            "SELECT COALESCE(SUM(basic_salary), 0) FROM employees"
        ).fetchone()[0]

        payload = {
            "employees": employees,
            "incidents": incidents,
            "payments": payments,
            "total_salary": total,
            "data_source": "live",
            "_cache": {
                "cached": False,
                "stale": False,
                "age_seconds": 0,
            },
        }
        cache_set("dashboard-data", payload, ttl=300)
        return jsonify(payload)

    except sqlite3.OperationalError:
        cached = cache_get("dashboard-data", allow_expired=True)
        if cached:
            cached["data_source"] = "cache"
            cached["warning"] = (
                "Database unavailable. Showing last-known-good data; "
                "it may be stale."
            )
            return jsonify(cached), 200
        raise
    finally:
        if conn is not None:
            conn.close()


@app.route("/add-employee", methods=["POST"])
def add_employee():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    department = str(data.get("department", "")).strip()

    try:
        salary = float(data.get("salary", data.get("basic_salary", 0)))
        if not (0 < salary < float("inf")):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify(error="Enter a valid positive basic salary."), 400

    if not name or not department:
        return jsonify(error="Name and department are required."), 400

    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO employees(name, department, basic_salary)
               VALUES (?, ?, ?)""",
            (name, department, salary),
        )
        conn.commit()
        return jsonify(message="Employee added.", id=cur.lastrowid), 201
    finally:
        conn.close()


@app.route("/employees/<int:employee_id>/salary", methods=["PUT"])
def update_salary(employee_id):
    data = request.get_json(silent=True) or {}
    fields = ("hra", "bonus", "allowances", "deductions")
    values = []

    for field in fields:
        try:
            value = float(data.get(field, 0))
            if not (0 <= value < float("inf")):
                raise ValueError
            values.append(value)
        except (TypeError, ValueError):
            return jsonify(error=f"Invalid amount for {field}."), 400

    conn = get_db()
    try:
        cur = conn.execute(
            """UPDATE employees
               SET hra=?, bonus=?, allowances=?, deductions=?
               WHERE id=?""",
            (*values, employee_id),
        )
        if cur.rowcount == 0:
            return jsonify(error="Employee not found."), 404

        conn.commit()
        row = conn.execute(
            "SELECT * FROM employees WHERE id=?",
            (employee_id,),
        ).fetchone()
        return jsonify(employee=employee_dict(row))
    finally:
        conn.close()


@app.route("/employees/<int:employee_id>", methods=["PUT"])
def update_employee(employee_id):
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    department = str(data.get("department", "")).strip()

    try:
        salary = float(data.get("basic_salary", data.get("salary", 0)))
        if not name or not department or not (0 < salary < float("inf")):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify(
            error="Name, department and a positive basic salary are required."
        ), 400

    conn = get_db()
    try:
        cur = conn.execute(
            """UPDATE employees
               SET name=?, department=?, basic_salary=?
               WHERE id=?""",
            (name, department, salary, employee_id),
        )
        if cur.rowcount == 0:
            return jsonify(error="Employee not found."), 404

        conn.commit()
        row = conn.execute(
            "SELECT * FROM employees WHERE id=?",
            (employee_id,),
        ).fetchone()
        return jsonify(
            message="Employee updated successfully.",
            employee=employee_dict(row),
        )
    finally:
        conn.close()


@app.route("/employees/<int:employee_id>", methods=["DELETE"])
def delete_employee(employee_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM employees WHERE id=?",
            (employee_id,),
        ).fetchone()
        if not row:
            return jsonify(error="Employee not found."), 404

        payment = conn.execute(
            "SELECT id FROM payments WHERE employee_id=? LIMIT 1",
            (employee_id,),
        ).fetchone()
        if payment:
            return jsonify(
                error=(
                    "This employee has payment history and cannot be deleted. "
                    "Keep the record for audit safety."
                )
            ), 409

        conn.execute("DELETE FROM employees WHERE id=?", (employee_id,))
        conn.commit()
        return jsonify(message="Employee deleted successfully.")
    finally:
        conn.close()


@app.route("/recovery-queue", methods=["GET"])
def recovery_queue_status():
    return jsonify(summary=queue_summary(), items=queue_items())


@app.route("/cache-status", methods=["GET"])
def cache_status_route():
    return jsonify(cache_status())


def _replay_queued_request(method, path, payload):
    """Replay a queued request through the same Flask route validation."""
    # Safety check: do not replay anything outside the explicit allowlist.
    if not is_queueable_write(method, path):
        return 400, {
            "success": False,
            "error": "OPERATION_NOT_QUEUEABLE",
        }

    with app.test_client() as client:
        response = client.open(path, method=method, json=payload)
        body = response.get_json(silent=True) or {}
        return response.status_code, body


def process_recovery_queue():
    """Replay eligible writes only after dependency recovery is verified."""
    if database_fault_active():
        app.logger.warning("Queue replay skipped: database fault is active.")
        return 0

    with _network_fault_lock:
        if _network_fault_active:
            app.logger.warning("Queue replay skipped: network fault is active.")
            return 0

    with _state_lock:
        if _chaos.get("api", False):
            app.logger.warning("Queue replay skipped: API fault is active.")
            return 0

    try:
        # A queue implementation must record final outcomes and must not
        # blindly replay an operation whose result is uncertain.
        return queue_process_pending(_replay_queued_request)
    except Exception:
        app.logger.exception("Recovery queue processing failed")
        return 0


@app.route("/api-status", methods=["GET"])
def api_status():
    with _state_lock:
        active = bool(_chaos.get("api", False))

    return jsonify({
        "status": "unavailable" if active else "healthy",
        "active": active,
        "scope": "payroll mutation endpoints",
        "monitoring_available": True,
        "failure_mode": "HTTP 503 application-level API outage",
        "circuit_breaker": "open" if active else "closed",
        "retry_policy": (
            "Only allowlisted writes may queue. Payment outcomes are never "
            "assumed successful after a timeout."
        ),
        "message": (
            "Payroll operations are temporarily unavailable."
            if active else "Payroll API is accepting operations."
        ),
    }), 200


@app.route("/database-status", methods=["GET"])
def database_status():
    return jsonify(get_database_fault_status()), 200


@app.route("/network-status", methods=["GET"])
def network_status():
    with _network_fault_lock:
        active = _network_fault_active
        started = _network_fault_started
        duration = _network_fault_duration

    elapsed = (
        round(time.monotonic() - started, 2)
        if active and started is not None
        else None
    )
    return jsonify({
        "status": "Unavailable" if active else "Healthy",
        "active": active,
        "elapsed_seconds": elapsed,
        "planned_duration_seconds": duration,
    }), 200


@app.route("/recovery-status", methods=["GET"])
def recovery_status():
    with _recovery_status_lock:
        items = {
            key: dict(value)
            for key, value in _recovery_status.items()
        }

    current = datetime.now().astimezone()
    for item in items.values():
        endpoint_time = current

        if not item.get("active") and item.get("finished_at"):
            try:
                endpoint_time = datetime.fromisoformat(item["finished_at"])
            except (TypeError, ValueError):
                endpoint_time = current

        try:
            started = datetime.fromisoformat(item.get("started_at"))
            elapsed = max(
                0, int((endpoint_time - started).total_seconds())
            )
        except (TypeError, ValueError):
            elapsed = int(item.get("elapsed_seconds", 0) or 0)

        item["elapsed_seconds"] = elapsed
        item["remaining_seconds"] = (
            0 if not item.get("active") else max(
                0,
                int(item.get(
                    "target_duration_seconds",
                    RECOVERY_DEMO_TARGET_SECONDS,
                )) - elapsed,
            )
        )

    return jsonify({
        "recoveries": items,
        "stages": [
            {"id": item[0], "label": item[1]}
            for item in _RECOVERY_STAGES
        ],
    })
@app.route("/chaos/network", methods=["POST"])
def chaos_network():
    """Inject a network fault, then restore and verify backend health."""
    global _network_fault_active
    global _network_fault_started
    global _network_fault_duration

    with _network_fault_lock:
        if _network_fault_active:
            return jsonify(
                started=False,
                message="A network fault is already in progress.",
            ), 409

    try:
        incident_id, incident_started = incident_start(
            "Network / HTTP 503",
            "Network fault injected; write requests are blocked.",
        )
    except Exception:
        app.logger.exception("Could not record network incident")
        return jsonify(error="Could not record the incident."), 500

    with _network_fault_lock:
        _network_fault_active = True
        _network_fault_started = time.monotonic()
        _network_fault_duration = None
        _network_fault_observed.clear()

    set_recovery_stage(
        "network",
        "detecting",
        "Network fault injected. Write operations are blocked.",
        incident_id,
        now(),
    )

    def recover_network_fault():
        global _network_fault_active
        global _network_fault_started
        global _network_fault_duration

        threading.Event().wait(1.2)

        set_recovery_stage(
            "network",
            "analyzing",
            "Checking service availability and preparing recovery.",
            incident_id,
        )

        # Clear only the injected fault. Actual health must still be checked.
        set_recovery_stage(
            "network",
            "working",
            "Restoring backend request handling.",
            incident_id,
        )

        with _network_fault_lock:
            _network_fault_active = False
            _network_fault_started = None
            _network_fault_duration = None

        checks = {}
        try:
            with app.test_client() as client:
                health_response = client.get("/health")
                dashboard_response = client.get("/dashboard-data")
                checks["/health"] = health_response.status_code
                checks["/dashboard-data"] = dashboard_response.status_code

            verified = (
                checks["/health"] == 200
                and checks["/dashboard-data"] == 200
            )
        except Exception as exc:
            checks["verification_error"] = str(exc)
            verified = False

        set_recovery_stage(
            "network",
            "verifying",
            "Checking health and dashboard endpoints after recovery.",
            incident_id,
        )

        wait_for_minimum_demo_window(incident_started, 15)

        try:
            incident_finish(
                incident_id,
                incident_started,
                "Recovered" if verified else "Unrecovered",
                f"Network recovery checks: {checks}",
                (
                    "Network health and dashboard checks passed."
                    if verified
                    else "Network recovery verification failed."
                ),
            )
            incident_recorded = True
        except Exception:
            incident_recorded = False
            app.logger.exception("Could not persist network incident outcome")

        if verified and incident_recorded:
            set_recovery_stage(
                "network",
                "recovered",
                "Network health and dashboard checks passed.",
                incident_id,
            )
            process_recovery_queue()
        else:
            set_recovery_failure(
                "network",
                "Recovery verification or incident recording failed.",
            )

    threading.Thread(
        target=recover_network_fault,
        daemon=True,
        name="resilpay-network-recovery",
    ).start()

    return jsonify(
        started=True,
        incident_id=incident_id,
        message="Network fault injected; recovery verification has started.",
        recovery_mode="condition-based",
    ), 202


@app.route("/chaos/<kind>", methods=["POST"])
def chaos_other(kind):
    """Inject API or payment-provider chaos and verify recovery."""
    if kind == "database":
        return jsonify(
            started=False,
            message="Use /chaos/database for the database failure scenario.",
        ), 409

    if kind not in ("api", "payment"):
        return jsonify(error="Unknown chaos scenario."), 404

    with _state_lock:
        if _chaos[kind]:
            return jsonify(error=f"{kind} chaos is already active."), 409

    labels = {
        "api": "API connection failure",
        "payment": "Payment gateway timeout",
    }

    try:
        incident_id, started = incident_start(
            labels[kind],
            f"{labels[kind]} injected; monitoring remains available. New test payments may remain pending verification.",
        )
    except Exception:
        app.logger.exception("Could not record %s incident", kind)
        return jsonify(error="Could not record the incident."), 500

    with _state_lock:
        _chaos[kind] = True
        if kind == "api":
            _api_fault_observed.clear()
        else:
            _payment_fault_observed.clear()

    set_recovery_stage(
        kind,
        "detecting",
        f"{labels[kind]} injected.",
        incident_id,
        now(),
    )
    def recover():
        set_recovery_stage(
            kind,
            "analyzing",
            f"Analyzing the {kind} failure and preparing recovery.",
            incident_id,
        )

        set_recovery_stage(
            kind,
            "working",
            f"{kind.capitalize()} remains unavailable during automatic recovery.",
            incident_id,
        )

        # Keep the API/provider fault active throughout the demo window.
        wait_for_minimum_demo_window(started, 15)

        with _state_lock:
            _chaos[kind] = False

        checks = {}
        verified = False

        try:
            with app.test_client() as client:
                if kind == "api":
                    status_response = client.get("/api-status")
                    health_response = client.get("/health")
                    checks["/api-status"] = status_response.status_code
                    checks["/health"] = health_response.status_code

                    verified = (
                        status_response.status_code == 200
                        and status_response.get_json().get("active") is False
                        and health_response.status_code == 200
                    )
                else:
                    health_response = client.get("/health")
                    checks["/health"] = health_response.status_code
                    verified = health_response.status_code == 200
        except Exception as exc:
            checks["verification_error"] = str(exc)
            verified = False

        set_recovery_stage(
            kind,
            "verifying",
            "Checking service health after restoring access.",
            incident_id,
        )

        try:
            incident_finish(
                incident_id,
                started,
                "Recovered" if verified else "Unrecovered",
                f"{labels[kind]} recovery checks: {checks}",
                (
                    "Service health checks passed."
                    if verified
                    else "Service health verification failed."
                ),
            )
            incident_recorded = True
        except Exception:
            incident_recorded = False
            app.logger.exception(
                "Could not persist %s incident outcome", kind
            )

        if verified and incident_recorded:
            set_recovery_stage(
                kind,
                "recovered",
                f"{labels[kind]} recovery verified.",
                incident_id,
            )
            if kind == "api":
                process_recovery_queue()
        else:
            set_recovery_failure(
                kind,
                "Recovery verification or incident recording failed.",
            )

    threading.Thread(
        target=recover,
        daemon=True,
        name=f"resilpay-{kind}-recovery",
    ).start()

    return jsonify(
        started=True,
        incident_id=incident_id,
        message=f"{labels[kind]} injected; recovery has started.",
        recovery_mode="condition-based",
    ), 202


@app.route("/chaos/database", methods=["POST"])
def chaos_database():
    """Inject SQLite failure, verify recovery, then replay queued writes."""
    if database_fault_active():
        return jsonify(
            started=False,
            message="A database fault is already active.",
        ), 409

    try:
        incident_id, incident_started = incident_start(
            "Database failure",
            "SQLite failure injected; supported writes will be queued.",
        )
    except Exception:
        app.logger.exception("Could not record database incident")
        return jsonify(error="Could not record the incident."), 500

    started, _duration = activate_database_fault()
    if not started:
        try:
            incident_finish(
                incident_id,
                incident_started,
                "Cancelled",
                "Database fault was already active.",
                "No additional fault was started.",
            )
        except Exception:
            app.logger.exception("Could not close duplicate database incident")

        return jsonify(
            started=False,
            message="A database fault is already active.",
        ), 409

    set_recovery_stage(
        "database",
        "detecting",
        "SQLite connection fault injected. Supported writes are queued.",
        incident_id,
        now(),
    )


    def recover_database():
        # Keep the simulated database outage active until the recovery
        # process reaches its actual restoration step.
        observed = wait_for_database_fault_observed(timeout=8)

        if not observed:
            app.logger.warning(
                "No database request encountered the injected fault "
                "within 8 seconds."
            )

        set_recovery_stage(
            "database",
            "analyzing",
            "Analyzing the database failure and preparing recovery.",
            incident_id,
        )

        set_recovery_stage(
            "database",
            "working",
            "Database remains unavailable while automatic recovery is in progress.",
            incident_id,
        )

        # IMPORTANT: wait BEFORE restoring access, not after it.
        wait_for_minimum_demo_window(incident_started, 15)

        # Restore SQLite access only when the outage window is complete.
        try:
            deactivate_database_fault()
        except Exception:
            app.logger.exception("Could not deactivate database fault")
            set_recovery_failure(
                "database",
                "Could not restore database access. Queued changes remain pending.",
            )
            return

        def database_probe():
            conn = None
            try:
                conn = get_db()
                conn.execute("SELECT 1").fetchone()
                conn.execute("SELECT COUNT(*) FROM employees").fetchone()
                return True, "SQLite connection and employee query succeeded."
            except Exception as exc:
                return False, str(exc)
            finally:
                if conn is not None:
                    conn.close()

        set_recovery_stage(
            "database",
            "verifying",
            "Verifying SQLite access before replaying queued changes.",
            incident_id,
        )

        healthy, detail = database_probe()
        checks = {
            "sqlite_probe": {
                "healthy": healthy,
                "detail": detail,
            }
        }

        # Record the incident outcome before replaying any queued operation.
        try:
            incident_finish(
                incident_id,
                incident_started,
                "Recovered" if healthy else "Unrecovered",
                f"Database recovery checks: {checks}",
                (
                    "SQLite queries passed."
                    if healthy
                    else "SQLite verification failed."
                ),
            )
            incident_recorded = True
        except Exception:
            incident_recorded = False
            app.logger.exception(
                "Could not persist database incident outcome"
            )

        if healthy and incident_recorded:
            set_recovery_stage(
                "database",
                "recovered",
                "SQLite verified healthy. Processing queued employee changes.",
                incident_id,
            )
            process_recovery_queue()
        else:
            set_recovery_failure(
                "database",
                "SQLite verification or incident recording failed. "
                "Queued operations remain pending.",
            )


    threading.Thread(
        target=recover_database,
        daemon=True,
        name="resilpay-database-recovery",
    ).start()

    return jsonify(
        started=True,
        incident_id=incident_id,
        message=(
            "Database failure injected. Supported operations will wait in "
            "the recovery queue until SQLite recovery is verified."
        ),
        recovery_mode="condition-based",
    ), 202


def make_payment(employee_id, run_id, amount_override=None, payment_method="UPI"):
    """Create or return a payment attempt, preserving idempotency per attempt."""
    base_key = f"resilpay-{run_id}-{employee_id}"
    conn = get_db()

    try:
        # A confirmed payment closes this employee's payroll period. Never
        # create another attempt once PAID exists for the same period.
        already_paid = conn.execute(
            """SELECT * FROM payments
               WHERE employee_id=?
                 AND payroll_period=?
                 AND status='PAID'
               ORDER BY id DESC LIMIT 1""",
            (employee_id, run_id),
        ).fetchone()

        if already_paid:
            return dict(already_paid), False

        attempts = conn.execute(
            """SELECT * FROM payments
               WHERE employee_id=? AND payroll_period=?
               ORDER BY id DESC""",
            (employee_id, run_id),
        ).fetchall()

        if attempts and attempts[0]["status"] == "PENDING_VERIFICATION":
            # A retry is blocked until the original attempt is reconciled.
            return dict(attempts[0]), False

        if attempts and attempts[0]["status"] not in ("FAILED",):
            # Reuse a previously recorded non-final attempt rather than
            # generating a second charge.
            return dict(attempts[0]), False

        attempt_number = len(attempts) + 1
        key = base_key if attempt_number == 1 else f"{base_key}-attempt-{attempt_number}"

        emp_row = conn.execute(
            "SELECT * FROM employees WHERE id=?",
            (employee_id,),
        ).fetchone()

        if not emp_row:
            raise LookupError("Employee not found.")

        emp = employee_dict(emp_row)
        # Queued payroll jobs retain the salary snapshot captured when queued.
        amount = float(amount_override) if amount_override is not None else emp["net_salary"]

        if amount <= 0:
            raise ValueError("Net payable must be positive.")

        # Execute the active processor implementation, not only a version label.
        processor_versions.process_payment(amount)

        with _state_lock:
            provider_failed = bool(_chaos.get("payment", False))

        if provider_failed:
            _payment_fault_observed.set()

        # A simulated gateway timeout is ambiguous: the request may have
        # reached the provider, so never label it as a definite decline or
        # automatically retry it. Persist the outcome for reconciliation.
        status = "PENDING_VERIFICATION" if provider_failed else "PAID"
        message = (
            "Gateway timeout: the test processor did not return a confirmation. "
            "No success is assumed; verify this transaction before retrying."
            if provider_failed
            else f"Simulated {payment_method} payment confirmed. No real money moved."
        )

        cur = conn.execute(
            """INSERT INTO payments
               (employee_id, employee_name, amount, idempotency_key,
                status, message, updated_at, payroll_period, payment_method)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                employee_id,
                emp["name"],
                amount,
                key,
                status,
                message,
                now(),
                run_id,
                payment_method,
            ),
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM payments WHERE id=?",
            (cur.lastrowid,),
        ).fetchone()

        return dict(row), True
    finally:
        conn.close()


@app.route("/payments/employee/<int:employee_id>", methods=["POST"])
def pay_employee(employee_id):
    data = request.get_json(silent=True) or {}
    run_id = str(data.get("run_id") or datetime.now().strftime("%Y-%m"))
    run_id = "".join(
        ch for ch in run_id if ch.isalnum() or ch in "-_"
    )[:80]

    if not run_id:
        return jsonify(error="Invalid payroll run ID."), 400

    try:
        payment_method = str(data.get("payment_method") or "UPI").upper()
        if payment_method not in ("UPI", "CARD", "NETBANKING", "WALLET"):
            return jsonify(error="Choose a supported test payment method."), 400
        payment, created = make_payment(employee_id, run_id, payment_method=payment_method)
    except LookupError as exc:
        return jsonify(error=str(exc)), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except RuntimeError as exc:
        conn = get_db()
        try:
            employee = conn.execute("SELECT id, basic_salary, hra, bonus, allowances, deductions FROM employees WHERE id=?", (employee_id,)).fetchone()
            if not employee:
                return jsonify(error="Employee not found."), 404
            amount = max(0.0, float(employee["basic_salary"] or 0) + float(employee["hra"] or 0) + float(employee["bonus"] or 0) + float(employee["allowances"] or 0) - float(employee["deductions"] or 0))
        finally:
            conn.close()
        job, _created = processor_rollback.enqueue_job(employee_id, run_id, amount)
        return jsonify(success=True, queued=True, job=job, message="Processor is unhealthy; payment job was saved as pending. No payment was marked PAID.", processor_error=str(exc)), 202

    if payment["status"] != "PAID":
        return jsonify(
            error=payment.get("message", "Payment is not confirmed."),
            payment=payment,
            message=(payment.get("message") or "Transaction is not confirmed.") + " Do not retry until its status is verified.",
        ), 503

    return jsonify(
        message=(
            "Simulated payment confirmed."
            if created
            else "Existing transaction returned; no duplicate created."
        ),
        payment=payment,
        duplicate_prevented=not created,
    ), 200


@app.route("/payments/run", methods=["POST"])
def payments_run():
    data = request.get_json(silent=True) or {}
    run_id = str(data.get("run_id") or datetime.now().strftime("%Y-%m"))
    run_id = "".join(
        ch for ch in run_id if ch.isalnum() or ch in "-_"
    )[:80]

    if not run_id:
        return jsonify(error="Invalid payroll run ID."), 400

    payment_method = str(data.get("payment_method") or "UPI").upper()
    if payment_method not in ("UPI", "CARD", "NETBANKING", "WALLET"):
        return jsonify(error="Choose a supported test payment method."), 400

    employee_ids = data.get("employee_ids")
    conn = get_db()

    try:
        if employee_ids is not None:
            if not isinstance(employee_ids, list) or not employee_ids:
                return jsonify(
                    error="employee_ids must be a non-empty list."
                ), 400

            # Accept only positive integer IDs.
            if any(
                isinstance(item, bool)
                or not isinstance(item, int)
                or item <= 0
                for item in employee_ids
            ):
                return jsonify(
                    error="employee_ids must contain positive integer IDs."
                ), 400

            placeholders = ",".join("?" for _ in employee_ids)
            rows = conn.execute(
                f"SELECT id FROM employees WHERE id IN ({placeholders})",
                employee_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM employees ORDER BY id"
            ).fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        try:
            payment, _ = make_payment(row["id"], run_id, payment_method=payment_method)
            results.append(payment)
        except (LookupError, ValueError):
            continue
        except RuntimeError as exc:
            conn = get_db()
            try:
                employee = conn.execute("SELECT id, basic_salary, hra, bonus, allowances, deductions FROM employees WHERE id=?", (row["id"],)).fetchone()
                if employee:
                    amount = max(0.0, float(employee["basic_salary"] or 0) + float(employee["hra"] or 0) + float(employee["bonus"] or 0) + float(employee["allowances"] or 0) - float(employee["deductions"] or 0))
                    job, _created = processor_rollback.enqueue_job(row["id"], run_id, amount)
                    results.append({"employee_id": row["id"], "status": "PENDING", "message": "Processor unhealthy; queued safely.", "job_id": job["id"], "processor_error": str(exc)})
            finally:
                conn.close()

    failed = sum(payment["status"] not in ("PAID", "PENDING") for payment in results)
    queued = sum(payment["status"] == "PENDING" for payment in results)

    return jsonify(
        message="Payroll run finished; inspect individual payment and job statuses.",
        run_id=run_id,
        payments=results,
        failed_count=failed,
        queued_count=queued,
        paid_count=sum(payment["status"] == "PAID" for payment in results),
        real_money_transferred=False,
    ), (202 if queued else (503 if failed else 200))


@app.route("/payments")
def payments():
    conn = get_db()
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM payments ORDER BY id DESC LIMIT 200"
            )
        ]
        return jsonify(payments=rows)
    finally:
        conn.close()



@app.route("/payments/<int:payment_id>/verify", methods=["POST"])
def verify_payment(payment_id):
    """Reconcile a pending local test transaction after the gateway recovers.

    This simulator never contacts a real provider. While the injected gateway
    fault is active, the result remains unknown. Once recovered, the local
    test ledger can confirm that no simulated charge was recorded and mark
    this attempt FAILED so a new idempotent attempt may be created safely.
    """
    with _state_lock:
        gateway_fault_active = bool(_chaos.get("payment", False))

    if gateway_fault_active:
        return jsonify(
            success=False,
            status="PENDING_VERIFICATION",
            message="The simulated gateway is still unavailable. Check again after recovery.",
        ), 409

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM payments WHERE id=?", (payment_id,)
        ).fetchone()
        if not row:
            return jsonify(success=False, message="Payment transaction not found."), 404

        payment = dict(row)
        if payment["status"] == "PAID":
            return jsonify(
                success=True,
                status="PAID",
                payment=payment,
                message="This transaction is already confirmed as paid. Do not create another payment.",
            ), 200

        if payment["status"] != "PENDING_VERIFICATION":
            return jsonify(
                success=True,
                status=payment["status"],
                payment=payment,
                message=f"This transaction is already marked {payment['status']}.",
            ), 200

        # In this local simulator, an injected timeout does not write a charge
        # to a provider ledger. Once the fault clears, reconciliation confirms
        # this attempt did not charge and a separate retry is safe.
        message = (
            "Verified in local test mode: no simulated charge was recorded. "
            "You can safely start a new payment attempt."
        )
        conn.execute(
            """UPDATE payments SET status='FAILED', message=?, updated_at=?
               WHERE id=? AND status='PENDING_VERIFICATION'""",
            (message, now(), payment_id),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM payments WHERE id=?", (payment_id,)
        ).fetchone()
        return jsonify(
            success=True,
            status="FAILED",
            payment=dict(updated),
            message=message,
            safe_to_retry=True,
            real_money_transferred=False,
        ), 200
    finally:
        conn.close()


# --- Simulated processor deployment / rollback API ---
processor_rollback.init_storage()
processor_versions.health_check()


def _resume_processor_job(employee_id, payroll_period, amount):
    """Resume the persisted salary snapshot after checking payment idempotency."""
    payment, _created = make_payment(int(employee_id), str(payroll_period), amount_override=float(amount))
    return payment


processor_rollback.configure_job_processor(_resume_processor_job)


@app.route("/processor-status", methods=["GET"])
@app.route("/api/processor-status", methods=["GET"])
def processor_status():
    return jsonify(processor_rollback.get_status()), 200


@app.route("/processor-incidents", methods=["GET"])
@app.route("/api/processor-incidents", methods=["GET"])
def processor_incidents():
    return jsonify(incidents=processor_rollback.get_incidents()), 200


@app.route("/processor-events", methods=["GET"])
@app.route("/api/processor-events", methods=["GET"])
def processor_events():
    return jsonify(events=processor_rollback.get_events()), 200


@app.route("/processor-payments", methods=["GET"])
@app.route("/api/processor-payments", methods=["GET"])
def processor_payments():
    return jsonify(payments=processor_rollback.get_payments()), 200


@app.route("/chaos/deploy-faulty", methods=["POST"])
@app.route("/api/chaos/deploy-faulty", methods=["POST"])
def deploy_faulty_processor():
    result, status_code = processor_rollback.deploy_faulty_version()
    return jsonify(result), status_code


@app.route("/payroll/process", methods=["POST"])
@app.route("/api/payroll/process", methods=["POST"])
def process_payroll_with_processor():
    """Queue jobs while unhealthy; otherwise process idempotently."""
    data = request.get_json(silent=True) or {}
    payroll_period = str(data.get("payroll_period") or data.get("run_id") or datetime.now().strftime("%Y-%m"))
    payroll_period = "".join(ch for ch in payroll_period if ch.isalnum() or ch in "-_")[:80]
    if not payroll_period:
        return jsonify(error="Invalid payroll period."), 400

    conn = get_db()
    try:
        employees = conn.execute(
            "SELECT id, basic_salary, hra, bonus, allowances, deductions FROM employees ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    health = processor_versions.health_check()
    recovering = processor_versions.get_deployment_status() in ("deploying", "detecting", "rolling_back", "verifying")
    results = []
    if not health["healthy"] or recovering:
        for employee in employees:
            amount = max(0.0, float(employee["basic_salary"] or 0) + float(employee["hra"] or 0) + float(employee["bonus"] or 0) + float(employee["allowances"] or 0) - float(employee["deductions"] or 0))
            job, created = processor_rollback.enqueue_job(employee["id"], payroll_period, amount)
            results.append({"employee_id": employee["id"], "status": job["status"], "job_id": job["id"], "created": created})
        return jsonify(message="Processor unhealthy or recovering; payroll jobs are persisted and will be resumed only after verified rollback.", payroll_period=payroll_period, queued_count=len(results), results=results, real_money_transferred=False), 202

    for employee in employees:
        try:
            payment, created = make_payment(employee["id"], payroll_period)
            results.append({"employee_id": employee["id"], "status": payment.get("status"), "payment_id": payment.get("id"), "created": created})
        except (LookupError, ValueError) as exc:
            results.append({"employee_id": employee["id"], "status": "FAILED", "message": str(exc)})
        except RuntimeError as exc:
            amount = max(0.0, float(employee["basic_salary"] or 0) + float(employee["hra"] or 0) + float(employee["bonus"] or 0) + float(employee["allowances"] or 0) - float(employee["deductions"] or 0))
            job, created = processor_rollback.enqueue_job(employee["id"], payroll_period, amount)
            results.append({"employee_id": employee["id"], "status": job["status"], "job_id": job["id"], "created": created, "message": str(exc)})

    queued = sum(item.get("status") == "Pending" for item in results)
    failed = sum(item.get("status") == "FAILED" for item in results)
    return jsonify(message="Simulated payroll processing finished.", payroll_period=payroll_period, results=results, paid_count=sum(item.get("status") == "PAID" for item in results), queued_count=queued, failed_count=failed, real_money_transferred=False), (202 if queued else (503 if failed else 200))

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
        threaded=True,
    )
