import sqlite3
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "resilpay.db"

# Thread-safe, in-process fault injection. Existing connections are unaffected;
# new connections fail while the fault is active.
_fault_lock = threading.Lock()
_database_fault_active = False
_database_fault_started = None
_database_fault_duration = None
_database_fault_observed = threading.Event()


def wait_for_database_fault_observed():
    """Block the recovery worker until a real database operation hits the fault."""
    _database_fault_observed.wait()


def activate_database_fault(duration=None):
    """Inject a SQLite connection failure until the recovery action clears it.

    The optional argument is accepted for backwards compatibility with older
    callers, but is intentionally ignored: recovery is condition-based, not
    controlled by a fixed duration.
    """
    global _database_fault_active, _database_fault_started, _database_fault_duration

    with _fault_lock:
        if _database_fault_active:
            return False, None
        _database_fault_active = True
        _database_fault_started = time.monotonic()
        _database_fault_duration = None
        _database_fault_observed.clear()
        return True, None


def deactivate_database_fault():
    """Restore database access and return the elapsed fault duration."""
    global _database_fault_active, _database_fault_started, _database_fault_duration

    with _fault_lock:
        started = _database_fault_started
        elapsed = time.monotonic() - started if started is not None else 0.0
        _database_fault_active = False
        _database_fault_started = None
        _database_fault_duration = None
        return round(elapsed, 2)


def get_database_fault_status():
    """Read fault state without opening a database connection."""
    with _fault_lock:
        active = _database_fault_active
        started = _database_fault_started
        duration = _database_fault_duration

    return {
        "active": active,
        "status": "Unavailable" if active else "Healthy",
        "elapsed_seconds": (
            round(time.monotonic() - started, 2)
            if active and started is not None else None
        ),
        "planned_duration_seconds": duration,
    }


def get_db():
    """Open SQLite or raise a genuine SQLite operational exception during fault injection."""
    with _fault_lock:
        if _database_fault_active:
            _database_fault_observed.set()
            raise sqlite3.OperationalError(
                "Injected database connection failure: "
                "SQLite access is temporarily unavailable."
            )

    connection = sqlite3.connect(str(DATABASE), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def init_db():
    """Create missing tables and migrate columns without deleting existing data."""
    connection = get_db()
    try:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            department TEXT NOT NULL,
            basic_salary REAL NOT NULL CHECK(basic_salary > 0)
        );
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            failure_type TEXT NOT NULL,
            message TEXT NOT NULL,
            recovery_status TEXT NOT NULL,
            recovery_time REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            detected_at TEXT,
            diagnosed_at TEXT,
            action_taken TEXT,
            recovered_at TEXT,
            detection_time REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            employee_name TEXT NOT NULL,
            amount REAL NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            FOREIGN KEY(employee_id) REFERENCES employees(id)
        );
        """)

        migrations = {
            "employees": [
                ("designation", "TEXT NOT NULL DEFAULT 'Employee'"),
                ("joining_date", "TEXT"),
                ("hra", "REAL NOT NULL DEFAULT 0"),
                ("bonus", "REAL NOT NULL DEFAULT 0"),
                ("allowances", "REAL NOT NULL DEFAULT 0"),
                ("deductions", "REAL NOT NULL DEFAULT 0"),
            ],
            "incidents": [
                ("detected_at", "TEXT"),
                ("diagnosed_at", "TEXT"),
                ("action_taken", "TEXT"),
                ("recovered_at", "TEXT"),
                ("detection_time", "REAL DEFAULT 0"),
            ],
            "payments": [
                ("payroll_period", "TEXT"),
            ],
        }

        for table, columns in migrations.items():
            existing = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            for column, definition in columns:
                if column not in existing:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                    )

        count = connection.execute(
            "SELECT COUNT(*) FROM employees"
        ).fetchone()[0]

        # Seed only an empty employee table. Existing records are preserved.
        if count == 0:
            connection.executemany(
                """INSERT INTO employees (name, department, basic_salary)
                   VALUES (?, ?, ?)""",
                [
                    ("Aarav Sharma", "Engineering", 30000),
                    ("Priya Verma", "HR", 25000),
                    ("Rohan Singh", "Finance", 28000),
                ],
            )

        connection.commit()
    finally:
        connection.close()
