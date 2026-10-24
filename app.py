from datetime import datetime
import sqlite3
import threading
import time
import uuid
from flask import Flask, request, jsonify, render_template
from cache import get_value as cache_get, set_value as cache_set, status as cache_status
from recovery_queue import enqueue as queue_enqueue, list_items as queue_items, summary as queue_summary, process_pending as queue_process_pending
from database import (
    get_db,
    init_db,
    activate_database_fault,
    deactivate_database_fault,
    get_database_fault_status,
)
app = Flask(__name__)
init_db()

_state_lock = threading.Lock()
_chaos = {"api": False, "payment": False}
_api_fault_started = None
_api_fault_duration = None


# Real HTTP fault injection for the local ResilPay backend.
_network_fault_lock = threading.Lock()
_network_fault_active = False
_network_fault_started = None
_network_fault_duration = None
_network_fault_observed = threading.Event()
_api_fault_observed = threading.Event()
_payment_fault_observed = threading.Event()

# Live recovery lifecycle consumed by the Control Center. Kept in memory so
# the UI can poll stage changes without touching payroll records.
_recovery_status_lock = threading.Lock()
_recovery_status = {}
_RECOVERY_STAGES = (
    ("detecting", "Detecting error"),
    ("analyzing", "Analyzing issue"),
    ("working", "Working on recovery"),
    ("verifying", "Verifying health"),
    ("recovered", "Recovered"),
)

def set_recovery_stage(kind, stage, message, incident_id=None, started_at=None):
    stage_index = next((i for i, item in enumerate(_RECOVERY_STAGES) if item[0] == stage), 0)
    with _recovery_status_lock:
        old = _recovery_status.get(kind, {})
        _recovery_status[kind] = {
            "kind": kind, "stage": stage, "stage_label": _RECOVERY_STAGES[stage_index][1],
            "stage_index": stage_index, "message": message,
            "incident_id": incident_id if incident_id is not None else old.get("incident_id"),
            "started_at": started_at if started_at is not None else old.get("started_at"),
            "updated_at": now(), "active": stage not in ("recovered", "failed"),
            "history": (old.get("history", []) + [{"stage": stage, "label": _RECOVERY_STAGES[stage_index][1], "at": now(), "message": message}])[-12:],
        }

def set_recovery_failure(kind, message):
    with _recovery_status_lock:
        old = _recovery_status.get(kind, {})
        _recovery_status[kind] = {**old, "stage": "failed", "stage_label": "Recovery needs attention",
            "message": message, "active": False, "updated_at": now(),
            "history": (old.get("history", []) + [{"stage": "failed", "label": "Recovery needs attention", "at": now(), "message": message}])[-12:]}


# Recovery retry policy: exponential backoff with real health/availability probes.
# The delays are retry intervals, not a command to mark recovery successful.
# A fault is cleared only after the recovery policy has exhausted its probes,
# followed by explicit post-recovery health verification.
RECOVERY_BACKOFF_SECONDS = (1, 2, 4, 8, 8)  # ~23 seconds before corrective action

def recovery_backoff_probe(probe, label):
    """Probe a failing dependency with bounded exponential backoff.

    Returns an audit list. A failed probe never marks the service recovered;
    the caller must perform a corrective action and then verify health.
    """
    attempts = []
    kind = label.split()[0].lower()
    if kind not in ("network", "api", "payment", "database"):
        kind = label.lower()
    set_recovery_stage(kind, "analyzing", f"Checking {label} availability and collecting diagnostic evidence.")
    for attempt, delay in enumerate(RECOVERY_BACKOFF_SECONDS, start=1):
        # Retry intervals are real probe/backoff work; progress changes while
        # diagnostics run rather than showing a static loading indicator.
        if attempt >= 3:
            set_recovery_stage(kind, "working", f"Applying recovery strategy; diagnostic retry {attempt} of {len(RECOVERY_BACKOFF_SECONDS)}.")
        threading.Event().wait(delay)
        try:
            healthy, detail = probe()
        except Exception as exc:
            healthy, detail = False, str(exc)
        attempts.append({"attempt": attempt, "backoff_seconds": delay,
                         "healthy": bool(healthy), "detail": str(detail)})
        if healthy:
            break
    return attempts

