
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ShieldCheck, PanelLeftClose, PanelLeftOpen, LayoutDashboard,
  Users, Calculator, CreditCard, Activity, Plus, RefreshCw,
  Search, Wallet, CheckCircle, Clock, AlertCircle, History,
  Pencil, Trash2, X, ListChecks
} from 'lucide-react';
import './App.css';

const API = '/api';
const POLL_INTERVAL = 2000;

const money = (value) =>
  '₹' + Number(value || 0).toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });

const period = () => new Date().toISOString().slice(0, 7);

const emptyQueueSummary = {
  pending: 0,
  queued: 0,
  processing: 0,
  completed: 0,
  failed: 0,
  needs_reconciliation: 0
};

const emptyEmployeeForm = {
  name: '',
  department: '',
  salary: ''
};

async function readJson(response) {
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    const error = new Error(
      data.message || data.error || `HTTP ${response.status}`
    );
    error.status = response.status;
    error.data = data;
    throw error;
  }

  return data;
}

async function safeGet(url) {
  const response = await fetch(url, {
    cache: 'no-store',
    headers: { Accept: 'application/json' }
  });
  return readJson(response);
}

async function writeJson(url, method, body) {
  const response = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  return readJson(response);
}

function faultIsActive(status) {
  return Boolean(status?.active);
}

