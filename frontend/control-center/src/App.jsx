
import { useCallback, useEffect, useMemo, useState } from "react";
import "./App.css";

const API = "/api";

const initialServices = [
  { id: "api", name: "Application API", detail: "Dashboard & employee requests" },
  { id: "database", name: "Database", detail: "Employee and payroll records" },
  { id: "payment", name: "Payment Engine", detail: "Simulated payroll processing" },
  { id: "network", name: "Network", detail: "Service connectivity" },
];

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(data.message || data.error || `Request failed (${response.status})`);
  }

  return data;
}

function formatMoney(value) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(Number(value) || 0);
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString("en-IN", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
}

function statusOf(value) {
  const text = String(value || "").toLowerCase();
  if (["healthy", "ok", "online", "recovered", "resolved", "paid", "success"].some((x) => text.includes(x))) return "healthy";
  if (["failed", "down", "unavailable", "critical", "error"].some((x) => text.includes(x))) return "failed";
  if (["degraded", "recovering", "pending", "detected", "diagnosed", "action"].some((x) => text.includes(x))) return "warning";
  return "neutral";
}

function Badge({ children, status = "neutral" }) {
  return <span className={`badge badge-${status}`}>{children || "Unknown"}</span>;
}

function Metric({ label, value, note, icon, tone = "" }) {
  return (
    <article className={`metric-card ${tone}`}>
      <div className="metric-top">
        <span>{label}</span>
        <span className="metric-icon">{icon}</span>
      </div>
      <div className="metric-value">{value}</div>
      <div className="metric-note">{note}</div>
    </article>
  );
}

