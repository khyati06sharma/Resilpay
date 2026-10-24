
import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BookOpen,
  CalendarDays,
  Check,
  CheckCircle2,
  Clock3,
  Cloud,
  Database,
  CreditCard,
  HeartPulse,
  Home,
  Menu,
  Monitor,
  Network,
  Plus,
  RefreshCw,
  Server,
  Settings,
  ShieldCheck,
  UserRound,
  Users,
  Wifi,
  X,
  Zap,
} from "lucide-react";
import "./App.css";

const API = "/api";
const REFRESH_INTERVAL = 5000;

const chaosTests = [
  {
    id: "network",
    title: "Simulate Network Failure",
    subtitle: "Test external service outage",
    color: "blue",
    icon: Network,
  },
  {
    id: "database",
    title: "Simulate Database Failure",
    subtitle: "Test DB connection issues",
    color: "pink",
    icon: Database,
  },
  {
    id: "api",
    title: "Simulate API Failure",
    subtitle: "Test API timeout/errors",
    color: "orange",
    icon: Activity,
  },
  {
    id: "payment",
    title: "Simulate Payment Failure",
    subtitle: "Test payment gateway issues",
    color: "purple",
    icon: CreditCard,
  },
];

const money = (value) =>
  "₹" +
  Number(value ?? 0).toLocaleString("en-IN", {
    maximumFractionDigits: 2,
  });

function initials(name = "") {
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

function formatDate(value) {
  if (!value) return "Not available";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) return String(value);

  return date.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

async function readResponse(response) {
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(
      data.message ||
        data.error ||
        `Request failed with status ${response.status}`
    );
  }

  return data;
}

function Sidebar({ page, setPage, open, close }) {
  const links = [
    { id: "Dashboard", icon: Home },
    { id: "Employees", icon: UserRound },
    { id: "Incident Timeline", icon: Clock3 },
    { id: "Chaos Testing", icon: Zap },
    { id: "Settings", icon: Settings },
  ];

  return (
    <>
      {open && (
        <button
          className="mobile-overlay"
          onClick={close}
          aria-label="Close menu"
        />
      )}

      <aside className={`sidebar ${open ? "sidebar-open" : ""}`}>
        <div className="brand">
          <div className="brand-shield">
            <ShieldCheck size={43} strokeWidth={1.5} />
          </div>

          <div>
            <div className="brand-name">ResilPay</div>
            <div className="brand-subtitle">SELF-HEALING PAYROLL</div>
          </div>
        </div>

        <nav className="side-nav">
          {links.map(({ id, icon: Icon }) => (
            <button
              key={id}
              className={`nav-item ${page === id ? "active" : ""}`}
              onClick={() => {
                setPage(id);
                close();
              }}
            >
              <Icon size={23} strokeWidth={2.3} />
              <span>{id}</span>
            </button>
          ))}
        </nav>

        <div className="sidebar-quote">
          <p>
            Stronger
            <br />
            Systems
            <br />
            for a Fairer
            <br />
            Tomorrow <span>⌁</span>
          </p>

          <HeartPulse size={21} />

          <div className="book-art">
            <div className="book-glow" />
            <BookOpen size={155} strokeWidth={1} />
            <span className="plant plant-one">❧</span>
            <span className="plant plant-two">✦</span>
          </div>
        </div>
      </aside>
    </>
  );
}

function StatCard({ type, icon: Icon, label, value, subtitle, trend }) {
  return (
    <article className={`stat-card stat-${type}`}>
      <div className="stat-top">
        <div className="stat-icon">
          <Icon size={29} fill="currentColor" strokeWidth={1.7} />
        </div>

        <div className="stat-copy">
          <div className="stat-title">{label}</div>
          <div className="stat-value">{value}</div>
          <div className="stat-subtitle">{subtitle}</div>
        </div>
      </div>

      <span className="change-pill">{trend}</span>
      <div className="stat-wave" />
    </article>
  );
}