@app.before_request
def inject_real_network_error():
    """Inject scoped, observable outages while keeping monitoring endpoints live.

    The API chaos scenario returns an explicit service-unavailable response on
    payroll mutation endpoints. This is an application-level API outage (HTTP
    503), not a TCP socket reset; keeping the process alive allows self-healing
    and health checks to run in the same demo backend.
    """
    path = request.path
    normalized_path = path.removeprefix("/api")

    # These endpoints must stay available so the Control Center can monitor,
    # trigger, and verify recovery while business operations are unavailable.
    always_available = {
        "/", "/health", "/api-status", "/chaos/api", "/chaos/network",
        "/network-status", "/database-status", "/recovery-status",
    }
    if normalized_path in always_available or request.method == "OPTIONS":
        return None

    # Existing network chaos affects ordinary application requests.
    with _network_fault_lock:
        network_active = _network_fault_active
    if network_active:
        _network_fault_observed.set()
        queued = queue_enqueue(request.method, normalized_path, request.get_json(silent=True) or {})
        if queued and not queued.get("full"):
            return jsonify(success=True, queued=True, queue_id=queued["queue_id"], status="Queued", message="Operation safely queued while network recovery is in progress; it is not saved yet."), 202
        if queued and queued.get("full"):
            return jsonify(success=False, error="RECOVERY_QUEUE_FULL", message="Recovery queue is full; no operation was accepted."), 429
        return jsonify({
            "success": False,
            "error": "NETWORK_FAULT",
            "message": "ResilPay network fault is active; request could not be completed.",
            "recovery": "Automatic recovery is in progress.",
        }), 503, {"Retry-After": "2"}

    # API chaos is scoped to employee/payroll writes. Monitoring and dashboard
    # polling remain available throughout the fault.
    api_mutation = (
        request.method in ("POST", "PUT", "PATCH", "DELETE")
        and (
            normalized_path == "/add-employee"
            or (normalized_path.startswith("/employees/") and normalized_path.endswith("/salary"))
            or normalized_path.startswith("/payments/employee/")
            or normalized_path == "/payments/run"
        )
    )
    with _state_lock:
        api_active = _chaos.get("api", False)
    if api_active and api_mutation:
        _api_fault_observed.set()
        queued = queue_enqueue(request.method, normalized_path, request.get_json(silent=True) or {})
        if queued and not queued.get("full"):
            return jsonify(success=True, queued=True, queue_id=queued["queue_id"], status="Queued", message="Operation queued during API outage; it is pending and has not been saved yet."), 202
        if queued and queued.get("full"):
            return jsonify(success=False, error="RECOVERY_QUEUE_FULL", message="Recovery queue is full; no operation was accepted."), 429
        return jsonify({
            "success": False,
            "error": "API_CONNECTION_UNAVAILABLE",
            "message": "Payroll API is temporarily unavailable. No success was confirmed; check the latest records before retrying this change.",
            "recovery": "Automatic recovery is in progress; service status remains available.",
        }), 503, {"Retry-After": "2", "X-ResilPay-Fault": "api"}

    return None



def now():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def database_fault_active():
    """Read the injected SQLite-fault state without opening a DB connection."""
    try:
        state = get_database_fault_status()
        return bool(state.get("active", False)) if isinstance(state, dict) else False
    except Exception:
        return False


@app.errorhandler(sqlite3.OperationalError)
def handle_sqlite_operational_error(exc):
    """Return a useful JSON response for SQLite connection/query failures."""
    active = database_fault_active()
    if active and request.method in ("POST", "PUT"):
        queued = queue_enqueue(request.method, request.path.removeprefix("/api"), request.get_json(silent=True) or {})
        if queued and not queued.get("full"):
            return jsonify(success=True, queued=True, queue_id=queued["queue_id"], status="Queued", message="Database unavailable; operation is queued and has not been saved yet."), 202
        if queued and queued.get("full"):
            return jsonify(success=False, error="RECOVERY_QUEUE_FULL", message="Recovery queue is full; operation was not accepted."), 429
    return jsonify({
        "success": False,
        "error": "DATABASE_UNAVAILABLE" if active else "DATABASE_ERROR",
        "message": (
            "SQLite access is temporarily unavailable; automatic recovery is in progress."
            if active else "A database operation failed. Please retry after checking the backend logs."
        ),
    }), (503 if active else 500)


