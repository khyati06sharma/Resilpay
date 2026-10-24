
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

outage_until = 0.0
outage_lock = threading.Lock()


class PayrollHandler(BaseHTTPRequestHandler):
    def send_json(self, status_code, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            body = b"ResilPay Mock Payroll Service is running."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/health":
            with outage_lock:
                unavailable = time.monotonic() < outage_until

            if unavailable:
                self.send_json(503, {
                    "status": "unhealthy",
                    "service": "mock-payroll",
                    "message": "Injected service outage"
                })
                return

            self.send_json(200, {
                "status": "healthy",
                "service": "mock-payroll"
            })
            return

        self.send_error(404)

    def do_POST(self):
        global outage_until

        if self.path != "/chaos/outage":
            self.send_error(404)
            return

        # Read the optional JSON body.
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b"{}"

        try:
            data = json.loads(raw_body)
            seconds = float(data.get("seconds", 6))
        except (ValueError, TypeError, json.JSONDecodeError):
            self.send_json(400, {"error": "Invalid JSON or duration"})
            return

        if not 1 <= seconds <= 30:
            self.send_json(400, {
                "error": "Duration must be between 1 and 30 seconds"
            })
            return

        with outage_lock:
            outage_until = time.monotonic() + seconds

        self.send_json(200, {
            "started": True,
            "status": "unhealthy",
            "outage_seconds": seconds,
            "message": "Chaos outage injected successfully"
        })

    def log_message(self, format, *args):
        print("[Payroll Service]", format % args)


if __name__ == "__main__":
    server = ThreadingHTTPServer(
        ("127.0.0.1", 5001),
        PayrollHandler
    )
    print("Mock payroll service running at http://127.0.0.1:5001")
    server.serve_forever()
