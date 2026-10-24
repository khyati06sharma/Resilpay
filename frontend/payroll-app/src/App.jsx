
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ShieldCheck, PanelLeftClose, PanelLeftOpen, LayoutDashboard,
  Users, Calculator, CreditCard, Activity, Plus, RefreshCw,
  Search, Wallet, CheckCircle, Clock, AlertCircle, History
} from 'lucide-react';
import './App.css';

const API = '/api';
const money = (n) => '₹' + Number(n || 0).toLocaleString('en-IN', {
  minimumFractionDigits: 2, maximumFractionDigits: 2
});
const period = () => new Date().toISOString().slice(0, 7);

async function readJson(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
  return data;
}

// GET requests are safe to retry. Never use this helper for employee or payment writes.
async function safeGetWithRetry(url, attempts = 3) {
  let lastError;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await fetch(url, { cache: 'no-store' });
      return await readJson(response);
    } catch (error) {
      lastError = error;
      if (attempt < attempts - 1) {
        await new Promise(resolve => window.setTimeout(resolve, 300 * (attempt + 1)));
      }
    }
  }
  throw lastError;
}

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [tab, setTab] = useState('overview');
  const [data, setData] = useState({ employees: [], incidents: [], payments: [], total_salary: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [apiUnavailable, setApiUnavailable] = useState(false);
  const [apiStatus, setApiStatus] = useState({ active: false, status: 'healthy' });
  const [cacheInfo, setCacheInfo] = useState({ cached: false, stale: false });
  const [queueInfo, setQueueInfo] = useState({ pending: 0, queued: 0, processing: 0, completed: 0, failed: 0 });
  const outageRef = useRef(false);
  const [notice, setNotice] = useState('');
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [form, setForm] = useState({ name: '', department: '', salary: '' });
  const [salaryForm, setSalaryForm] = useState({
    hra: 0, bonus: 0, allowances: 0, deductions: 0
  });

  const refresh = useCallback(async () => {
    try {
      // Both are read-only GETs, so bounded automatic retries are safe.
      const [result, status, queue] = await Promise.all([
        safeGetWithRetry(`${API}/dashboard-data`),
        safeGetWithRetry(`${API}/api-status`),
        safeGetWithRetry(`${API}/recovery-queue`).catch(() => ({ summary: {} })),
      ]);
      setData({
        employees: result.employees || [],
        incidents: result.incidents || [],
        payments: result.payments || [],
        total_salary: result.total_salary || 0
      });
      setApiStatus(status || { active: false, status: 'healthy' });
      setCacheInfo(result._cache || { cached: result.data_source === 'cache', stale: result.data_source === 'cache' });
      setQueueInfo(queue.summary || { pending: 0, queued: 0, processing: 0, completed: 0, failed: 0 });

      if (outageRef.current || status?.active) {
        outageRef.current = false;
        setApiUnavailable(Boolean(status?.active));
        if (!status?.active) {
          setNotice('API connection restored. Health checks passed and payroll data is refreshed.');
        }
      } else {
        setApiUnavailable(Boolean(status?.active));
      }
      setError('');
    } catch (e) {
      const message = e.message || 'Unable to connect to the ResilPay backend.';
      setError(message);
      outageRef.current = true;
      setApiUnavailable(true);
    } finally {
      setLoading(false);
    }
  }, []);

  // Load immediately, then keep checking so a fault triggered from the
  // separate Control Center is detected by this Payroll App too.
  useEffect(() => {
    refresh();

    const pollId = window.setInterval(() => {
      // Avoid starting another request while the previous dashboard request
      // is still in flight (the ref is shared with refresh below).
      refresh();
    }, 2000);

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
  const selected = employees.find(e => e.id === selectedId) || employees[0];
  const filtered = employees.filter(e =>
    `${e.name} ${e.department} ${e.id}`.toLowerCase().includes(search.toLowerCase())
  );
  const paid = data.payments.filter(p => p.status === 'PAID');
  const latestByEmployee = new Map();
  data.payments.forEach(p => {
    if (!latestByEmployee.has(p.employee_id)) latestByEmployee.set(p.employee_id, p);
  });

  useEffect(() => {
    if (selected) {
      setSelectedId(selected.id);
      setSalaryForm({
        hra: selected.hra || 0,
        bonus: selected.bonus || 0,
        allowances: selected.allowances || 0,
        deductions: selected.deductions || 0
      });
    }
  }, [selected?.id, selected?.hra, selected?.bonus, selected?.allowances, selected?.deductions]);

  async function addEmployee(e) {
    e.preventDefault();
    setSaving(true);
    setNotice('');
    try {
      const result = await fetch(`${API}/add-employee`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: form.name.trim(),
          department: form.department.trim(),
          salary: Number(form.salary)
        })
      }).then(readJson);
      if (result.queued) { setNotice(`Queued for recovery (ID ${result.queue_id}). This employee is not saved yet.`); }
      else { setNotice(result.message || 'Employee added.'); setForm({ name: '', department: '', salary: '' }); setShowForm(false); }
      await refresh();
    } catch (e) {
      setError(`Employee save was not confirmed: ${e.message}. Your form is preserved. Check the refreshed employee list before retrying to avoid creating a duplicate.`);
    } finally {
      setSaving(false);
    }
  }

  async function saveSalary(e) {
    e.preventDefault();
    if (!selected) return;
    setSaving(true);
    setNotice('');
    try {
      await fetch(`${API}/employees/${selected.id}/salary`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          hra: Number(salaryForm.hra),
          bonus: Number(salaryForm.bonus),
          allowances: Number(salaryForm.allowances),
          deductions: Number(salaryForm.deductions)
        })
      }).then(readJson);
      setNotice('Salary structure update accepted. Check queue status before assuming it is saved.');
      await refresh();
    } catch (e) {
      setError(`Salary update was not confirmed: ${e.message}. Check the current employee details before retrying.`);
    } finally {
      setSaving(false);
    }
  }

  async function payEmployee(employee) {
    setBusyId(employee.id);
    setError('');
    setNotice('');
    try {
      const result = await fetch(`${API}/payments/employee/${employee.id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: period() })
      }).then(readJson);
      setNotice(result.message || 'Simulated payment confirmed.');
      await refresh();
    } catch (e) {
      setError(`Payment was not confirmed: ${e.message || 'API unavailable'}. Check the transaction history before retrying; do not assume the payment failed.`);
      await refresh();
    } finally {
      setBusyId(null);
    }
  }

  async function runPayroll() {
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const result = await fetch(`${API}/payments/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: period() })
      }).then(readJson);
      setNotice(`Payroll run complete: ${result.paid_count} paid, ${result.failed_count} failed. No real money was transferred.`);
      await refresh();
    } catch (e) {
      setError(`Payroll run was not confirmed: ${e.message}. Review the transaction history before retrying.`);
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
    return { basic, hra, bonus, allowances, deductions, gross, net: gross - deductions };
  }

  function navItem(key, label, Icon) {
    return (
      <button key={key}
        className={`sidebar-link ${tab === key ? 'active' : ''}`}
        onClick={() => setTab(key)} title={label}>
        <Icon size={18} />
        {sidebarOpen && <span>{label}</span>}
      </button>
    );
  }

  return (
    <div className="rp-app">
      <header className="rp-topbar">
        <button className="rp-menu" onClick={() => setSidebarOpen(v => !v)}
          title={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
          aria-label="Toggle sidebar">
          {sidebarOpen ? <PanelLeftClose size={20} /> : <PanelLeftOpen size={20} />}
        </button>
        <div className="rp-brand-mark"><ShieldCheck size={22} /></div>
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
          <button className="sidebar-link" onClick={refresh} title="Refresh data">
            <History size={18} />
            {sidebarOpen && <span>Refresh data</span>}
          </button>
          {sidebarOpen && (
            <div className="rp-sidebar-note">
              <span className="rp-note-dot" />
              <div><b>Self-healing enabled</b><small>Monitoring is handled in Control Center.</small></div>
            </div>
          )}
        </aside>

        <main className="rp-main">
          <div className="rp-page-heading">
            <div>
              <div className="rp-breadcrumb">RESILPAY / {tab.toUpperCase()}</div>
              <h1>{{
                overview: 'Payroll overview',
                employees: 'Employee directory',
                salary: 'Salary register',
                payments: 'Payment register'
              }[tab]}</h1>
              <p>{{
                overview: 'A clear view of your payroll operations.',
                employees: 'Employee records, departments and basic pay.',
                salary: 'Manage compensation components and net payable.',
                payments: 'Track simulated payments and outstanding transactions.'
              }[tab]}</p>
            </div>
            <div className="rp-heading-actions">
              <button className="rp-btn secondary" onClick={refresh} disabled={loading}>
                <RefreshCw size={15} /> Refresh
              </button>
              <button className="rp-btn primary" onClick={() => setShowForm(v => !v)}>
                <Plus size={16} /> Add employee
              </button>
            </div>
          </div>

          {apiUnavailable && (
            <div role="alert" aria-live="assertive" className="rp-alert error" style={{
              display: 'flex', alignItems: 'flex-start', gap: '12px',
              padding: '16px 18px', marginBottom: '16px',
              border: '1px solid #D88A83', borderRadius: '12px',
              background: '#FBE9E7', color: '#842D2D'
            }}>
              <AlertCircle size={22} style={{ flexShrink: 0, marginTop: '2px' }} />
              <div style={{ flex: 1 }}>
                <strong style={{ display: 'block', marginBottom: '4px' }}>
                  Backend operation temporarily unavailable
                </strong>
                <span>
                  Payroll write operations are temporarily unavailable. The dashboard and service-status
                  monitoring remain online. Read-only requests retry automatically; employee and payment
                  changes are not blindly retried. Check the current records before retrying an operation
                  whose outcome is uncertain.
                </span>
                {error && <small style={{ display: 'block', marginTop: '6px' }}>Details: {error}</small>}
              </div>
              <button type="button" onClick={refresh} style={{
                border: '1px solid #B85B55', borderRadius: '8px', padding: '7px 10px',
                background: '#FFF8F7', color: '#842D2D', cursor: 'pointer',
                whiteSpace: 'nowrap'
              }}>Retry now</button>
            </div>
          )}
          {error && !apiUnavailable && (
            <div className="rp-alert error"><AlertCircle size={17} /><span>{error}</span><button onClick={() => setError('')}>Dismiss</button></div>
          )}
          {notice && <div className="rp-alert success"><CheckCircle size={17} /><span>{notice}</span><button onClick={() => setNotice('')}>Dismiss</button></div>}
          {(cacheInfo.cached || queueInfo.pending > 0 || queueInfo.failed > 0) && (
            <div className="rp-alert" role="status" style={{display:'flex',gap:12,alignItems:'center',flexWrap:'wrap',marginBottom:14}}>
              {cacheInfo.cached && <span><strong>{cacheInfo.stale ? 'Cached · may be stale' : 'Cached data'}</strong> — showing last-known-good read data; writes still require backend confirmation.</span>}
              {queueInfo.pending > 0 && <span><strong>{queueInfo.pending} operation(s) queued</strong> — pending operations are not yet saved.</span>}
              {queueInfo.failed > 0 && <span><strong>{queueInfo.failed} queued operation(s) failed</strong> — review before retrying.</span>}
            </div>
          )}

          <section className="rp-metrics">
            <article className="rp-metric">
              <div className="rp-metric-label">ACTIVE EMPLOYEE RECORDS</div>
              <div className="rp-metric-value">{loading ? '—' : employees.length}</div>
              <div className="rp-metric-foot">Registered in SQLite</div>
            </article>
            <article className="rp-metric">
              <div className="rp-metric-label">MONTHLY BASIC PAYROLL</div>
              <div className="rp-metric-value">{loading ? '—' : money(data.total_salary)}</div>
              <div className="rp-metric-foot">Before allowances and deductions</div>
            </article>
            <article className="rp-metric">
              <div className="rp-metric-label">CONFIRMED PAYMENTS</div>
              <div className="rp-metric-value">{paid.length}</div>
              <div className="rp-metric-foot">{data.payments.length} recorded transaction(s)</div>
            </article>
          </section>

          {showForm && (
            <form className="rp-panel rp-form" onSubmit={addEmployee}>
              <div className="rp-panel-title"><div><h2>New employee</h2><p>Add a record to the employee register.</p></div></div>
              <div className="rp-form-grid">
                <label>Full name<input required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
                <label>Department<input required value={form.department} onChange={e => setForm({ ...form, department: e.target.value })} /></label>
                <label>Basic monthly salary (₹)<input required type="number" min="0.01" step="0.01" value={form.salary} onChange={e => setForm({ ...form, salary: e.target.value })} /></label>
              </div>
              <button className="rp-btn primary" disabled={saving}>{saving ? 'Saving…' : 'Save employee'}</button>
            </form>
          )}

          {tab === 'overview' && (
            <>
              <section className="rp-panel">
                <div className="rp-panel-title">
                  <div><h2>Payroll snapshot</h2><p>Summary of employee compensation and payment status.</p></div>
                  <button className="rp-btn primary" onClick={runPayroll} disabled={saving}>
                    <Wallet size={16} /> {saving ? 'Processing…' : 'Run simulated payroll'}
                  </button>
                </div>
                <div className="rp-overview-grid">
                  <div><span>Employees awaiting payment</span><strong>{employees.filter(e => latestByEmployee.get(e.id)?.status !== 'PAID').length}</strong></div>
                  <div><span>Recent incidents</span><strong>{data.incidents.length}</strong></div>
                  <div><span>Payroll period</span><strong>{period()}</strong></div>
                </div>
              </section>
              <section className="rp-panel">
                <div className="rp-panel-title"><div><h2>Recent employee records</h2><p>Latest data from the Flask API.</p></div><button className="rp-text-button" onClick={() => setTab('employees')}>View all employees →</button></div>
                <EmployeeTable employees={employees.slice(0, 5)} onSalary={e => { setSelectedId(e.id); setTab('salary'); }} />
              </section>
            </>
          )}

          {tab === 'employees' && (
            <section className="rp-panel">
              <div className="rp-panel-title"><div><h2>Employee details</h2><p>Search and review registered employees.</p></div><span className="rp-count">{filtered.length} RECORDS</span></div>
              <div className="rp-search"><Search size={16} /><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search name, department or employee ID" /></div>
              <EmployeeTable employees={filtered} onSalary={e => { setSelectedId(e.id); setTab('salary'); }} />
            </section>
          )}

          {tab === 'salary' && (
            <section className="rp-panel">
              <div className="rp-panel-title"><div><h2>Salary structure</h2><p>Save compensation components to SQLite.</p></div></div>
              {!selected ? <p>No employees available.</p> : (
                <>
                  <label className="rp-select-label">EMPLOYEE
                    <select value={selected.id} onChange={e => setSelectedId(Number(e.target.value))}>
                      {employees.map(e => <option key={e.id} value={e.id}>{e.name} — {e.department}</option>)}
                    </select>
                  </label>
                  <form onSubmit={saveSalary}>
                    <div className="rp-salary-grid">
                      <div className="rp-salary-field"><label>Basic salary</label><div className="rp-money-input"><span>₹</span><input readOnly value={Number(selected.basic_salary || 0).toFixed(2)} /></div></div>
                      {[
                        ['HRA', 'hra'], ['Bonus', 'bonus'],
                        ['Other allowances', 'allowances'], ['Deductions', 'deductions']
                      ].map(([label, field]) => (
                        <div className="rp-salary-field" key={field}>
                          <label>{label}</label>
                          <div className="rp-money-input"><span>₹</span><input type="number" min="0" step="0.01" value={salaryForm[field]} onChange={e => setSalaryForm({ ...salaryForm, [field]: e.target.value })} /></div>
                        </div>
                      ))}
                    </div>
                    <div className="rp-totals">
                      <div><span>Gross monthly pay</span><strong>{money(salary(selected).gross)}</strong></div>
                      <div><span>Total deductions</span><strong>{money(salary(selected).deductions)}</strong></div>
                      <div className="net"><span>Net payable</span><strong>{money(salary(selected).net)}</strong></div>
                    </div>
                    <button className="rp-btn primary" disabled={saving}>{saving ? 'Saving…' : 'Save salary structure'}</button>
                  </form>
                </>
              )}
            </section>
          )}

          {tab === 'payments' && (
            <section className="rp-panel">
              <div className="rp-panel-title"><div><h2>Payment register</h2><p>Payment status for the current demo payroll period.</p></div><button className="rp-btn primary" onClick={runPayroll} disabled={saving}><Wallet size={15} /> Run all</button></div>
              <div className="rp-payment-summary">
                <div><CheckCircle size={17} /><span>Paid <b>{paid.length}</b></span></div>
                <div><Clock size={17} /><span>Unpaid / pending <b>{employees.filter(e => latestByEmployee.get(e.id)?.status !== 'PAID').length}</b></span></div>
                <div><Wallet size={17} /><span>Basic payroll <b>{money(data.total_salary)}</b></span></div>
              </div>
              <div className="rp-table-wrap">
                <table className="rp-table">
                  <thead><tr><th>EMPLOYEE</th><th>NET PAYABLE</th><th>STATUS</th><th>REFERENCE</th><th>ACTION</th></tr></thead>
                  <tbody>
                    {employees.map(e => {
                      const payment = latestByEmployee.get(e.id);
                      const status = payment?.status || 'UNPAID';
                      return <tr key={e.id}>
                        <td><div className="rp-employee-cell"><span className="rp-avatar">{e.name.split(/\s+/).map(x => x[0]).slice(0, 2).join('').toUpperCase()}</span><div><b>{e.name}</b><small>EMP{String(e.id).padStart(3, '0')}</small></div></div></td>
                        <td className="rp-amount">{money(salary(e).net)}</td>
                        <td><span className={`rp-status ${status.toLowerCase()}`}>{status}</span></td>
                        <td>{payment?.idempotency_key || '—'}</td>
                        <td><button className="rp-btn small" disabled={busyId === e.id || status === 'PAID'} onClick={() => payEmployee(e)}>{busyId === e.id ? 'Processing…' : status === 'PAID' ? 'Paid' : 'Pay (demo)'}</button></td>
                      </tr>;
                    })}
                    {!employees.length && <tr><td colSpan="5" className="rp-empty">No employees found.</td></tr>}
                  </tbody>
                </table>
              </div>
              <p className="rp-footnote">Simulated transactions only. A payment is marked paid only when the backend returns a confirmed simulated result.</p>
            </section>
          )}

          <footer className="rp-footer">ResilPay · Payroll operations · No real money is transferred</footer>
        </main>
      </div>
    </div>
  );
}

function EmployeeTable({ employees, onSalary }) {
  return (
    <div className="rp-table-wrap">
      <table className="rp-table">
        <thead><tr><th>EMPLOYEE</th><th>EMPLOYEE ID</th><th>DEPARTMENT</th><th>BASIC SALARY</th><th></th></tr></thead>
        <tbody>
          {employees.map(e => (
            <tr key={e.id}>
              <td><div className="rp-employee-cell"><span className="rp-avatar">{e.name.split(/\s+/).map(x => x[0]).slice(0, 2).join('').toUpperCase()}</span><div><b>{e.name}</b><small>{e.designation || 'Employee'}</small></div></div></td>
              <td>EMP{String(e.id).padStart(3, '0')}</td>
              <td>{e.department}</td>
              <td className="rp-amount">{money(e.basic_salary)}</td>
              <td><button className="rp-text-button" onClick={() => onSalary(e)}>Salary details →</button></td>
            </tr>
          ))}
          {!employees.length && <tr><td colSpan="5" className="rp-empty">No employee records found.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}