def employee_dict(row):
    item = dict(row)
    basic = float(item.get("basic_salary") or 0)
    hra = float(item.get("hra") or 0)
    bonus = float(item.get("bonus") or 0)
    allowances = float(item.get("allowances") or 0)
    deductions = float(item.get("deductions") or 0)
    item["gross_salary"] = round(basic + hra + bonus + allowances, 2)
    item["net_salary"] = round(item["gross_salary"] - deductions, 2)
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
            (kind, message, "Detected", detected, detected,
             "Automatic recovery initiated", 0.001),
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
            (message, status, duration, action, recovered, incident_id),
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
            "index.html", employees=employees,
            incidents=incidents, total_salary=total
        )
    finally:
        conn.close()


@app.route("/health", methods=["GET"])
def health():
    """Report actual SQLite connectivity and the current network-fault state."""
    with _network_fault_lock:
        network_active = _network_fault_active

    try:
        conn = get_db()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        db_connected = True
        db_message = None
    except sqlite3.OperationalError as exc:
        db_connected = False
        db_message = str(exc)

    healthy = db_connected and not network_active
    payload = {
        "status": "healthy" if healthy else "degraded",
        "database": "connected" if db_connected else "unavailable",
        "network": "unavailable" if network_active else "healthy",
    }
    if db_message:
        payload["message"] = db_message
    return jsonify(payload), (200 if healthy else 503)


# @app.route("/health", methods=["GET"])
# def health():
#     """Check database availability and report current network state."""
#     conn = None
#     try:
#         if database_fault_active():
#             return jsonify(
#                 status="degraded",
#                 database="unavailable",
#                 network="unavailable" if _network_fault_active else "healthy",
#             ), 503

#         conn = get_db()
#         conn.execute("SELECT 1").fetchone()
#         with _network_fault_lock:
#             network_active = _network_fault_active

#         return jsonify(
#             status="degraded" if network_active else "healthy",
#             database="connected",
#             network="unavailable" if network_active else "healthy",
#         ), 200
#     except Exception as exc:
#         return jsonify(
#             status="unhealthy",
#             database="unavailable",
#             message=str(exc),
#         ), 503
#     finally:
#         if conn is not None:
#             conn.close()

@app.route("/dashboard-data")
def dashboard_data():
    # Safe read fallback: use the last-known-good snapshot when the DB or
    # injected network path is unavailable. Writes never use this cache.
    with _network_fault_lock:
        network_active = _network_fault_active
    if network_active:
        cached = cache_get("dashboard-data", allow_expired=True)
        if cached:
            cached["data_source"] = "cache"
            cached["warning"] = "Network fault active. Showing last-known-good data; it may be stale."
            return jsonify(cached), 200

    conn = None
    try:
        conn = get_db()
        employees = [employee_dict(row) for row in conn.execute("SELECT * FROM employees ORDER BY id")]
        incidents = [dict(row) for row in conn.execute("SELECT * FROM incidents ORDER BY id DESC LIMIT 50")]
        payments = [dict(row) for row in conn.execute("SELECT * FROM payments ORDER BY id DESC LIMIT 100")]
        total = conn.execute("SELECT COALESCE(SUM(basic_salary), 0) FROM employees").fetchone()[0]
        payload = {"employees": employees, "incidents": incidents, "payments": payments,
                   "total_salary": total, "data_source": "live",
                   "_cache": {"cached": False, "stale": False, "age_seconds": 0}}
        cache_set("dashboard-data", payload, ttl=300)
        return jsonify(payload)
    except sqlite3.OperationalError:
        cached = cache_get("dashboard-data", allow_expired=True)
        if cached:
            cached["data_source"] = "cache"
            cached["warning"] = "Database unavailable. Showing last-known-good data; it may be stale."
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
            "SELECT * FROM employees WHERE id=?", (employee_id,)
        ).fetchone()
        return jsonify(employee=employee_dict(row))
    finally:
        conn.close()


