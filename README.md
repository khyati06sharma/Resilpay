# ResilPay — Self-Healing Payroll Demo

ResilPay is a college demo using a Flask + SQLite API, a simulated payroll service, and two React interfaces. It does not move real money.

## Requirements
- Python 3.10+
- Node.js 20.19+ or 22.12+
- npm

## Start the project (three terminals)

**Terminal 1 — backend API** (from the `resilpay` folder):
```powershell
python -m pip install -r requirements.txt
python app.py
```

**Terminal 2 — simulated external payroll service** (same project folder):
```powershell
python payroll_service.py
```

**Terminal 3 — Payroll App**:
```powershell
cd frontend/payroll-app
npm install
npm run dev
```
Open http://localhost:5173/.

**Terminal 4 — Control Center**:
```powershell
cd frontend/control-center
npm install
npm run dev
```
Open http://localhost:5174/.

Keep all four terminals running. Both Vite apps proxy `/api/*` to the same Flask API on port 5000.

## Demo sequence
1. Open Payroll App and verify employee records and monthly salary total.
2. Click **Run simulated payroll**. The UI will show transaction history and `PAID` statuses for simulated transactions only.
3. Open Control Center and confirm service health.
4. Click one chaos button at a time: network/service outage, database failure, API failure, payment failure.
5. Watch the incident timeline refresh. The database/API/payment chaos switches clear after 8 seconds; the network scenario uses the external mock service and retry loop. Confirm final health before starting the next test.
6. For a normal-vs-chaos comparison, record baseline response and transaction statuses before injecting a failure, then record incident timestamps, retry/recovery time, and final status.

## Safety notes
- All payment transactions are simulated; no real payment gateway is connected.
- Payment run requests accept a `run_id`; use the same `run_id` to make a repeated request idempotent for the same employee set.
- If a request times out, verify the transaction status before starting a new payroll run. Do not treat an unknown outcome as paid.
- Database writes use a SQLite transaction and roll back if the payroll batch raises an error.
- Chaos buttons are for local demonstration only; do not expose the development servers to the public internet.

## Current implementation notes
- Incident records are persisted to SQLite with failure type, status, action, detection/recovery timing, and timestamps.
- Database, API and payment chaos are controlled simulation switches that recover automatically after 8 seconds; they do not kill the database or corrupt stored data.
- Network chaos depends on the separate `payroll_service.py` process being available.
- Application-process crash recovery is not one of the four selected chaos scenarios.
- Run `python -m py_compile app.py database.py healing.py payroll_service.py` to check Python syntax.
