from __future__ import annotations

import json
import mimetypes
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ..events import follow, read_events
from ..httpjson import JSONHandler
from ..lineage import Lineage
from ..registry import ModelRegistry

STATIC = Path(__file__).parent / "static"


def make_server(workspace: str | Path, host: str, port: int) -> ThreadingHTTPServer:
    """Static frontend plus a small read-only JSON API over the workspace.

    GET /api/runs                    recent runs with step counts
    GET /api/runs/<id>               one run: steps, artifacts, metrics
    GET /api/runs/<id>/events        the full event list
    GET /api/runs/<id>/stream        server-sent events, live until the run ends
    GET /api/artifacts/<id>          producer, upstream lineage and consumers
    GET /api/models                  registry: versions, cards, champion
    """
    workspace = Path(workspace)
    lineage = Lineage(workspace / "conveyor.db")
    registry = ModelRegistry(workspace)
    runs_dir = workspace / "runs"

    class Handler(JSONHandler):
        def do_GET(self) -> None:
            url = urlparse(self.path)
            parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
            if parts[:1] != ["api"]:
                self.send_static(url.path)
                return
            try:
                self.route(parts[1:], parse_qs(url.query))
            except LookupError as exc:
                self.send_error_json(404, str(exc))

        def route(self, parts: list[str], query: dict[str, list[str]]) -> None:
            if parts == ["runs"]:
                limit = int(query.get("limit", ["50"])[0])
                self.send_json(lineage.runs(limit=limit))
            elif len(parts) >= 2 and parts[0] == "runs":
                run_id = lineage.resolve_run(parts[1])
                if run_id is None:
                    raise LookupError(f"no run {parts[1]!r}")
                tail = parts[2:]
                if not tail:
                    self.send_json(lineage.run(run_id))
                elif tail == ["events"]:
                    self.send_json(read_events(runs_dir / f"{run_id}.jsonl"))
                elif tail == ["stream"]:
                    self.stream(runs_dir / f"{run_id}.jsonl")
                else:
                    raise LookupError("unknown endpoint")
            elif len(parts) == 2 and parts[0] == "artifacts":
                artifact = parts[1]
                self.send_json(
                    {
                        "id": artifact,
                        "producer": lineage.producer(artifact),
                        "upstream": lineage.upstream(artifact),
                        "consumers": lineage.consumers(artifact),
                    }
                )
            elif parts == ["models"]:
                self.send_json(
                    [
                        {
                            "name": name,
                            "champion": (c := registry.champion(name)) and c.version,
                            "versions": [v.card for v in registry.versions(name)],
                        }
                        for name in registry.names()
                    ]
                )
            else:
                raise LookupError("unknown endpoint")

        def stream(self, path: Path) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for event in follow(path):
                    chunk = f"id: {event['seq']}\ndata: {json.dumps(event)}\n\n"
                    self.wfile.write(chunk.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def send_static(self, path: str) -> None:
            target = (STATIC / path.lstrip("/")).resolve()
            if target.is_dir():
                target = target / "index.html"
            if not target.is_relative_to(STATIC.resolve()) or not target.is_file():
                self.send_error_json(404, "not found")
                return
            body = target.read_bytes()
            kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def serve_ui(workspace: str, host: str, port: int, out: Any) -> None:
    server = make_server(workspace, host, port)
    out.line(f"conveyor ui on http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