@app.route("/recovery-queue", methods=["GET"])
def recovery_queue_status():
    return jsonify(summary=queue_summary(), items=queue_items())


@app.route("/cache-status", methods=["GET"])
def cache_status_route():
    return jsonify(cache_status())


def _replay_queued_request(method, path, payload):
    # Only allow the safe employee-write routes admitted by recovery_queue.py.
    with app.test_client() as client:
        response = client.open(path, method=method, json=payload)
        body = response.get_json(silent=True) or {}
        return response.status_code, body


def process_recovery_queue():
    try:
        return queue_process_pending(_replay_queued_request)
    except Exception:
        app.logger.exception("Recovery queue processing failed")
        return 0


@app.route("/api-status", methods=["GET"])
def api_status():
    """Status endpoint intentionally stays reachable during API chaos."""
    with _state_lock:
        active = bool(_chaos.get("api", False))
    return jsonify({
        "status": "unavailable" if active else "healthy",
        "active": active,
        "scope": "payroll mutation endpoints",
        "monitoring_available": True,
        "failure_mode": "HTTP 503 application-level API outage",
        "circuit_breaker": "open" if active else "closed",
        "retry_policy": "bounded exponential backoff for safe GET requests; no blind write/payment retry",
        "message": "Payroll operations are temporarily unavailable." if active else "Payroll API is accepting operations.",
    }), 200


@app.route("/database-status", methods=["GET"])
def database_status():
    """Expose injected database fault state without opening SQLite."""
    return jsonify(get_database_fault_status()), 200


@app.route("/network-status", methods=["GET"])
def network_status():
    """Expose the injected fault state without external-service dependencies."""
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
        items = {key: dict(value) for key, value in _recovery_status.items()}
    return jsonify({"recoveries": items, "stages": [
        {"id": item[0], "label": item[1]} for item in _RECOVERY_STAGES
    ]})


@app.route("/chaos/network", methods=["POST"])
def chaos_network():
    """Inject HTTP 503 responses, perform corrective recovery, then verify health."""
    global _network_fault_active
    global _network_fault_started
    global _network_fault_duration

    with _network_fault_lock:
        if _network_fault_active:
            return jsonify({
                "started": False,
                "message": "A network fault is already in progress.",
            }), 409

    # Persist the incident before activating the fault. If SQLite fails,
    # no outage is started and the application remains usable.
    incident_id, incident_started = incident_start(
        "Network / HTTP 503",
        "HTTP 503 fault injection started; normal API requests will fail temporarily.",
    )

    set_recovery_stage("network", "detecting", "Network outage injected; monitoring requests and service checks are being registered.", incident_id, now())
    with _network_fault_lock:
        # Recheck in case two trigger requests arrived almost simultaneously.
        if _network_fault_active:
            incident_finish(
                incident_id, incident_started, "Cancelled",
                "Another network fault was already active.",
                "No additional fault was started.",
            )
            return jsonify({
                "started": False,
                "message": "A network fault is already in progress.",
            }), 409
        _network_fault_active = True
        _network_fault_started = time.monotonic()
        _network_fault_duration = None
        _network_fault_observed.clear()

    def recover_network_fault():
        global _network_fault_active
        global _network_fault_started
        global _network_fault_duration

        # Keep the detection state visible to the UI before diagnostic probes start.
        threading.Event().wait(1.2)
        def network_probe():
            with _network_fault_lock:
                active = _network_fault_active
            return (not active, "injected network fault remains active" if active else "fault cleared")
        probe_history = recovery_backoff_probe(network_probe, "network")
        set_recovery_stage("network", "working", "Disabling the injected network fault and restoring request handling.", incident_id)
        # Corrective action follows the diagnostic retry policy.
        with _network_fault_lock:
            _network_fault_active = False
            _network_fault_started = None
            _network_fault_duration = None

        checks = {"recovery_probes": probe_history}
        try:
            with app.test_client() as client:
                for endpoint in ("/health", "/dashboard-data"):
                    response = client.get(endpoint)
                    checks[endpoint] = response.status_code
        except Exception as exc:
            checks["verification_error"] = str(exc)

        set_recovery_stage("network", "verifying", "Running health and dashboard checks after the corrective action.", incident_id)
        threading.Event().wait(1.5)
        verified = checks.get("/health") == 200 and checks.get("/dashboard-data") == 200
        incident_finish(
            incident_id,
            incident_started,
            "Recovered" if verified else "Unrecovered",
            (f"Network fault cleared by recovery action; checks passed: {checks}."
             if verified else f"Fault cleared, but recovery verification failed: {checks}."),
            ("Fault disabled and health/dashboard checks verified."
             if verified else "Fault disabled, but verification failed; investigation required."),
        )
        if verified:
            set_recovery_stage("network", "recovered", "Network health and dashboard requests passed verification.", incident_id)
            process_recovery_queue()
        else:
            set_recovery_failure("network", "The injected fault was cleared but post-recovery health checks failed.")

    threading.Thread(
        target=recover_network_fault,
        daemon=True,
        name="resilpay-network-recovery",
    ).start()

    return jsonify({
        "started": True,
        "incident_id": incident_id,
        "message": "Network fault injected; recovery action and health verification have started.",
        "recovery_mode": "condition-based",
        "planned_duration_seconds": None,
    }), 202