function SystemOverview({ health, networkStatus }) {
  const components = [
    { name: "Frontend (React)", icon: Monitor, healthy: true },
    { name: "Backend (Flask)", icon: Server, healthy: health === "healthy" },
    { name: "Database (SQLite)", icon: Database, healthy: health === "healthy" },
    {
      name: "External Service",
      icon: Cloud,
      healthy: networkStatus === "healthy",
    },
  ];

  const allHealthy = components.every((component) => component.healthy);

  return (
    <section className="system-panel">
      <div className="panel-heading system-heading">
        <div className="heading-icon pulse-icon">
          <Activity size={27} />
        </div>

        <div className="heading-copy">
          <h2>System Overview</h2>
          <p>Live status of all services and components</p>
        </div>

        <span
          className={`healthy-pill ${
            allHealthy ? "" : "status-warning"
          }`}
        >
          {allHealthy ? "● All Systems Healthy" : "● Attention Required"}
        </span>
      </div>

      <div className="component-grid">
        {components.map(({ name, icon: Icon, healthy }) => (
          <div className="component-tile" key={name}>
            <div className="component-icon">
              <Icon size={30} />
            </div>

            <strong>{name}</strong>

            <span
              className={`component-status ${
                healthy ? "" : "component-warning"
              }`}
            >
              <i />
              {healthy ? "Healthy" : "Check status"}
            </span>
          </div>
        ))}
      </div>

      <div className="system-wave">
        <svg
          viewBox="0 0 700 60"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <path d="M0 30 C35 10 55 60 105 38 S180 18 220 44 S300 20 350 38 S425 52 470 29 S540 13 590 37 S660 13 700 31" />
          <path d="M0 44 C50 20 70 58 130 46 S220 22 270 47 S365 28 410 43 S500 55 555 30 S645 43 700 18" />
        </svg>

        <span>Resilient today, reliable tomorrow. ♥</span>
      </div>
    </section>
  );
}

function ChaosTesting({ onTest, busy }) {
  return (
    <section className="panel chaos-panel">
      <div className="panel-heading">
        <div className="heading-icon purple-icon">
          <Zap size={27} fill="currentColor" />
        </div>

        <div className="heading-copy">
          <h2>Chaos Testing</h2>
          <p>Deliberately break the system and watch it heal.</p>
        </div>
      </div>

      <div className="chaos-grid">
        {chaosTests.map(({ id, title, subtitle, color, icon: Icon }) => (
          <button
            key={id}
            className={`chaos-button ${color}`}
            disabled={Boolean(busy)}
            onClick={() => onTest(id)}
          >
            <Icon size={25} />

            <span className="chaos-button-copy">
              <strong>{busy === id ? "Running test..." : title}</strong>
              <small>{subtitle}</small>
            </span>

            {busy === id ? (
              <RefreshCw size={16} className="spin" />
            ) : (
              <ArrowRight size={16} />
            )}
          </button>
        ))}
      </div>

      <div className="simulation-note">
        <AlertTriangle size={13} />
        Demonstration mode — no real payments are made.
      </div>
    </section>
  );
}

