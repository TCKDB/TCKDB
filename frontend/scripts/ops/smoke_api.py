"""Tiny deterministic API used only by the frontend container smoke test."""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API spelling
        body = json.dumps(
            {
                "status": "ok",
                "degraded": [],
                "response": os.environ.get("API_RESPONSE", "api"),
                "headers": {
                    "x_real_ip": self.headers.get("X-Real-IP"),
                    "x_forwarded_proto": self.headers.get("X-Forwarded-Proto"),
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


HTTPServer(("", 8010), Handler).serve_forever()