@app.route("/chaos/<kind>", methods=["POST"])
def chaos_other(kind):
    """Inject API/payment faults with automatic recovery and verification."""
    global _api_fault_started, _api_fault_duration
    if kind == "database":
        return jsonify({
            "started": False,
            "message": "Use /chaos/database for the SQLite connection-failure scenario.",
        }), 409
    if kind not in _chaos:
        return jsonify(error="Unknown chaos scenario."), 404

    with _state_lock:
        if _chaos[kind]:
            return jsonify(error=f"{kind} chaos is already active."), 409

    labels = {"api": "API connection failure", "payment": "Payment failure"}
    try:
        incident_id, started = incident_start(
            labels[kind],
            ("Payroll mutation API made unavailable; monitoring endpoints remain reachable."
             if kind == "api" else "Injected simulated payment-provider failure."),
        )
    except Exception:
        app.logger.exception("Could not persist incident before %s fault", kind)
        return jsonify(error="Could not start fault because incident logging failed."), 500

    with _state_lock:
        # Check again after writing the incident, as concurrent triggers may race.
        if _chaos[kind]:
            try:
                incident_finish(incident_id, started, "Cancelled",
                                "Another fault was already active.",
                                "No second fault was started.")
            except Exception:
                app.logger.exception("Could not close duplicate incident")
            return jsonify(error=f"{kind} chaos is already active."), 409
        _chaos[kind] = True
        set_recovery_stage(kind, "detecting", f"{labels[kind]} injected; recovery worker has registered the failure.", incident_id, now())
        if kind == "api":
            _api_fault_started = time.monotonic()
            _api_fault_duration = None
            _api_fault_observed.clear()
        else:
            _payment_fault_observed.clear()

    def recover():
        global _api_fault_started, _api_fault_duration
        # Keep the detection state visible to the UI before diagnostic probes start.
        threading.Event().wait(1.2)
        def dependency_probe():
            with _state_lock:
                active_now = bool(_chaos.get(kind, False))
            return (not active_now, f"{kind} injected fault remains active" if active_now else f"{kind} fault cleared")
        probe_history = recovery_backoff_probe(dependency_probe, kind)
        set_recovery_stage(kind, "working", f"Applying corrective action for {labels[kind].lower()}.", incident_id)
        with _state_lock:
            _chaos[kind] = False
            if kind == "api":
                _api_fault_started = None
                _api_fault_duration = None

        verified = False
        checks = {"recovery_probes": probe_history}
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
                    # The payment-provider fault is cleared, but failed payment
                    # records remain FAILED until reconciled; never mark them paid
                    # without provider confirmation.
                    health_response = client.get("/health")
                    checks["/health"] = health_response.status_code
                    verified = health_response.status_code == 200
        except Exception as exc:
            checks["verification_error"] = str(exc)

        set_recovery_stage(kind, "verifying", "Checking backend health and confirming the fault flag is cleared.", incident_id)
        threading.Event().wait(1.5)
        try:
            incident_finish(
                incident_id, started,
                "Recovered" if verified else "Unrecovered",
                (f"{labels[kind]} cleared; post-recovery checks passed: {checks}."
                 if verified else f"Fault cleared but post-recovery checks failed: {checks}."),
                ("Automatic recovery cleared the fault and health checks passed."
                 if verified else "Fault was cleared, but health-check verification failed."),
            )
        except Exception:
            app.logger.exception("Could not persist recovery incident for %s", kind)
        if verified:
            set_recovery_stage(kind, "recovered", f"{labels[kind]} recovery verified; backend health is available again.", incident_id)
            if kind == "api":
                process_recovery_queue()
        else:
            set_recovery_failure(kind, f"{labels[kind]} health verification failed; inspect backend logs.")

    threading.Thread(target=recover, daemon=True, name=f"resilpay-{kind}-recovery").start()
    return jsonify(
        started=True,
        incident_id=incident_id,
        message=("Payroll API connection-unavailable fault injected; corrective recovery and health checks have started."
                 if kind == "api" else "Payment-provider fault injected; recovery checks have started."),
        recovery_mode="condition-based",
        planned_duration_seconds=None,
    ), 202