function EmployeeDirectory({ employees, onAdd, page, loading }) {
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    name: "",
    department: "",
    salary: "",
  });
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  async function submit(event) {
    event.preventDefault();
    setFormError("");
    setSaving(true);

    try {
      await onAdd(form);

      setForm({ name: "", department: "", salary: "" });
      setShowForm(false);
    } catch (error) {
      setFormError(error.message || "Could not add employee.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel employee-panel">
      <div className="panel-heading">
        <div className="heading-icon blue-soft">
          <Users size={22} />
        </div>

        <div className="heading-copy">
          <h2>Employee Directory</h2>
          <p>Manage your team and their salaries.</p>
        </div>

        <span className="count-pill">{employees.length} employees</span>
      </div>

      {page === "Employees" && (
        <div className="employee-page-note">
          Employee records are loaded from your Flask API and SQLite database.
        </div>
      )}

      <div className="employee-table-wrap">
        <table className="employee-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Name</th>
              <th>Department</th>
              <th>Monthly Salary</th>
            </tr>
          </thead>

          <tbody>
            {loading && employees.length === 0 && (
              <tr>
                <td colSpan="4" className="empty-state">
                  Loading employee records...
                </td>
              </tr>
            )}

            {!loading &&
              employees.map((employee, index) => (
                <tr key={employee.id}>
                  <td>{index + 1}</td>

                  <td>
                    <div className="employee-name">
                      <span className={`avatar avatar-${index % 4}`}>
                        {initials(employee.name)}
                      </span>
                      <span>{employee.name}</span>
                    </div>
                  </td>

                  <td>{employee.department}</td>

                  <td className="salary-cell">
                    {money(employee.basic_salary)}
                  </td>
                </tr>
              ))}

            {!loading && employees.length === 0 && (
              <tr>
                <td colSpan="4" className="empty-state">
                  No employee records were returned by the database.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {showForm && (
        <form className="add-form" onSubmit={submit}>
          <label>
            Employee name
            <input
              required
              value={form.name}
              onChange={(event) =>
                setForm({ ...form, name: event.target.value })
              }
              placeholder="Ananya Sharma"
            />
          </label>

          <label>
            Department
            <input
              required
              value={form.department}
              onChange={(event) =>
                setForm({ ...form, department: event.target.value })
              }
              placeholder="Engineering"
            />
          </label>

          <label>
            Monthly salary (₹)
            <input
              required
              type="number"
              min="0.01"
              step="0.01"
              value={form.salary}
              onChange={(event) =>
                setForm({ ...form, salary: event.target.value })
              }
              placeholder="30000"
            />
          </label>

          {formError && <p className="form-error">{formError}</p>}

          <div className="form-actions">
            <button
              className="primary-button"
              type="submit"
              disabled={saving}
            >
              {saving ? "Saving..." : "Save Employee"}
            </button>

            <button
              type="button"
              className="secondary-button"
              onClick={() => {
                setShowForm(false);
                setFormError("");
              }}
              disabled={saving}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {!showForm && (
        <button
          className="primary-button add-employee-button"
          onClick={() => setShowForm(true)}
        >
          <Plus size={17} />
          Add Employee
        </button>
      )}
    </section>
  );
}

function IncidentTimeline({ incidents, onViewAll, showAll = false }) {
  const colors = ["green", "blue", "purple", "orange", "pink"];
  const visibleIncidents = showAll ? incidents : incidents.slice(0, 4);

  return (
    <section className="panel timeline-panel">
      <div className="panel-heading">
        <div className="heading-icon orange-soft">
          <Clock3 size={22} />
        </div>

        <div className="heading-copy">
          <h2>Incident Timeline</h2>
          <p>Every failure, every recovery, logged.</p>
        </div>

        {!showAll && (
          <button className="view-all" onClick={onViewAll}>
            View all <ArrowRight size={12} />
          </button>
        )}
      </div>

      <div className="timeline-list">
        {visibleIncidents.map((incident, index) => {
          const recovered = String(incident.recovery_status || "")
            .toLowerCase()
            .includes("recover");

          return (
            <article
              className="timeline-item"
              key={incident.id ?? `${incident.failure_type}-${index}`}
            >
              <div className={`timeline-icon ${colors[index % colors.length]}`}>
                {recovered ? (
                  <Check size={18} />
                ) : (
                  <AlertTriangle size={17} />
                )}
              </div>

              <div className="timeline-details">
                <div className="incident-title">
                  {incident.failure_type || "System incident"}
                </div>

                <div className="incident-date">
                  Detected: {formatDate(incident.created_at)}
                </div>

                {incident.message && <small>{incident.message}</small>}
              </div>

              <div className="incident-result">
                <span
                  className={`recovered-badge ${
                    recovered ? "" : "not-recovered"
                  }`}
                >
                  {incident.recovery_status || "Recorded"}
                </span>

                <small>
                  Recovery:{" "}
                  {Number(incident.recovery_time || 0).toFixed(2)}s
                </small>
              </div>
            </article>
          );
        })}

        {incidents.length === 0 && (
          <div className="empty-state">
            No incidents recorded yet. Run a chaos test to begin.
          </div>
        )}
      </div>
    </section>
  );
}

function NetworkStatus({ data }) {
  const healthy = data?.status === "healthy";

  return (
    <section className="network-panel">
      <div className="panel-heading">
        <div className="heading-icon network-icon">
          <Network size={23} />
        </div>

        <div className="heading-copy">
          <h2>Network Status</h2>
          <p>External payroll service connection</p>
        </div>
      </div>

      <div className="network-main">
        <div className={`health-ring ${healthy ? "" : "ring-warning"}`}>
          <Wifi size={31} />
          <strong>{healthy ? "Healthy" : data?.status || "Checking"}</strong>
          <small>
            Service {healthy ? "reachable" : "being checked"}
          </small>
        </div>

        <div className="network-metrics">
          <div>
            <span>Attempts</span>
            <strong>{data?.attempts ?? 0}</strong>
          </div>

          <div>
            <span>Detected</span>
            <strong>{formatDate(data?.detected_at)}</strong>
          </div>

          <div>
            <span>Recovered</span>
            <strong>{formatDate(data?.recovered_at)}</strong>
          </div>

          <div>
            <span>Recovery time</span>
            <strong>
              {Number(data?.recovery_time || 0).toFixed(2)} seconds
            </strong>
          </div>
        </div>
      </div>

      <div className={`network-notice ${healthy ? "" : "notice-warning"}`}>
        {healthy ? (
          <CheckCircle2 size={26} />
        ) : (
          <Activity size={26} />
        )}

        <div>
          <strong>
            {data?.message || "Waiting for the network status endpoint."}
          </strong>

          <p>
            {healthy
              ? "The service is reachable. When an outage is injected, recovery will be monitored here."
              : "The service may be unavailable or recovering. Check the latest status."}
          </p>
        </div>
      </div>

      <div className="network-footnote">
        Status is based on the backend network-status endpoint.
      </div>
    </section>
  );
}

function OtherPage({
  page,
  employees,
  incidents,
  health,
  loading,
  onRefresh,
  onAdd,
}) {
  if (page === "Employees") {
    return (
      <div className="page-content">
        <div className="page-title-row">
          <div>
            <h2>Employees</h2>
            <p>Manage employee records and monthly salaries.</p>
          </div>

          <button
            className="secondary-button"
            onClick={onRefresh}
            disabled={loading}
          >
            <RefreshCw size={14} />
            Refresh
          </button>
        </div>

        <EmployeeDirectory
          employees={employees}
          onAdd={onAdd}
          page={page}
          loading={loading}
        />
      </div>
    );
  }

  if (page === "Incident Timeline") {
    return (
      <div className="page-content">
        <div className="page-title-row">
          <div>
            <h2>Incident Timeline</h2>
            <p>Failure and recovery records from your backend.</p>
          </div>

          <button className="secondary-button" onClick={onRefresh}>
            <RefreshCw size={14} />
            Refresh
          </button>
        </div>

        <IncidentTimeline incidents={incidents} showAll />
      </div>
    );
  }

  if (page === "Settings") {
    return (
      <div className="page-content">
        <div className="page-title-row">
          <div>
            <h2>Settings</h2>
            <p>ResilPay service configuration and system information.</p>
          </div>

          <button className="secondary-button" onClick={onRefresh}>
            <RefreshCw size={14} />
            Check connection
          </button>
        </div>

        <div className="settings-grid">
          <div className="setting-item">
            <strong>Backend connection</strong>
            <span>{health}</span>
          </div>

          <div className="setting-item">
            <strong>Database</strong>
            <span>SQLite · Local storage</span>
          </div>

          <div className="setting-item">
            <strong>Health-check interval</strong>
            <span>{REFRESH_INTERVAL / 1000} seconds</span>
          </div>

          <div className="setting-item">
            <strong>Payment mode</strong>
            <span>Simulation only — no real money</span>
          </div>
        </div>
      </div>
    );
  }

  return null;
}

export default function App() {
  // No hardcoded employee data or payroll total.
  // These values are loaded from Flask and the existing SQLite database.
  const [employees, setEmployees] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [health, setHealth] = useState("checking");
  const [totalSalary, setTotalSalary] = useState(0);
  const [network, setNetwork] = useState({
    status: "checking",
    attempts: 0,
  });

  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [page, setPage] = useState("Dashboard");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [notice, setNotice] = useState("");

  const loadDashboard = useCallback(async () => {
    try {
      const [healthResponse, dashboardResponse] = await Promise.all([
        fetch(`${API}/health`),
        fetch(`${API}/dashboard-data`),
      ]);

      const healthData = await readResponse(healthResponse);
      const dashboardData = await readResponse(dashboardResponse);

      setHealth(String(healthData.status || "unknown").toLowerCase());

      setEmployees(
        Array.isArray(dashboardData.employees)
          ? dashboardData.employees
          : []
      );

      setIncidents(
        Array.isArray(dashboardData.incidents)
          ? dashboardData.incidents
          : []
      );

      setTotalSalary(Number(dashboardData.total_salary ?? 0));
      setError("");
    } catch (err) {
      setHealth("unavailable");
      setError(
        err.message ||
          "Cannot connect to Flask. Check that the backend and database are running."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  const loadNetwork = useCallback(async () => {
    try {
      const response = await fetch(`${API}/network-status`);
      const data = await readResponse(response);
      setNetwork(data);
    } catch {
      setNetwork((previous) => ({
        ...previous,
        status: "unavailable",
        message: "Cannot retrieve the network status from Flask.",
      }));
    }
  }, []);

  useEffect(() => {
    loadDashboard();
    loadNetwork();

    const timer = window.setInterval(() => {
      loadDashboard();
      loadNetwork();
    }, REFRESH_INTERVAL);

    return () => window.clearInterval(timer);
  }, [loadDashboard, loadNetwork]);

  async function addEmployee(form) {
    const response = await fetch(`${API}/add-employee`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: form.name.trim(),
        department: form.department.trim(),
        salary: Number(form.salary),
      }),
    });

    const result = await readResponse(response);

    await loadDashboard();

    if (result.message) {
      setNotice(result.message);
    } else {
      setNotice("Employee saved successfully.");
    }
  }

  async function runChaos(id) {
    const routes = {
      network: `${API}/chaos/network`,
      database: `${API}/chaos/database`,
      api: `${API}/chaos/api`,
      payment: `${API}/chaos/payment`,
    };

    setBusy(id);
    setNotice("");

    try {
      const response = await fetch(routes[id], {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });

      const result = await readResponse(response);

      setNotice(
        result.message ||
          `${id} failure test started. Check the incident timeline for updates.`
      );

      await Promise.all([loadNetwork(), loadDashboard()]);
    } catch (err) {
      setNotice(
        err.message ||
          `Could not run the ${id} test. Check whether its Flask endpoint exists.`
      );
    } finally {
      setBusy("");

      // Refresh again after the healing process has had time to run.
      window.setTimeout(() => {
        loadDashboard();
        loadNetwork();
      }, 1500);
    }
  }

  const recoveredCount = incidents.filter((incident) =>
    String(incident.recovery_status || "")
      .toLowerCase()
      .includes("recover")
  ).length;

  const date = new Date();

  return (
    <div className="app-shell">
      <Sidebar
        page={page}
        setPage={setPage}
        open={mobileOpen}
        close={() => setMobileOpen(false)}
      />

      <main className="main-content">
        <header className="top-header">
          <button
            className="mobile-menu"
            onClick={() => setMobileOpen(true)}
            aria-label="Open navigation"
          >
            <Menu size={21} />
          </button>

          <div className="greeting">
            <h1>
              Good {date.getHours() < 12
                ? "Morning"
                : date.getHours() < 17
                  ? "Afternoon"
                  : "Evening"}
              ! <span className="sun">☀️</span>
            </h1>

            <p>
              Your payroll system is running smoothly. Here's what's happening.
            </p>
          </div>

          <div className="header-meta">
            <div className="connection-pill">
              <span
                className={`live-dot ${
                  health !== "healthy" ? "dot-warning" : ""
                }`}
              />

              {health === "healthy"
                ? "Backend Connected"
                : health === "unavailable"
                  ? "Backend Offline"
                  : "Checking Backend"}

              <Wifi size={17} />
            </div>

            <div className="meta-item">
              <CalendarDays size={17} />
              <span>
                {date.toLocaleDateString("en-IN", {
                  day: "numeric",
                  month: "short",
                  year: "numeric",
                })}
              </span>
            </div>

            <div className="meta-item">
              <Clock3 size={17} />
              <span>
                {date.toLocaleTimeString("en-IN", {
                  hour: "2-digit",
                  minute: "2-digit",
                  hour12: false,
                })}
              </span>
            </div>

            <div className="handwriting">
              Self-healing
              <br />
              in action ♡
            </div>
          </div>
        </header>

        {error && (
          <div className="error-banner">
            <AlertTriangle size={17} />
            {error}
          </div>
        )}

        {notice && (
          <div className="toast">
            <Activity size={18} />
            <span>{notice}</span>

            <button
              onClick={() => setNotice("")}
              aria-label="Dismiss notification"
            >
              <X size={16} />
            </button>
          </div>
        )}

        {page === "Dashboard" || page === "Chaos Testing" ? (
          <>
            <section className="stats-grid">
              <StatCard
                type="blue"
                icon={Users}
                label="Total Employees"
                value={loading ? "—" : employees.length}
                subtitle="Registered employees"
                trend="Live database"
              />

              <StatCard
                type="green"
                icon={CreditCard}
                label="Monthly Payroll"
                value={loading ? "—" : money(totalSalary)}
                subtitle="Total basic salaries"
                trend="Live database"
              />

              <StatCard
                type="mint"
                icon={HeartPulse}
                label="System Health"
                value={
                  health === "healthy"
                    ? "Healthy"
                    : health === "unavailable"
                      ? "Offline"
                      : "Checking"
                }
                subtitle="Live backend status"
                trend={
                  health === "healthy" ? "Operational" : "Monitoring"
                }
              />

              <StatCard
                type="pink"
                icon={AlertTriangle}
                label="Recent Incidents"
                value={incidents.length}
                subtitle="Incidents returned by Flask"
                trend={`${recoveredCount} recovered`}
              />
            </section>

            <section className="middle-grid">
              <SystemOverview
                health={health}
                networkStatus={network.status}
              />

              <ChaosTesting onTest={runChaos} busy={busy} />
            </section>

            <section className="bottom-grid">
              <EmployeeDirectory
                employees={employees}
                onAdd={addEmployee}
                page={page}
                loading={loading}
              />

              <IncidentTimeline
                incidents={incidents}
                onViewAll={() => setPage("Incident Timeline")}
              />

              <NetworkStatus data={network} />
            </section>

            <footer className="dashboard-footnote">
              ResilPay · Self-Healing Payroll Demonstration · Simulated
              failures only · No real payments
            </footer>
          </>
        ) : (
          <OtherPage
            page={page}
            employees={employees}
            incidents={incidents}
            health={health}
            loading={loading}
            onRefresh={() => {
              loadDashboard();
              loadNetwork();
            }}
            onAdd={addEmployee}
          />
        )}
      </main>
    </div>
  );
}
