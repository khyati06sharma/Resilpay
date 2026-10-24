# ResilPay Control Center

Separate React/Vite monitoring frontend. Run `npm install` and `npm run dev`; default URL is http://localhost:5174. Requests to `/api/*` are proxied to Flask on port 5000.

Currently available test: simulated external payroll-service outage via `/chaos/network`. Database/API/payment chaos endpoints and durable incident writes are not implemented in the supplied backend yet, so the UI does not present those tests as working.
