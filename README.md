# ResilPay — Self-Healing Payroll System

ResilPay is an academic project that demonstrates how a payroll application can detect service problems, attempt recovery from temporary failures, and show system health and incident information to an administrator.

The project includes a Payroll app, a separate Control Center, a Flask backend, an SQLite database, and a local mock payroll service for testing.

> **Project scope:** ResilPay is a local demonstration. The mock payroll service is used to simulate failures; this project is not connected to a live bank or payment gateway.

## Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Technology Stack](#technology-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Running the Applications](#running-the-applications)
- [Testing Self-Healing](#testing-self-healing)
- [API Endpoints](#api-endpoints)
- [Limitations](#limitations)
- [Future Improvements](#future-improvements)

## Overview

Payroll systems need reliable service availability because failures can delay processing or leave an operation's status uncertain. ResilPay explores a simple approach to resilience:

1. Check whether the local payroll service is available.
2. Detect a temporary service interruption.
3. Retry the connection using configured retry delays.
4. Track recovery state and make incident information available for review.

The goal is to make failures easier to detect and investigate—not to claim that every payment failure can be fixed automatically.

## Features

- **Payroll interface** — view employee and payroll information.
- **Control Center** — monitor system health and review incidents.
- **Employee records** — store employee information in SQLite.
- **Incident history** — record system incidents and recovery status.
- **Network failure simulation** — test behavior when the local mock payroll service becomes unavailable.
- **Health checks and retries** — the healing logic checks the mock service and attempts reconnection.
- **Recovery visibility** — track the system's reported recovery state and timing.
- **Payment-status workflow** — includes a payment verification endpoint in the backend.

Only describe a failure scenario as successfully recovered after confirming the result in the running application.

## Architecture

```text
+----------------------+       +-----------------------+
| Payroll React App    |       | React Control Center  |
| Employee / payroll   |       | Health / incidents    |
+-----------+----------+       +-----------+-----------+
            |                              |
            +-------------+----------------+
                          |
                          v
                +-------------------+
                | Flask Backend     |
                | app.py : 5000     |
                +----+----------+---+
                     |          |
                     v          v
             +-------------+  +----------------------+
             | SQLite DB   |  | Healing Logic        |
             | resilpay.db |  | healing.py           |
             | employees   |  +----------+-----------+
             | incidents   |             |
             +-------------+             | health check / retry
                                         v
                              +------------------------+
                              | Mock Payroll Service   |
                              | payroll_service.py     |
                              | :5001 /health          |
                              +------------------------+
```

### Main components

| Component | Responsibility |
|---|---|
| Payroll React app | Provides the payroll and employee interface. |
| Control Center React app | Displays system health, incidents, and recovery information. |
| Flask backend (`app.py`) | Handles application requests and status endpoints on port `5000`. |
| SQLite (`resilpay.db`) | Stores employee and incident records. |
| Healing logic (`healing.py`) | Checks the mock service's health and attempts reconnection with retries. |
| Mock payroll service (`payroll_service.py`) | Local dependency used to test service availability and simulated outages on port `5001`. |

### Healing configuration

The current healing configuration is described as follows:

- Health-check URL: `http://127.0.0.1:5001/health`
- Request timeout: `1` second
- Retry delays: `1`, `2`, `4`, and `4` seconds

These settings do **not** guarantee a fixed recovery time. Measure recovery time during a test, from failure injection until the application confirms recovery.

## Technology Stack

- **Frontend:** React
- **Backend:** Python and Flask
- **Database:** SQLite
- **Local dependency:** Python mock payroll service
- **Healing:** Python health checks, retries, and recovery-state tracking
- **Icons:** Lucide React (where used in the frontend)

## Project Structure

The repository contains the main backend files and separate frontend applications. The key paths include:

```text
resilpay/
├── app.py
├── database.py
├── healing.py
├── payroll_service.py
├── recovery_queue.py
├── cache.py
├── resilpay.db
├── requirements.txt
├── frontend/
│   ├── payroll-app/
│   └── control-center/
└── ...
```

Some additional files and folders may be present in the repository.

## Getting Started

### Requirements

- Python installed on your computer
- Node.js and npm installed
- Git (optional, for cloning the repository)

### 1. Get the project

If you are cloning from GitHub:

```bash
git clone <YOUR-GITHUB-REPOSITORY-URL>
cd Resilpay
```

Replace the placeholder with your repository URL. If you already have the project folder, open that folder in VS Code instead.

### 2. Set up the Python environment

From the project root in Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell blocks environment activation, you can run the Python commands from your configured terminal or follow your system's Python environment policy.

### 3. Install frontend dependencies

Open a terminal for each frontend folder and install its dependencies:

```powershell
cd frontend\payroll-app
npm install
```

Then, in a separate terminal:

```powershell
cd frontend\control-center
npm install
```

Check each folder's `package.json` for its available scripts. Use the script listed there to start the development server; the exact script name can vary by project setup.

## Running the Applications

Start each service in a separate terminal, from the appropriate project directory:

| Service | Entry point | Expected port |
|---|---|---:|
| Flask backend | `app.py` | `5000` |
| Mock payroll service | `payroll_service.py` | `5001` |
| Payroll frontend | `frontend/payroll-app` | Check its `package.json` |
| Control Center frontend | `frontend/control-center` | Check its `package.json` |

For the Python services, the usual commands from the project root are:

```powershell
python app.py
```

```powershell
python payroll_service.py
```

Run these in separate terminals. If your project has different startup instructions or environment settings, follow the repository's current configuration.

## Testing Self-Healing

A basic network recovery demonstration can follow this sequence:

1. Start the backend and mock payroll service.
2. Confirm the mock service is healthy.
3. Open the Control Center and note the normal status.
4. Use the application's Chaos Testing interface or the configured network-chaos endpoint to trigger a simulated outage.
5. Observe whether the system detects the issue and begins retrying.
6. Wait for the mock service to become available again.
7. Confirm the reported status and incident record.
8. Record the recovery time from the actual test.

The project includes a mock-service outage route, `/chaos/outage`, and a backend network-chaos route, `/chaos/network`. Use the application's expected request method and payload for your current implementation.

For a report or demo, use measured results. Do not invent recovery times or claim that database, API, or payment failure scenarios recover automatically unless you have tested them.

## API Endpoints

The backend includes the following known routes. Confirm the current implementation in `app.py` before relying on them, because request methods and payloads may matter.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Returns backend health/status information. |
| `GET /dashboard-data` | Provides dashboard data. |
| `GET /network-status` | Provides network-healing status. |
| `POST /chaos/network` | Triggers the configured network-failure simulation. |
| `POST /add-employee` | Adds an employee using the expected request body. |
| `POST /payments/<payment_id>/verify` | Verifies or checks the status of a payment record. |

The mock payroll service also exposes `GET /health` and a `/chaos/outage` route for simulated outages. Check `payroll_service.py` for the exact request method and payload.

## Limitations

- **Local simulation:** The mock payroll service is not a real bank or payment gateway.
- **Not every failure can be healed:** Retries can help with temporary availability problems, but cannot fix every database fault, invalid input, rejected payment, or persistent outage.
- **Payment uncertainty:** If a payment request times out, its true outcome may be uncertain. A production system would need safe reconciliation, idempotency, and authoritative payment-status checks before retrying.
- **Testing is important:** Database, API, and payment recovery should only be reported as successful after repeatable tests confirm the behavior.
- **Recovery time varies:** Results depend on outage duration, retry timing, machine load, and when recovery is confirmed.

## Future Improvements

- Add automated repeatable tests for each failure type.
- Record detection and recovery timestamps automatically.
- Improve administrator notifications and incident summaries.
- Add safer payment reconciliation and idempotency controls.
- Test persistent failures and recovery after application restarts.
- Add stronger security, access control, backups, and carefully managed payment-provider integration before any real-world use.

## Academic Project Note

ResilPay is an academic demonstration of resilience concepts. Its purpose is to show health checking, retry-based recovery for a local mock dependency, recovery-state tracking, and incident visibility. Actual results should be documented from the running application and test evidence.

---

**Author:** Add your name and course details here before publishing, if required by your college.