function recoveryIsBlocking(recovery) {
  return Boolean(
    recovery?.active ||
    ['detecting', 'analyzing', 'working', 'verifying'].includes(
      recovery?.stage
    ) ||
    recovery?.stage === 'failed'
  );
}

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [tab, setTab] = useState('overview');

  const [data, setData] = useState({
    employees: [],
    incidents: [],
    payments: [],
    total_salary: 0
  });

  const [loading, setLoading] = useState(true);
  const [backendDisconnected, setBackendDisconnected] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [incidentNotice, setIncidentNotice] = useState('');

  const [apiStatus, setApiStatus] = useState({
    active: false,
    status: 'unknown'
  });
  const [networkStatus, setNetworkStatus] = useState({
    active: false,
    status: 'unknown'
  });
  const [databaseStatus, setDatabaseStatus] = useState({
    active: false,
    status: 'unknown'
  });
  const [recoveryStatus, setRecoveryStatus] = useState({ recoveries: {} });

  const [cacheInfo, setCacheInfo] = useState({
    cached: false,
    stale: false
  });

  const [queueInfo, setQueueInfo] = useState(emptyQueueSummary);
  const [queueItems, setQueueItems] = useState([]);

  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [editingEmployee, setEditingEmployee] = useState(null);

  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);

  const [form, setForm] = useState(emptyEmployeeForm);
  const [salaryForm, setSalaryForm] = useState({
    hra: 0,
    bonus: 0,
    allowances: 0,
    deductions: 0
  });

  const refreshInProgress = useRef(false);
  const previousIncidentStates = useRef(null);
  const previousApiActive = useRef(null);

  const networkRecovery = recoveryStatus?.recoveries?.network;
  const databaseRecovery = recoveryStatus?.recoveries?.database;
  const apiRecovery = recoveryStatus?.recoveries?.api;

  // A network fault blocks writes until recovery is verified.
  // Database/API faults do not disable eligible write actions: those requests
  // must reach Flask so it can persist them in the recovery queue.
  const networkBlocked = Boolean(
    backendDisconnected ||
    faultIsActive(networkStatus) ||
    recoveryIsBlocking(networkRecovery)
  );

  const databaseFault = faultIsActive(databaseStatus);
  const apiFault = faultIsActive(apiStatus);

  const refresh = useCallback(async () => {
    if (refreshInProgress.current) return;
    refreshInProgress.current = true;

    try {
      // Fetch monitoring endpoints independently. A database fault should not
      // prevent the UI from seeing status and submitting queueable operations.
      const [
        dashboardResult,
        apiResult,
        networkResult,
        databaseResult,
        recoveryResult,
        queueResult
      ] = await Promise.all([
        safeGet(`${API}/dashboard-data`).catch((e) => ({ fetchError: e })),
        safeGet(`${API}/api-status`).catch(() => null),
        safeGet(`${API}/network-status`).catch(() => null),
        safeGet(`${API}/database-status`).catch(() => null),
        safeGet(`${API}/recovery-status`).catch(() => null),
        safeGet(`${API}/recovery-queue`).catch(() => null)
      ]);

      const monitoringAvailable = Boolean(
        apiResult && networkResult && databaseResult && recoveryResult
      );

      setBackendDisconnected(!monitoringAvailable);

      if (apiResult) {
        const active = Boolean(apiResult.active);

        if (
          previousApiActive.current !== null &&
          previousApiActive.current !== active
        ) {
          if (active) {
            setIncidentNotice(
              'API fault detected. Eligible operations can be queued while monitoring remains available.'
            );
            setNotice('');
          } else {
            setNotice(
              'API fault cleared. Check the recovery queue for final operation results.'
            );
            setIncidentNotice('');
          }
        }

        previousApiActive.current = active;
        setApiStatus(apiResult);
      }

      if (networkResult) setNetworkStatus(networkResult);
      if (databaseResult) setDatabaseStatus(databaseResult);
      if (recoveryResult) setRecoveryStatus(recoveryResult);

      if (queueResult) {
        setQueueItems(queueResult.items || []);
        setQueueInfo({
          ...emptyQueueSummary,
          ...(queueResult.summary || {})
        });
      }

      if (dashboardResult && !dashboardResult.fetchError) {
        const incomingIncidents = dashboardResult.incidents || [];
        const incidentMap = new Map(
          incomingIncidents.map((item) => [
            String(item.id),
            item.recovery_status
          ])
        );

        if (previousIncidentStates.current !== null) {
          const previous = previousIncidentStates.current;
          const newest = [...incomingIncidents].sort(
            (a, b) => Number(b.id) - Number(a.id)
          )[0];

          if (newest && !previous.has(String(newest.id))) {
            setIncidentNotice(
              `Failure detected: ${newest.failure_type}. Automatic recovery has started.`
            );
            setNotice('');
          } else if (
            newest &&
            previous.has(String(newest.id)) &&
            previous.get(String(newest.id)) !== newest.recovery_status
          ) {
            if (newest.recovery_status === 'Recovered') {
              setNotice(
                `Recovery completed: ${newest.failure_type}. The backend recorded a verified recovery.`
              );
              setIncidentNotice('');
              setError('');
            } else if (
              ['Unrecovered', 'Failed'].includes(newest.recovery_status)
            ) {
              setIncidentNotice(
                `Recovery needs attention: ${newest.failure_type}. Check the incident timeline.`
              );
            }
          }
        }

        previousIncidentStates.current = incidentMap;

        // Use the backend's current/cached snapshot. Never add queued employee
        // records locally; only a later backend response can show a saved row.
        setData({
          employees: dashboardResult.employees || [],
          incidents: incomingIncidents,
          payments: dashboardResult.payments || [],
          total_salary: dashboardResult.total_salary || 0
        });

        setCacheInfo(
          dashboardResult._cache || {
            cached: dashboardResult.data_source === 'cache',
            stale: dashboardResult.data_source === 'cache'
          }
        );

        setError('');
      } else if (dashboardResult?.fetchError) {
        setError(
          'Dashboard data is temporarily unavailable. Status monitoring is still active; check the fault status below.'
        );
      }
    } catch (e) {
      setError(e.message || 'Unable to refresh ResilPay status.');
      setBackendDisconnected(true);
    } finally {
      setLoading(false);
      refreshInProgress.current = false;
    }
  }, []);

  useEffect(() => {
    refresh();

    const pollId = window.setInterval(refresh, POLL_INTERVAL);
    const onVisible = () => {
      if (document.visibilityState === 'visible') refresh();
    };

    window.addEventListener('focus', onVisible);
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      window.clearInterval(pollId);
      window.removeEventListener('focus', onVisible);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [refresh]);

  const employees = data.employees;

  const selected =
    employees.find((employee) => Number(employee.id) === Number(selectedId)) ||
    employees[0];

  const filtered = employees.filter((employee) =>
    `${employee.name} ${employee.department} ${employee.id}`
      .toLowerCase()
      .includes(search.toLowerCase())
  );

  const currentPeriodPayments = data.payments.filter(
    (payment) =>
      !payment.payroll_period || payment.payroll_period === period()
  );

  const latestByEmployee = useMemo(() => {
    const result = new Map();

    [...currentPeriodPayments]
      .sort((a, b) => Number(b.id || 0) - Number(a.id || 0))
      .forEach((payment) => {
        if (!result.has(Number(payment.employee_id))) {
          result.set(Number(payment.employee_id), payment);
        }
      });

    return result;
  }, [data.payments, currentPeriodPayments.length]);

  const paid = currentPeriodPayments.filter(
    (payment) => payment.status === 'PAID'
  );

  const pendingQueueItems = queueItems.filter((item) =>
    ['Queued', 'Processing', 'NeedsReconciliation'].includes(item.status)
  );

  useEffect(() => {
    if (!selected) return;

    setSelectedId(selected.id);
    setSalaryForm({
      hra: selected.hra || 0,
      bonus: selected.bonus || 0,
      allowances: selected.allowances || 0,
      deductions: selected.deductions || 0
    });
  }, [
    selected?.id,
    selected?.hra,
    selected?.bonus,
    selected?.allowances,
    selected?.deductions
  ]);

  function startAddEmployee() {
    if (networkBlocked) return;
    setEditingEmployee(null);
    setForm(emptyEmployeeForm);
    setShowForm(true);
    setError('');
    setNotice('');
  }

  function startEditEmployee(employee) {
    if (networkBlocked) return;
    setEditingEmployee(employee);
    setForm({
      name: employee.name || '',
      department: employee.department || '',
      salary: String(employee.basic_salary || '')
    });
    setShowForm(true);
    setError('');
    setNotice('');
  }

  function closeEmployeeForm() {
    setShowForm(false);
    setEditingEmployee(null);
    setForm(emptyEmployeeForm);
  }

  async function addEmployee(event) {
    event.preventDefault();
    if (networkBlocked || saving) return;

    setSaving(true);
    setError('');
    setNotice('');

    try {
      const result = await writeJson(`${API}/add-employee`, 'POST', {
        name: form.name.trim(),
        department: form.department.trim(),
        salary: Number(form.salary)
      });

      if (result.queued) {
        setNotice(
          `Employee addition queued (${result.queue_id}). It is not saved to SQLite until recovery succeeds.`
        );
        closeEmployeeForm();
      } else {
        setNotice(result.message || 'Employee added successfully.');
        closeEmployeeForm();
      }

      await refresh();
    } catch (e) {
      setError(
        `Employee addition was not confirmed: ${e.message}. Check the queue and employee list before retrying.`
      );
      await refresh();
    } finally {
      setSaving(false);
    }
  }

  async function updateEmployee(event) {
    event.preventDefault();
    if (!editingEmployee || networkBlocked || saving) return;

    setSaving(true);
    setError('');
    setNotice('');

    try {
      const result = await writeJson(
        `${API}/employees/${editingEmployee.id}`,
        'PUT',
        {
          name: form.name.trim(),
          department: form.department.trim(),
          basic_salary: Number(form.salary)
        }
      );

      if (result.queued) {
        setNotice(
          `Employee update queued (${result.queue_id}). The saved record remains unchanged until replay completes.`
        );
        closeEmployeeForm();
      } else {
        setNotice(result.message || 'Employee updated successfully.');
        closeEmployeeForm();
      }

      await refresh();
    } catch (e) {
      setError(
        `Employee update was not confirmed: ${e.message}. Review the employee record before retrying.`
      );
      await refresh();
    } finally {
      setSaving(false);
    }
  }

  async function deleteEmployee(employee) {
    if (networkBlocked || deletingId !== null) return;

    const confirmed = window.confirm(
      `Delete employee ${employee.name} (EMP${String(employee.id).padStart(3, '0')})?\n\nThe backend may reject deletion if payment history exists.`
    );

    if (!confirmed) return;

    setDeletingId(employee.id);
    setError('');
    setNotice('');

    try {
      const result = await writeJson(
        `${API}/employees/${employee.id}`,
        'DELETE'
      );

      if (result.queued) {
        setNotice(
          `Employee deletion queued (${result.queue_id}). The employee remains visible until the queue reports Completed.`
        );
      } else {
        setNotice(result.message || 'Employee deleted successfully.');

        if (Number(selectedId) === Number(employee.id)) {
          setSelectedId(null);
        }
      }

      await refresh();
    } catch (e) {
      setError(
        `Employee deletion was not confirmed: ${e.message}. Check the queue and employee list.`
      );
      await refresh();
    } finally {
      setDeletingId(null);
    }
  }

  async function saveSalary(event) {
    event.preventDefault();
    if (!selected || networkBlocked || saving) return;

    setSaving(true);
    setError('');
    setNotice('');

    try {
      const result = await writeJson(
        `${API}/employees/${selected.id}/salary`,
        'PUT',
        {
          hra: Number(salaryForm.hra),
          bonus: Number(salaryForm.bonus),
          allowances: Number(salaryForm.allowances),
          deductions: Number(salaryForm.deductions)
        }
      );

      if (result.queued) {
        setNotice(
          `Salary update queued (${result.queue_id}). It is not saved until the queue reports Completed.`
        );
      } else {
        setNotice(result.message || 'Salary structure updated.');
      }

      await refresh();
    } catch (e) {
      setError(
        `Salary update was not confirmed: ${e.message}. Check the queue and employee record before retrying.`
      );
      await refresh();
    } finally {
      setSaving(false);
    }
  }

  async function payEmployee(employee) {
    if (networkBlocked || busyId !== null) return;

    setBusyId(employee.id);
    setError('');
    setNotice('');

    try {
      const result = await writeJson(
        `${API}/payments/employee/${employee.id}`,
        'POST',
        { run_id: period() }
      );

      if (result.queued) {
        setNotice(
          `Payment request queued (${result.queue_id}). No payment is confirmed yet. Check the final queue result after recovery.`
        );
      } else {
        setNotice(
          result.message ||
          `Payment status: ${result.payment?.status || 'review transaction history'}.`
        );
      }

      await refresh();
    } catch (e) {
      setError(
        `Payment was not confirmed: ${e.message}. Review transaction history before retrying; do not assume the payment failed.`
      );
      await refresh();
    } finally {
      setBusyId(null);
    }
  }

  async function runPayroll() {
    if (networkBlocked || saving) return;

    const confirmed = window.confirm(
      `Run simulated payroll for ${period()}?\n\nReview existing transaction history if a previous run had an uncertain outcome.`
    );

    if (!confirmed) return;

    setSaving(true);
    setError('');
    setNotice('');

    try {
      const result = await writeJson(`${API}/payments/run`, 'POST', {
        run_id: period()
      });

      if (result.queued) {
        setNotice(
          `Payroll run queued (${result.queue_id}). No payment is confirmed until processing finishes.`
        );
      } else {
        setNotice(
          `Payroll run ${result.run_id || period()}: ${result.paid_count ?? 0} paid, ${result.failed_count ?? 0} failed. No real money was transferred.`
        );
      }

      await refresh();
    } catch (e) {
      setError(
        `Payroll run was not fully confirmed: ${e.message}. Review payment records before running it again.`
      );
      await refresh();
    } finally {
      setSaving(false);
    }
  }

  function salary(employee) {
    const basic = Number(employee.basic_salary || 0);
    const hra = Number(employee.hra || 0);
    const bonus = Number(employee.bonus || 0);
    const allowances = Number(employee.allowances || 0);
    const deductions = Number(employee.deductions || 0);
    const gross = basic + hra + bonus + allowances;

    return {
      basic,
      hra,
      bonus,
      allowances,
      deductions,
      gross,
      net: gross - deductions
    };
  }

  function navItem(key, label, Icon) {
    return (
      <button
        key={key}
        className={`sidebar-link ${tab === key ? 'active' : ''}`}
        onClick={() => setTab(key)}
        title={label}
      >
        <Icon size={18} />
        {sidebarOpen && <span>{label}</span>}
      </button>
    );
  }

  function paymentStatus(employee) {
    return latestByEmployee.get(Number(employee.id))?.status || 'UNPAID';
  }

  return (
    <div className="rp-app">
      <header className="rp-topbar">
        <button
          className="rp-menu"
          onClick={() => setSidebarOpen((value) => !value)}
          title={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
          aria-label="Toggle sidebar"
        >
          {sidebarOpen
            ? <PanelLeftClose size={20} />
            : <PanelLeftOpen size={20} />}
        </button>

        <div className="rp-brand-mark">
          <ShieldCheck size={22} />
        </div>

        <div className="rp-brand">
          <strong>ResilPay</strong>
          <small>PAYROLL OPERATIONS</small>
        </div>

        <div className="rp-top-actions">
          <span className="rp-demo"><span /> DEMO ENVIRONMENT</span>
          <a href="http://localhost:5174" className="rp-control-link">
            <Activity size={15} /> Control Center
          </a>
        </div>
      </header>

      <div className={`rp-layout ${sidebarOpen ? '' : 'is-collapsed'}`}>
        <aside className="rp-sidebar">
          {sidebarOpen && <div className="rp-side-heading">WORKSPACE</div>}

          {navItem('overview', 'Overview', LayoutDashboard)}
          {navItem('employees', 'Employees', Users)}
          {navItem('salary', 'Salary Register', Calculator)}
          {navItem('payments', 'Payments', CreditCard)}

          <div className="rp-side-separator" />

          {sidebarOpen && <div className="rp-side-heading">SYSTEM</div>}

          <a href="http://localhost:5174" className="sidebar-link">
            <ShieldCheck size={18} />
            {sidebarOpen && <span>Control Center</span>}
          </a>

          <button
            className="sidebar-link"
            onClick={refresh}
            title="Refresh data"
            disabled={backendDisconnected}
          >
            <History size={18} />
            {sidebarOpen && <span>Refresh data</span>}
          </button>

          {sidebarOpen && (
            <div className="rp-sidebar-note">
              <span className="rp-note-dot" />
              <div>
                <b>Self-healing enabled</b>
                <small>Monitoring is handled in Control Center.</small>
              </div>
            </div>
          )}
        </aside>

        <main className="rp-main">
          <div className="rp-page-heading">
            <div>
              <div className="rp-breadcrumb">
                RESILPAY / {tab.toUpperCase()}
              </div>

              <h1>
                {{
                  overview: 'Payroll overview',
                  employees: 'Employee directory',
                  salary: 'Salary register',
                  payments: 'Payment register'
                }[tab]}
              </h1>

              <p>
                {{
                  overview: 'A clear view of your payroll operations.',
                  employees: 'Employee records, departments and basic pay.',
                  salary: 'Manage compensation components and net payable.',
                  payments: 'Track simulated payments and outstanding transactions.'
                }[tab]}
              </p>
            </div>

            <div className="rp-heading-actions">
              <button
                className="rp-btn secondary"
                onClick={refresh}
                disabled={loading || backendDisconnected}
              >
                <RefreshCw size={15} /> Refresh
              </button>

              <button
                className="rp-btn primary"
                onClick={startAddEmployee}
                disabled={networkBlocked}
                title={
                  networkBlocked
                    ? 'Disabled until network recovery is verified'
                    : 'Add employee'
                }
              >
                <Plus size={16} /> Add employee
              </button>
            </div>
          </div>

          {networkBlocked && (
            <div
              role="alert"
              aria-live="assertive"
              className="rp-alert error"
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 12,
                padding: '16px 18px',
                marginBottom: 16,
                border: '1px solid #D88A83',
                borderRadius: 12,
                background: '#FBE9E7',
                color: '#842D2D'
              }}
            >
              <AlertCircle size={22} style={{ flexShrink: 0, marginTop: 2 }} />
              <div style={{ flex: 1 }}>
                <strong style={{ display: 'block', marginBottom: 4 }}>
                  {backendDisconnected
                    ? 'Backend connection unavailable'
                    : 'Network failure — payroll actions disabled'}
                </strong>
                <span>
                  {backendDisconnected
                    ? 'ResilPay cannot verify backend status. Write actions are disabled to prevent unconfirmed changes.'
                    : 'Writes are disabled until network recovery clears the fault and verifies service health. Existing employee and payment records remain unchanged.'}
                </span>
                {error && (
                  <small style={{ display: 'block', marginTop: 6 }}>
                    Details: {error}
                  </small>
                )}
              </div>
              <button
                type="button"
                onClick={refresh}
                disabled={backendDisconnected}
                style={{
                  border: '1px solid #B85B55',
                  borderRadius: 8,
                  padding: '7px 10px',
                  background: '#FFF8F7',
                  color: '#842D2D',
                  cursor: backendDisconnected ? 'not-allowed' : 'pointer',
                  whiteSpace: 'nowrap'
                }}
              >
                Retry status
              </button>
            </div>
          )}

          {databaseFault && (
            <div className="rp-alert error" role="alert" aria-live="assertive">
              <AlertCircle size={18} />
              <span>
                <strong>Database failure — operations are waiting.</strong>{' '}
                Eligible employee changes and payment requests can be queued by
                Flask. They are not saved or confirmed until database recovery
                is verified and the queue reports a final result.
              </span>
            </div>
          )}

          {apiFault && !networkBlocked && (
            <div className="rp-alert error" role="alert" aria-live="assertive">
              <AlertCircle size={18} />
              <span>
                <strong>API failure active.</strong>{' '}
                Monitoring remains available. Eligible operations may be queued
                by the backend; check each queue item's final status before
                assuming a change was saved.
              </span>
            </div>
          )}

          {incidentNotice && (
            <div className="rp-alert error" role="alert" aria-live="assertive">
              <AlertCircle size={17} />
              <span>{incidentNotice}</span>
              <button onClick={() => setIncidentNotice('')}>Dismiss</button>
            </div>
          )}

          {error && !networkBlocked && (
            <div className="rp-alert error" role="alert" aria-live="assertive">
              <AlertCircle size={17} />
              <span>{error}</span>
              <button onClick={() => setError('')}>Dismiss</button>
            </div>
          )}

          {notice && (
            <div className="rp-alert success" role="status" aria-live="polite">
              <CheckCircle size={17} />
              <span>{notice}</span>
              <button onClick={() => setNotice('')}>Dismiss</button>
            </div>
          )}

          {(cacheInfo.cached ||
            queueInfo.pending > 0 ||
            queueInfo.failed > 0 ||
            queueInfo.needs_reconciliation > 0) && (
            <div
              className="rp-alert"
              role="status"
              style={{
                display: 'flex',
                gap: 12,
                alignItems: 'center',
                flexWrap: 'wrap',
                marginBottom: 14
              }}
            >
              {cacheInfo.cached && (
                <span>
                  <strong>
                    {cacheInfo.stale ? 'Cached · may be stale' : 'Cached data'}
                  </strong>
                  {' '}— showing last-known-good data; writes still require
                  backend confirmation.
                </span>
              )}

              {queueInfo.pending > 0 && (
                <span>
                  <strong>{queueInfo.pending} operation(s) queued</strong>
                  {' '}— these operations are not yet confirmed as saved.
                </span>
              )}

              {queueInfo.failed > 0 && (
                <span>
                  <strong>{queueInfo.failed} queued operation(s) failed</strong>
                  {' '}— review their results before retrying.
                </span>
              )}

              {queueInfo.needs_reconciliation > 0 && (
                <span>
                  <strong>
                    {queueInfo.needs_reconciliation} operation(s) need
                    reconciliation
                  </strong>
                  {' '}— verify the actual outcome before retrying.
                </span>
              )}
            </div>
          )}

          {showForm && (
            <form
              className="rp-panel rp-form"
              onSubmit={editingEmployee ? updateEmployee : addEmployee}
            >
              <div className="rp-panel-title">
                <div>
                  <h2>{editingEmployee ? 'Edit employee' : 'New employee'}</h2>
                  <p>
                    {databaseFault
                      ? 'The backend may queue this change until database recovery.'
                      : editingEmployee
                        ? 'Update the employee record.'
                        : 'Add a record to the employee register.'}
                  </p>
                </div>

                <button
                  type="button"
                  className="rp-btn secondary"
                  onClick={closeEmployeeForm}
                  aria-label="Close employee form"
                >
                  <X size={15} /> Cancel
                </button>
              </div>

              <div className="rp-form-grid">
                <label>
                  Full name
                  <input
                    required
                    maxLength={120}
                    value={form.name}
                    onChange={(event) =>
                      setForm({ ...form, name: event.target.value })
                    }
                  />
                </label>

                <label>
                  Department
                  <input
                    required
                    maxLength={120}
                    value={form.department}
                    onChange={(event) =>
                      setForm({ ...form, department: event.target.value })
                    }
                  />
                </label>

                <label>
                  Basic monthly salary (₹)
                  <input
                    required
                    type="number"
                    min="0.01"
                    step="0.01"
                    value={form.salary}
                    onChange={(event) =>
                      setForm({ ...form, salary: event.target.value })
                    }
                  />
                </label>
              </div>

              <button
                className="rp-btn primary"
                disabled={saving || networkBlocked}
              >
                {saving
                  ? 'Submitting…'
                  : editingEmployee
                    ? 'Save employee changes'
                    : 'Save employee'}
              </button>

              {databaseFault && (
                <p className="rp-footnote">
                  The employee will not appear in the employee list until the
                  backend confirms that the queued operation completed.
                </p>
              )}
            </form>
          )}

          <section className="rp-panel" style={{ marginBottom: 16 }}>
            <div className="rp-panel-title">
              <div>
                <h2>
                  <ListChecks size={18} style={{ verticalAlign: 'middle' }} />{' '}
                  Recovery queue
                </h2>
                <p>
                  Queued changes are not saved until their status is Completed.
                </p>
              </div>
              <span className="rp-count">{queueInfo.pending} PENDING</span>
            </div>

            <div className="rp-overview-grid">
              <div><span>Queued</span><strong>{queueInfo.queued}</strong></div>
              <div><span>Processing</span><strong>{queueInfo.processing}</strong></div>
              <div><span>Completed</span><strong>{queueInfo.completed}</strong></div>
              <div><span>Failed</span><strong>{queueInfo.failed}</strong></div>
              <div>
                <span>Needs review</span>
                <strong>{queueInfo.needs_reconciliation}</strong>
              </div>
            </div>

            <div className="rp-table-wrap">
              <table className="rp-table">
                <thead>
                  <tr>
                    <th>OPERATION</th>
                    <th>STATUS</th>
                    <th>CREATED</th>
                    <th>RESULT</th>
                  </tr>
                </thead>
                <tbody>
                  {queueItems.map((item) => (
                    <tr key={item.queue_id}>
                      <td>
                        <b>{item.method}</b> {item.path}
                        <small style={{ display: 'block' }}>
                          ID: {item.queue_id}
                        </small>
                      </td>
                      <td>
                        <span
                          className={`rp-status ${String(item.status || '').toLowerCase()}`}
                        >
                          {item.status}
                        </span>
                      </td>
                      <td>
                        {item.created_at
                          ? new Date(item.created_at).toLocaleString()
                          : '—'}
                      </td>
                      <td>
                        {item.result?.message || 'Waiting for processing'}
                      </td>
                    </tr>
                  ))}

                  {!queueItems.length && (
                    <tr>
                      <td colSpan="4" className="rp-empty">
                        No queued operations.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {pendingQueueItems.length > 0 && (
              <p className="rp-footnote">
                Pending changes are not yet confirmed in the database. Keep
                this page open to follow their status.
              </p>
            )}
          </section>

          <section className="rp-metrics">
            <article className="rp-metric">
              <div className="rp-metric-label">ACTIVE EMPLOYEE RECORDS</div>
              <div className="rp-metric-value">
                {loading ? '—' : employees.length}
              </div>
              <div className="rp-metric-foot">Registered in SQLite</div>
            </article>

            <article className="rp-metric">
              <div className="rp-metric-label">MONTHLY BASIC PAYROLL</div>
              <div className="rp-metric-value">
                {loading ? '—' : money(data.total_salary)}
              </div>
              <div className="rp-metric-foot">
                Before allowances and deductions
              </div>
            </article>

            <article className="rp-metric">
              <div className="rp-metric-label">CONFIRMED PAYMENTS</div>
              <div className="rp-metric-value">{paid.length}</div>
              <div className="rp-metric-foot">
                {currentPeriodPayments.length} transaction(s) for this period
              </div>
            </article>
          </section>

          {tab === 'overview' && (
            <>
              <section className="rp-panel">
                <div className="rp-panel-title">
                  <div>
                    <h2>Payroll snapshot</h2>
                    <p>Summary of employee compensation and payment status.</p>
                  </div>
                  <button
                    className="rp-btn primary"
                    onClick={runPayroll}
                    disabled={saving || networkBlocked}
                  >
                    <Wallet size={16} />
                    {saving ? 'Processing…' : 'Run simulated payroll'}
                  </button>
                </div>

                <div className="rp-overview-grid">
                  <div>
                    <span>Employees awaiting payment</span>
                    <strong>
                      {employees.filter((employee) =>
                        paymentStatus(employee) !== 'PAID'
                      ).length}
                    </strong>
                  </div>
                  <div>
                    <span>Recent incidents</span>
                    <strong>{data.incidents.length}</strong>
                  </div>
                  <div>
                    <span>Payroll period</span>
                    <strong>{period()}</strong>
                  </div>
                </div>
              </section>

              <section className="rp-panel">
                <div className="rp-panel-title">
                  <div>
                    <h2>Recent employee records</h2>
                    <p>Latest data from the Flask API.</p>
                  </div>
                  <button
                    className="rp-text-button"
                    onClick={() => setTab('employees')}
                  >
                    View all employees →
                  </button>
                </div>

                <EmployeeTable
                  employees={employees.slice(0, 5)}
                  onSalary={(employee) => {
                    setSelectedId(employee.id);
                    setTab('salary');
                  }}
                  onEdit={startEditEmployee}
                  onDelete={deleteEmployee}
                  deletingId={deletingId}
                  networkBlocked={networkBlocked}
                />
              </section>
            </>
          )}

          {tab === 'employees' && (
            <section className="rp-panel">
              <div className="rp-panel-title">
                <div>
                  <h2>Employee details</h2>
                  <p>Search, edit and review registered employees.</p>
                </div>
                <span className="rp-count">{filtered.length} RECORDS</span>
              </div>

              <div className="rp-search">
                <Search size={16} />
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search name, department or employee ID"
                />
              </div>

              <EmployeeTable
                employees={filtered}
                onSalary={(employee) => {
                  setSelectedId(employee.id);
                  setTab('salary');
                }}
                onEdit={startEditEmployee}
                onDelete={deleteEmployee}
                deletingId={deletingId}
                networkBlocked={networkBlocked}
              />
            </section>
          )}

          {tab === 'salary' && (
            <section className="rp-panel">
              <div className="rp-panel-title">
                <div>
                  <h2>Salary structure</h2>
                  <p>Save compensation components to SQLite.</p>
                </div>
              </div>

              {!selected ? (
                <p>No employees available.</p>
              ) : (
                <>
                  <label className="rp-select-label">
                    EMPLOYEE
                    <select
                      value={selected.id}
                      onChange={(event) =>
                        setSelectedId(Number(event.target.value))
                      }
                    >
                      {employees.map((employee) => (
                        <option key={employee.id} value={employee.id}>
                          {employee.name} — {employee.department}
                        </option>
                      ))}
                    </select>
                  </label>

                  <form onSubmit={saveSalary}>
                    <div className="rp-salary-grid">
                      <div className="rp-salary-field">
                        <label>Basic salary</label>
                        <div className="rp-money-input">
                          <span>₹</span>
                          <input
                            readOnly
                            value={Number(selected.basic_salary || 0).toFixed(2)}
                          />
                        </div>
                      </div>

                      {[
                        ['HRA', 'hra'],
                        ['Bonus', 'bonus'],
                        ['Other allowances', 'allowances'],
                        ['Deductions', 'deductions']
                      ].map(([label, field]) => (
                        <div className="rp-salary-field" key={field}>
                          <label>{label}</label>
                          <div className="rp-money-input">
                            <span>₹</span>
                            <input
                              type="number"
                              min="0"
                              step="0.01"
                              value={salaryForm[field]}
                              onChange={(event) =>
                                setSalaryForm({
                                  ...salaryForm,
                                  [field]: event.target.value
                                })
                              }
                            />
                          </div>
                        </div>
                      ))}
                    </div>

                    <div className="rp-totals">
                      <div>
                        <span>Gross monthly pay</span>
                        <strong>{money(salary(selected).gross)}</strong>
                      </div>
                      <div>
                        <span>Total deductions</span>
                        <strong>{money(salary(selected).deductions)}</strong>
                      </div>
                      <div className="net">
                        <span>Net payable</span>
                        <strong>{money(salary(selected).net)}</strong>
                      </div>
                    </div>

                    <button
                      className="rp-btn primary"
                      disabled={saving || networkBlocked}
                    >
                      {saving ? 'Submitting…' : 'Save salary structure'}
                    </button>

                    {databaseFault && (
                      <p className="rp-footnote">
                        The salary update may be queued. The saved values remain
                        unchanged until the queue reports Completed.
                      </p>
                    )}
                  </form>
                </>
              )}
            </section>
          )}

          {tab === 'payments' && (
            <section className="rp-panel">
              <div className="rp-panel-title">
                <div>
                  <h2>Payment register</h2>
                  <p>Payment status for the current demo payroll period.</p>
                </div>
                <button
                  className="rp-btn primary"
                  onClick={runPayroll}
                  disabled={saving || networkBlocked}
                >
                  <Wallet size={15} /> Run all
                </button>
              </div>

              <div className="rp-payment-summary">
                <div>
                  <CheckCircle size={17} />
                  <span>Paid <b>{paid.length}</b></span>
                </div>
                <div>
                  <Clock size={17} />
                  <span>
                    Unpaid / pending{' '}
                    <b>
                      {employees.filter((employee) =>
                        paymentStatus(employee) !== 'PAID'
                      ).length}
                    </b>
                  </span>
                </div>
                <div>
                  <Wallet size={17} />
                  <span>Basic payroll <b>{money(data.total_salary)}</b></span>
                </div>
              </div>

              <div className="rp-table-wrap">
                <table className="rp-table">
                  <thead>
                    <tr>
                      <th>EMPLOYEE</th>
                      <th>NET PAYABLE</th>
                      <th>STATUS</th>
                      <th>REFERENCE</th>
                      <th>ACTION</th>
                    </tr>
                  </thead>
                  <tbody>
                    {employees.map((employee) => {
                      const payment = latestByEmployee.get(Number(employee.id));
                      const status = payment?.status || 'UNPAID';

                      return (
                        <tr key={employee.id}>
                          <td>
                            <div className="rp-employee-cell">
                              <span className="rp-avatar">
                                {employee.name
                                  .split(/\s+/)
                                  .map((part) => part[0])
                                  .slice(0, 2)
                                  .join('')
                                  .toUpperCase()}
                              </span>
                              <div>
                                <b>{employee.name}</b>
                                <small>
                                  EMP{String(employee.id).padStart(3, '0')}
                                </small>
                              </div>
                            </div>
                          </td>

                          <td className="rp-amount">
                            {money(salary(employee).net)}
                          </td>

                          <td>
                            <span className={`rp-status ${status.toLowerCase()}`}>
                              {status}
                            </span>
                          </td>

                          <td>{payment?.idempotency_key || '—'}</td>

                          <td>
                            <button
                              className="rp-btn small"
                              disabled={
                                busyId === employee.id ||
                                status === 'PAID' ||
                                networkBlocked
                              }
                              onClick={() => payEmployee(employee)}
                            >
                              {busyId === employee.id
                                ? 'Processing…'
                                : status === 'PAID'
                                  ? 'Paid'
                                  : status === 'FAILED'
                                    ? 'Review payment'
                                    : 'Pay (demo)'}
                            </button>
                          </td>
                        </tr>
                      );
                    })}

                    {!employees.length && (
                      <tr>
                        <td colSpan="5" className="rp-empty">
                          No employees found.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              <p className="rp-footnote">
                Simulated transactions only. An uncertain or failed payment
                must be reconciled before retrying. Provider recovery alone does
                not confirm payment success.
              </p>
            </section>
          )}

          <footer className="rp-footer">
            ResilPay · Payroll operations · No real money is transferred
          </footer>
        </main>
      </div>
    </div>
  );
}

function EmployeeTable({
  employees,
  onSalary,
  onEdit,
  onDelete,
  deletingId,
  networkBlocked
}) {
  return (
    <div className="rp-table-wrap">
      <table className="rp-table">
        <thead>
          <tr>
            <th>EMPLOYEE</th>
            <th>EMPLOYEE ID</th>
            <th>DEPARTMENT</th>
            <th>BASIC SALARY</th>
            <th>DETAILS</th>
            <th>ACTIONS</th>
          </tr>
        </thead>
        <tbody>
          {employees.map((employee) => (
            <tr key={employee.id}>
              <td>
                <div className="rp-employee-cell">
                  <span className="rp-avatar">
                    {employee.name
                      .split(/\s+/)
                      .map((part) => part[0])
                      .slice(0, 2)
                      .join('')
                      .toUpperCase()}
                  </span>
                  <div>
                    <b>{employee.name}</b>
                    <small>{employee.designation || 'Employee'}</small>
                  </div>
                </div>
              </td>

              <td>EMP{String(employee.id).padStart(3, '0')}</td>
              <td>{employee.department}</td>
              <td className="rp-amount">{money(employee.basic_salary)}</td>

              <td>
                <button
                  className="rp-text-button"
                  onClick={() => onSalary(employee)}
                >
                  Salary details →
                </button>
              </td>

              <td>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  <button
                    type="button"
                    className="rp-btn small"
                    onClick={() => onEdit(employee)}
                    disabled={networkBlocked}
                    title="Edit employee"
                  >
                    <Pencil size={14} /> Edit
                  </button>

                  <button
                    type="button"
                    className="rp-btn small"
                    disabled={deletingId === employee.id || networkBlocked}
                    onClick={() => onDelete(employee)}
                    title="Delete employee"
                  >
                    <Trash2 size={14} />
                    {deletingId === employee.id ? 'Deleting…' : 'Delete'}
                  </button>
                </div>
              </td>
            </tr>
          ))}

          {!employees.length && (
            <tr>
              <td colSpan="6" className="rp-empty">
                No employee records found.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