function App() {
  const [collapsed, setCollapsed] = useState(false);
  const [page, setPage] = useState("Overview");
  const [dashboard, setDashboard] = useState({});
  const [health, setHealth] = useState({});
  const [network, setNetwork] = useState({});
  const [databaseStatus, setDatabaseStatus] = useState({});
  const [apiStatus, setApiStatus] = useState({});
  const [queueState, setQueueState] = useState({ summary: {}, items: [] });
  const [cacheState, setCacheState] = useState({});
  const [recoveryState, setRecoveryState] = useState({ recoveries: {}, stages: [] });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [busy, setBusy] = useState("");
  const [lastUpdated, setLastUpdated] = useState(null);

  const loadData = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    else setRefreshing(true);

    try {
      const results = await Promise.allSettled([
        request("/dashboard-data"),
        request("/health"),
        request("/network-status"),
        request("/database-status"),
        request("/api-status"),
        request("/recovery-queue"),
        request("/cache-status"),
      ]);

      if (results[0].status === "fulfilled") {
        setDashboard(results[0].value || {});
        setError("");
      } else {
        setError(results[0].reason?.message || "Dashboard API is unavailable.");
      }

      if (results[1].status === "fulfilled") setHealth(results[1].value || {});
      else if (results[1].reason?.message) {
        // Health may intentionally return HTTP 503 while a fault is active.
        // /database-status and /network-status remain available for observability.
      }
      if (results[2].status === "fulfilled") setNetwork(results[2].value || {});
      if (results[3].status === "fulfilled") setDatabaseStatus(results[3].value || {});
      if (results[4].status === "fulfilled") setApiStatus(results[4].value || {});
      if (results[5].status === "fulfilled") setQueueState(results[5].value || { summary: {}, items: [] });
      if (results[6].status === "fulfilled") setCacheState(results[6].value || {});
      request("/recovery-status").then((value) => setRecoveryState(value || { recoveries: {}, stages: [] })).catch(() => {});

      setLastUpdated(new Date());
    } catch (err) {
      setError(err.message || "Unable to load Control Center data.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    const timer = setInterval(() => loadData(true), 1500);
    return () => clearInterval(timer);
  }, [loadData]);

  const employees = dashboard.employees || [];
  const incidents = dashboard.incidents || [];
  const payments = dashboard.payments || [];

  const totalSalary = useMemo(
    () =>
      Number(
        dashboard.total_salary ??
          employees.reduce((sum, employee) => sum + Number(employee.net_salary ?? employee.basic_salary ?? employee.salary ?? 0), 0)
      ),
    [dashboard.total_salary, employees]
  );

  const serviceList = useMemo(() => {
    const healthServices = health.services || health.checks || {};
    const networkState = network.status || network.state || network.health || "";
    return initialServices.map((service) => {
      let raw;

      if (service.id === "network") {
        raw = networkState || healthServices.network || health.network || "Unknown";
      } else if (service.id === "database") {
        raw = databaseStatus.status || healthServices.database || health.database || "Unknown";
      } else if (service.id === "api") {
        raw = apiStatus.status || healthServices.api || health.api || "Unknown";
      } else {
        raw =
          healthServices[service.id] ??
          health[service.id] ??
          healthServices[service.id === "api" ? "application" : service.id] ??
          "Unknown";
      }

      if (typeof raw === "object" && raw !== null) {
        raw = raw.status || raw.health || (raw.healthy ? "Healthy" : "Unknown");
      }

      return { ...service, status: raw };
    });
  }, [health, network, databaseStatus, apiStatus]);

  const healthyCount = serviceList.filter((service) => statusOf(service.status) === "healthy").length;
  const activeIncidents = incidents.filter((incident) => {
    const status = String(incident.recovery_status || incident.status || "").toLowerCase();
    return !["recovered", "resolved", "closed"].includes(status);
  }).length;

  const recoveredIncidents = incidents.filter((incident) =>
    ["recovered", "resolved", "closed"].includes(
      String(incident.recovery_status || incident.status || "").toLowerCase()
    )
  );

  const recoveryTimes = recoveredIncidents
    .map((incident) => Number(incident.recovery_time ?? incident.recovery_time_seconds))
    .filter((time) => Number.isFinite(time) && time >= 0);

  const averageRecovery = recoveryTimes.length
    ? `${(recoveryTimes.reduce((sum, time) => sum + time, 0) / recoveryTimes.length).toFixed(1)}s`
    : "—";

  async function runChaos(kind) {
    setBusy(kind);
    setActionMessage("");
    try {
      const path = kind === "network" ? "/chaos/network" : `/chaos/${kind}`;
      const result = await request(path, { method: "POST" });
      setActionMessage(result.message || `${kind} chaos test triggered.`);
      await loadData(true);
    } catch (err) {
      setActionMessage(err.message || `Could not trigger ${kind} test.`);
      await loadData(true);
    } finally {
      setBusy("");
    }
  }

  async function refreshNow() {
    await loadData(true);
  }

  const navItems = [
    { label: "Overview", icon: "◫" },
    { label: "Service Health", icon: "◎" },
    { label: "Incident Timeline", icon: "◷" },
    { label: "Chaos Lab", icon: "⚡" },
    { label: "Payroll Snapshot", icon: "▤" },
  ];

  return (
    <div className={`resilpay-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">R<span>.</span></div>
          {!collapsed && (
            <div className="brand-copy">
              <strong>ResilPay</strong>
              <small>CONTROL CENTER</small>
            </div>
          )}
        </div>

        <button
          className="collapse-button"
          onClick={() => setCollapsed((value) => !value)}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? "›" : "‹"}
        </button>

        {!collapsed && <div className="nav-caption">WORKSPACE</div>}
        <nav className="side-nav">
          {navItems.map((item) => (
            <button
              key={item.label}
              className={`nav-item ${page === item.label ? "active" : ""}`}
              onClick={() => setPage(item.label)}
              title={collapsed ? item.label : undefined}
            >
              <span className="nav-icon">{item.icon}</span>
              {!collapsed && <span>{item.label}</span>}
              {!collapsed && item.label === "Incident Timeline" && activeIncidents > 0 && (
                <span className="nav-count">{activeIncidents}</span>
              )}
            </button>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <div className="sidebar-status">
            <span className="live-dot" />
            {!collapsed && (
              <div>
                <strong>Monitoring active</strong>
                <small>Auto-refresh every 5 sec</small>
              </div>
            )}
          </div>
          {!collapsed && (
            <div className="sidebar-footer">
              <span className="avatar">RP</span>
              <div>
                <strong>ResilPay Admin</strong>
                <small>System operator</small>
              </div>
            </div>
          )}
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div className="breadcrumbs">
            <span>ResilPay</span><span className="crumb-separator">/</span><strong>{page}</strong>
          </div>
          <div className="topbar-actions">
            <div className="system-pill"><span className="live-dot" /> Live monitoring</div>
            <button className="icon-button" onClick={refreshNow} disabled={refreshing} title="Refresh data">
              {refreshing ? "…" : "↻"}
            </button>
          </div>
        </header>

        <div className="page-content">
          <section className="welcome-row">
            <div>
              <div className="eyebrow">RESILIENCE OPERATIONS <span /></div>
              <h1>{page === "Overview" ? "System overview" : page}</h1>
              <p className="page-subtitle">
                Monitor service health, simulate failures, and track self-healing performance.
              </p>
            </div>
            <div className="updated-label">
              <span className="updated-dot" />
              Updated {lastUpdated ? lastUpdated.toLocaleTimeString("en-IN") : "—"}
            </div>
          </section>

          {error && (
            <div className="notice notice-error">
              <strong>Connection issue</strong>
              <span>{error}</span>
              <button onClick={refreshNow}>Retry</button>
            </div>
          )}
          <section className="metric-grid" style={{marginBottom:16}}>
            <Metric label="Recovery queue pending" value={queueState.summary?.pending ?? "—"} note={`${queueState.summary?.completed ?? 0} completed · ${queueState.summary?.failed ?? 0} failed`} icon="⇄" />
            <Metric label="Read cache entries" value={cacheState.items ?? "—"} note="Last-known-good read snapshots" icon="▤" />
          </section>
          {queueState.items?.length > 0 && (
            <section className="panel" style={{marginBottom:16}}>
              <div className="panel-heading"><div><h2>Recovery queue</h2><p>Queued writes are not considered saved until confirmed.</p></div></div>
              <div className="table-wrap"><table><thead><tr><th>Operation</th><th>Status</th><th>Updated</th><th>Result</th></tr></thead><tbody>
                {queueState.items.slice(0,8).map(item => <tr key={item.queue_id}><td>{item.method} {item.path}</td><td><Badge status={statusOf(item.status)}>{item.status}</Badge></td><td>{formatDate(item.updated_at)}</td><td>{item.result?.message || (item.status === "Queued" ? "Waiting for recovery" : "—")}</td></tr>)}
              </tbody></table></div>
            </section>
          )}
          {actionMessage && (
            <div className="notice notice-info">
              <span className="notice-symbol">i</span>
              <span>{actionMessage}</span>
              <button onClick={() => setActionMessage("")}>Dismiss</button>
            </div>
          )}

          {(page === "Overview" || page === "Service Health") && (
            <>
              <section className="section-heading">
                <div><h2>Operational summary</h2><p>A live snapshot of your payroll resilience.</p></div>
                <Badge status={healthyCount === serviceList.length ? "healthy" : "warning"}>
                  {healthyCount}/{serviceList.length} services healthy
                </Badge>
              </section>

              <div className="metrics-grid">
                <Metric label="Services healthy" value={`${healthyCount}/${serviceList.length}`} note="Across monitored components" icon="◎" />
                <Metric label="Active incidents" value={activeIncidents} note="Requiring attention" icon="!" tone={activeIncidents ? "metric-alert" : ""} />
                <Metric label="Avg. recovery time" value={averageRecovery} note="From recorded recoveries" icon="↻" />
                <Metric label="Employees" value={employees.length} note="Currently registered" icon="♙" />
              </div>

              <section className="content-grid">
                <article className="panel service-panel">
                  <div className="panel-heading">
                    <div><h2>Service health</h2><p>Component status checks</p></div>
                    <span className="tiny-label">LIVE</span>
                  </div>
                  <div className="service-list">
                    {serviceList.map((service) => (
                      <div className="service-row" key={service.id}>
                        <div className={`service-symbol symbol-${service.id}`}>
                          {service.id === "api" ? "A" : service.id === "database" ? "D" : service.id === "payment" ? "₹" : "N"}
                        </div>
                        <div className="service-copy">
                          <strong>{service.name}</strong>
                          <small>{service.detail}</small>
                        </div>
                        <Badge status={statusOf(service.status)}>{String(service.status)}</Badge>
                      </div>
                    ))}
                  </div>
                  <button className="text-action" onClick={() => setPage("Service Health")}>
                    View service status <span>→</span>
                  </button>
                </article>

                <article className="panel resilience-panel">
                  <div className="panel-heading">
                    <div><h2>Resilience at a glance</h2><p>Recovery and incident indicators</p></div>
                    <span className="panel-icon">✳</span>
                  </div>
                  <div className="resilience-score">
                    <div className="score-number">{incidents.length ? Math.round((recoveredIncidents.length / incidents.length) * 100) : 100}<small>%</small></div>
                    <div><strong>Incident recovery rate</strong><p>Based on recorded incident statuses</p></div>
                  </div>
                  <div className="progress-track">
                    <div className="progress-fill" style={{ width: `${incidents.length ? Math.round((recoveredIncidents.length / incidents.length) * 100) : 100}%` }} />
                  </div>
                  <div className="mini-stats">
                    <div><span>Incidents recorded</span><strong>{incidents.length}</strong></div>
                    <div><span>Recovered</span><strong>{recoveredIncidents.length}</strong></div>
                    <div><span>Payroll total</span><strong>{formatMoney(totalSalary)}</strong></div>
                  </div>
                  <p className="disclaimer">Recovery rate reflects the statuses returned by the backend; it is not an uptime guarantee.</p>
                </article>
              </section>

              <section className="panel chaos-panel">
                <div className="panel-heading">
                  <div><h2>Chaos testing</h2><p>Trigger controlled failures and observe recovery behavior.</p></div>
                  <Badge status="warning">Simulation mode</Badge>
                </div>
                <div className="chaos-grid">
                  {[
                    { id: "database", title: "Database failure", detail: "Simulate database unavailability", icon: "D" },
                    { id: "api", title: "API failure", detail: "Simulate dashboard API disruption", icon: "A" },
                    { id: "payment", title: "Payment failure", detail: "Simulate a payroll payment issue", icon: "₹" },
                    { id: "network", title: "Network outage", detail: "Trigger a service connectivity outage", icon: "N" },
                  ].map((test) => (
                    <div className="chaos-card" key={test.id}>
                      <div className={`chaos-icon chaos-${test.id}`}>{test.icon}</div>
                      <div className="chaos-copy"><strong>{test.title}</strong><p>{test.detail}</p></div>
                      <button
                        className="run-button"
                        disabled={Boolean(busy)}
                        onClick={() => runChaos(test.id)}
                      >
                        {busy === test.id ? "Running…" : "Run test"} <span>↗</span>
                      </button>
                    </div>
                  ))}
                </div>
                <p className="safety-note"><span>ⓘ</span> These controls are intended for your local demo environment. Use simulated payroll only.</p>
              </section>

              <section className="panel timeline-panel recovery-lifecycle-panel">
                <div className="panel-heading">
                  <div><h2>Live recovery lifecycle</h2><p>Stages update from the backend while each recovery worker runs.</p></div>
                  <Badge status={Object.values(recoveryState.recoveries || {}).some((r) => r.active) ? "warning" : "neutral"}>
                    {Object.values(recoveryState.recoveries || {}).some((r) => r.active) ? "Recovery in progress" : "Waiting for a failure test"}
                  </Badge>
                </div>
                <div className="chaos-grid">
                  {[
                    { id: "network", title: "Network" }, { id: "api", title: "API" },
                    { id: "database", title: "Database" }, { id: "payment", title: "Payment" },
                  ].map((item) => {
                    const recovery = (recoveryState.recoveries || {})[item.id];
                    const currentIndex = recovery ? (recovery.stage === "failed" ? -1 : Number(recovery.stage_index ?? 0)) : -2;
                    const labels = ["Detecting error", "Analyzing issue", "Working on recovery", "Verifying health", "Recovered"];
                    return (
                      <article className="chaos-card recovery-stage-card" key={item.id} style={{ display: "block", minWidth: 0 }}>
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                          <strong>{item.title} recovery</strong>
                          <Badge status={recovery?.stage === "recovered" ? "healthy" : recovery?.stage === "failed" ? "failed" : recovery?.active ? "warning" : "neutral"}>
                            {recovery?.stage_label || "Idle"}
                          </Badge>
                        </div>
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(5,minmax(0,1fr))", gap: 5, margin: "14px 0 10px" }}>
                          {labels.map((label, index) => {
                            const complete = currentIndex >= index;
                            const active = currentIndex === index && recovery?.active;
                            return <div key={label} title={label} style={{ height: 6, borderRadius: 99, background: complete ? "#8d3048" : "var(--border, #e5dfe1)", opacity: active ? 1 : complete ? 0.85 : 0.5, boxShadow: active ? "0 0 0 2px rgba(141,48,72,.15)" : "none" }} />;
                          })}
                        </div>
                        <p style={{ minHeight: 40, margin: 0, fontSize: 12, lineHeight: 1.5, color: "var(--muted, #756a6d)" }}>
                          {recovery?.message || "Run this failure in Chaos Lab to watch each recovery stage update live."}
                        </p>
                        {recovery?.started_at && <small style={{ display: "block", marginTop: 8, opacity: 0.7 }}>Started: {formatDate(recovery.started_at)}</small>}
                      </article>
                    );
                  })}
                </div>
              </section>

              <section className="panel timeline-panel">
                <div className="panel-heading">
                  <div><h2>Recent incidents</h2><p>Failure detection and recovery lifecycle</p></div>
                  <button className="text-action" onClick={() => setPage("Incident Timeline")}>View timeline →</button>
                </div>
                <IncidentTable incidents={incidents.slice(0, 5)} />
              </section>
            </>
          )}

          {page === "Incident Timeline" && (
            <section className="panel timeline-panel full-panel">
              <div className="panel-heading">
                <div><h2>Incident lifecycle</h2><p>Detected → Diagnosed → Action taken → Recovered</p></div>
                <Badge status="neutral">{incidents.length} records</Badge>
              </div>
              <IncidentTable incidents={incidents} expanded />
            </section>
          )}

          {page === "Chaos Lab" && (
            <section className="panel full-panel">
              <div className="panel-heading">
                <div><h2>Controlled failure lab</h2><p>Run one simulation at a time and observe the resulting incident records.</p></div>
                <Badge status="warning">Local simulation</Badge>
              </div>
              <div className="chaos-grid chaos-grid-large">
                {[
                  { id: "database", title: "Database failure", detail: "Test database health checks and fallback behavior", icon: "D" },
                  { id: "api", title: "API failure", detail: "Test how the dashboard responds to API disruption", icon: "A" },
                  { id: "payment", title: "Payment failure", detail: "Test payment failure handling and recovery", icon: "₹" },
                  { id: "network", title: "Network outage", detail: "Test network monitoring and service recovery", icon: "N" },
                ].map((test) => (
                  <div className="chaos-card" key={test.id}>
                    <div className={`chaos-icon chaos-${test.id}`}>{test.icon}</div>
                    <div className="chaos-copy"><strong>{test.title}</strong><p>{test.detail}</p></div>
                    <button className="run-button" disabled={Boolean(busy)} onClick={() => runChaos(test.id)}>
                      {busy === test.id ? "Running…" : "Run test"} <span>↗</span>
                    </button>
                  </div>
                ))}
              </div>
              <section className="panel timeline-panel recovery-lifecycle-panel" style={{ marginTop: 20 }}>
                <div className="panel-heading">
                  <div><h2>Live recovery stages</h2><p>Updates automatically as the backend diagnoses, repairs, and verifies each fault.</p></div>
                  <Badge status={Object.values(recoveryState.recoveries || {}).some((r) => r.active) ? "warning" : "neutral"}>
                    {Object.values(recoveryState.recoveries || {}).some((r) => r.active) ? "Recovery in progress" : "Ready"}
                  </Badge>
                </div>
                <div className="chaos-grid">
                  {[{ id: "network", title: "Network" }, { id: "api", title: "API" }, { id: "database", title: "Database" }, { id: "payment", title: "Payment" }].map((item) => {
                    const recovery = (recoveryState.recoveries || {})[item.id];
                    const currentIndex = recovery ? (recovery.stage === "failed" ? -1 : Number(recovery.stage_index ?? 0)) : -2;
                    const labels = ["Detecting error", "Analyzing issue", "Working on recovery", "Verifying health", "Recovered"];
                    return <article className="chaos-card recovery-stage-card" key={item.id} style={{ display: "block", minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}><strong>{item.title}</strong><Badge status={recovery?.stage === "recovered" ? "healthy" : recovery?.stage === "failed" ? "failed" : recovery?.active ? "warning" : "neutral"}>{recovery?.stage_label || "Idle"}</Badge></div>
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(5,minmax(0,1fr))", gap: 5, margin: "14px 0 10px" }}>{labels.map((label, index) => <div key={label} title={label} style={{ height: 6, borderRadius: 99, background: currentIndex >= index ? "#8d3048" : "var(--border, #e5dfe1)", opacity: currentIndex >= index ? 0.85 : 0.5 }} />)}</div>
                      <p style={{ minHeight: 40, margin: 0, fontSize: 12, lineHeight: 1.5, color: "var(--muted, #756a6d)" }}>{recovery?.message || "Run this test to watch its recovery stages."}</p>
                    </article>;
                  })}
                </div>
              </section>
              <div className="lab-callout"><strong>Demo checklist</strong><p>Record the incident creation time, recovery time, status transitions, and whether the application returned to normal after the simulation.</p></div>
            </section>
          )}

          {page === "Payroll Snapshot" && (
            <>
              <div className="metrics-grid">
                <Metric label="Employees" value={employees.length} note="Registered employees" icon="♙" />
                <Metric label="Salary total" value={formatMoney(totalSalary)} note="Backend-reported total where available" icon="₹" />
                <Metric label="Payment records" value={payments.length} note="Recorded payment attempts" icon="▤" />
                <Metric label="Paid records" value={payments.filter((payment) => String(payment.status).toLowerCase() === "paid").length} note="Marked paid by backend" icon="✓" />
              </div>
              <section className="panel full-panel">
                <div className="panel-heading"><div><h2>Recent payroll records</h2><p>Payment data from the simulated backend</p></div></div>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Employee</th><th>Amount</th><th>Status</th><th>Updated</th></tr></thead>
                    <tbody>
                      {payments.slice(0, 20).map((payment, index) => (
                        <tr key={payment.id ?? payment.idempotency_key ?? index}>
                          <td><strong>{payment.employee_name || `Employee #${payment.employee_id ?? "—"}`}</strong></td>
                          <td>{formatMoney(payment.amount)}</td>
                          <td><Badge status={statusOf(payment.status)}>{payment.status || "Unknown"}</Badge></td>
                          <td>{formatDate(payment.updated_at || payment.created_at)}</td>
                        </tr>
                      ))}
                      {!payments.length && <tr><td colSpan="4" className="empty-state">No payment records returned by the backend yet.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          )}

          <footer className="page-footer">
            <span>ResilPay <b>•</b> Self-healing payroll demo</span>
            <span>{loading ? "Connecting…" : "Monitoring enabled"} <span className="footer-dot" /></span>
          </footer>
        </div>
      </main>
    </div>
  );
}

