from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from typing import Any

from .events import json_safe

MAX_BODY = 10 * 1024 * 1024


class JSONHandler(BaseHTTPRequestHandler):
    server_version = "conveyor"

    def send_json(self, value: Any, status: int = 200) -> None:
        body = json.dumps(json_safe(value), default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status)

    def read_json(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("request body too large")
        return json.loads(self.rfile.read(length) or b"null")

    def log_message(self, format: str, *args: Any) -> None:
        pass
