# ResilPay Payroll Workspace

Separate React/Vite payroll frontend. Run `npm install` and `npm run dev`; default URL is http://localhost:5173. Requests to `/api/*` are proxied to Flask on port 5000.

Currently implemented: employee list, add employee, and monthly basic salary total backed by SQLite. Payroll runs and payment transactions are not yet implemented; the UI labels payment processing as not configured.