@app.route("/chaos/database", methods=["POST"])
def chaos_database():
    """Inject a real SQLite connection-layer error, then verify recovery."""
    if database_fault_active():
        return jsonify({
            "started": False,
            "message": "A database fault is already active.",
        }), 409

    # Persist the incident while SQLite is still available.
    incident_id, incident_started = incident_start(
        "Database failure",
        "Injected SQLite connection failure; automatic recovery is scheduled.",
    )

    set_recovery_stage("database", "detecting", "SQLite connection fault injected; database failure registered.", incident_id, now())
    started, _duration = activate_database_fault()
    if not started:
        # Another request may have activated the fault between the check and call.
        try:
            incident_finish(
                incident_id, incident_started, "Cancelled",
                "Database fault was already active; no second fault was started.",
                "No additional fault was started.",
            )
        except Exception:
            app.logger.exception("Could not mark duplicate database incident cancelled")
        return jsonify({
            "started": False,
            "message": "A database fault is already active.",
        }), 409

    def recover_database():
        # Keep the detection state visible to the UI before diagnostic probes start.
        threading.Event().wait(1.2)
        def database_probe():
            try:
                probe_conn = get_db()
                try:
                    probe_conn.execute("SELECT 1").fetchone()
                finally:
                    probe_conn.close()
                return True, "SQLite probe succeeded"
            except sqlite3.OperationalError as exc:
                return False, str(exc)
        probe_history = recovery_backoff_probe(database_probe, "database")
        set_recovery_stage("database", "working", "Restoring SQLite access after repeated connection probes.", incident_id)
        # Corrective action: restore access, then verify a real query.
        try:
            deactivate_database_fault()
        except Exception:
            app.logger.exception("Could not deactivate the SQLite fault")

        recovered = False
        verification_message = "Database recovery verification failed."
        try:
            conn = get_db()
            try:
                conn.execute("SELECT 1").fetchone()
                conn.execute("SELECT COUNT(*) FROM employees").fetchone()
                recovered = True
                verification_message = "SQLite connection and employee query succeeded."
            finally:
                conn.close()
        except Exception as exc:
            verification_message = f"SQLite recovery verification failed: {exc}"

        set_recovery_stage("database", "verifying", "Checking SELECT 1 and reading employee count to verify database health.", incident_id)
        threading.Event().wait(1.5)
        try:
            incident_finish(
                incident_id,
                incident_started,
                "Recovered" if recovered else "Unrecovered",
                verification_message + f" Diagnostic probes: {probe_history}.",
                "Database access restored after bounded backoff probes and verified."
                if recovered else "Database access was restored, but verification failed.",
            )
        except Exception:
            app.logger.exception("Could not persist database recovery incident %s", incident_id)
        if recovered:
            set_recovery_stage("database", "recovered", "SQLite queries passed; database recovery is verified.", incident_id)
            process_recovery_queue()
        else:
            set_recovery_failure("database", verification_message)

    threading.Thread(
        target=recover_database,
        daemon=True,
        name="resilpay-database-recovery",
    ).start()

    return jsonify({
        "started": True,
        "incident_id": incident_id,
        "message": "SQLite connection fault injected; database access restoration and verification have started.",
        "recovery_mode": "condition-based",
        "planned_duration_seconds": None,
    }), 202