function IncidentTable({ incidents, expanded = false }) {
  if (!incidents?.length) {
    return <div className="empty-state">No incidents recorded yet. Run a chaos test to create a demo incident.</div>;
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Incident</th>
            <th>Detected</th>
            <th>Recovery status</th>
            {expanded && <th>Diagnosis</th>}
            {expanded && <th>Action taken</th>}
            <th>Recovery time</th>
          </tr>
        </thead>
        <tbody>
          {incidents.map((incident, index) => {
            const status = incident.recovery_status || incident.status || "Unknown";
            return (
              <tr key={incident.id ?? `${incident.failure_type}-${index}`}>
                <td>
                  <div className="incident-cell">
                    <span className={`incident-marker marker-${statusOf(status)}`} />
                    <div><strong>{incident.failure_type || incident.type || "System incident"}</strong><small>{incident.message || "No description provided"}</small></div>
                  </div>
                </td>
                <td>{formatDate(incident.detected_at || incident.created_at)}</td>
                <td><Badge status={statusOf(status)}>{status}</Badge></td>
                {expanded && <td>{incident.diagnosis || incident.diagnosed_at || "—"}</td>}
                {expanded && <td>{incident.action_taken || "—"}</td>}
                <td>{incident.recovery_time != null ? `${incident.recovery_time}s` : incident.recovery_time_seconds != null ? `${incident.recovery_time_seconds}s` : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default App;