def make_payment(employee_id, run_id):
    """Create or return a transaction using a stable idempotency key."""
    key = f"resilpay-{run_id}-{employee_id}"
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT * FROM payments WHERE idempotency_key=?", (key,)
        ).fetchone()
        if existing:
            return dict(existing), False

        emp = conn.execute(
            "SELECT * FROM employees WHERE id=?", (employee_id,)
        ).fetchone()
        if not emp:
            raise LookupError("Employee not found.")

        emp = employee_dict(emp)
        amount = emp["net_salary"]
        if amount <= 0:
            raise ValueError("Net payable must be positive.")

        # Do not create a Paid transaction when the simulated provider
        # is unavailable. Record a failed attempt and signal the recovery worker.
        if _chaos["payment"]:
            _payment_fault_observed.set()
        status = "FAILED" if _chaos["payment"] else "PAID"
        message = (
            "Simulated provider failure; no payment confirmed."
            if status == "FAILED"
            else "Simulated provider confirmed payment."
        )

        cur = conn.execute(
            """INSERT INTO payments
               (employee_id, employee_name, amount, idempotency_key,
                status, message, updated_at, payroll_period)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                employee_id, emp["name"], amount, key,
                status, message, now(), run_id,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM payments WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row), True
    finally:
        conn.close()


@app.route("/payments/employee/<int:employee_id>", methods=["POST"])
def pay_employee(employee_id):
    data = request.get_json(silent=True) or {}
    run_id = str(data.get("run_id") or datetime.now().strftime("%Y-%m"))
    run_id = "".join(ch for ch in run_id if ch.isalnum() or ch in "-_")[:80]
    if not run_id:
        return jsonify(error="Invalid payroll run ID."), 400

    try:
        payment, created = make_payment(employee_id, run_id)
    except LookupError as exc:
        return jsonify(error=str(exc)), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    if payment["status"] != "PAID":
        return jsonify(
            error=payment["message"], payment=payment,
            message="Transaction is not paid. Reconcile before retrying."
        ), 503

    return jsonify(
        message="Simulated payment confirmed." if created else
                "Existing transaction returned; no duplicate created.",
        payment=payment,
        duplicate_prevented=not created,
    ), 200


@app.route("/payments/run", methods=["POST"])
def payments_run():
    data = request.get_json(silent=True) or {}
    run_id = str(data.get("run_id") or uuid.uuid4())
    employee_ids = data.get("employee_ids")

    conn = get_db()
    try:
        if employee_ids:
            if not isinstance(employee_ids, list) or not employee_ids:
                return jsonify(error="employee_ids must be a non-empty list."), 400
            placeholders = ",".join("?" for _ in employee_ids)
            rows = conn.execute(
                f"SELECT id FROM employees WHERE id IN ({placeholders})",
                employee_ids,
            ).fetchall()
        else:
            rows = conn.execute("SELECT id FROM employees ORDER BY id").fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        try:
            payment, _ = make_payment(row["id"], run_id)
            results.append(payment)
        except (LookupError, ValueError):
            continue

    failed = sum(p["status"] != "PAID" for p in results)
    return jsonify(
        message="Simulated payroll run finished; inspect individual statuses.",
        run_id=run_id,
        payments=results,
        failed_count=failed,
        paid_count=len(results) - failed,
        real_money_transferred=False,
    ), 200


@app.route("/payments")
def payments():
    conn = get_db()
    try:
        rows = [
            dict(row) for row in conn.execute(
                "SELECT * FROM payments ORDER BY id DESC LIMIT 200"
            )
        ]
        return jsonify(payments=rows)
    finally:
        conn.close()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
