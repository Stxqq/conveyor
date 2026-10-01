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
from ..store import ArtifactStore

STATIC = Path(__file__).parent / "static"
# Model cards, gate decisions and drift reports are a few kB; anything bigger
# is data, and the browser has no business parsing it.
MAX_VALUE_BYTES = 256 * 1024


def make_server(workspace: str | Path, host: str, port: int) -> ThreadingHTTPServer:
    """Static frontend plus a small read-only JSON API over the workspace.

    GET /api/runs                    recent runs with step counts
    GET /api/runs/<id>               one run: steps, artifacts, metrics
    GET /api/runs/<id>/events        the full event list
    GET /api/runs/<id>/stream        server-sent events, live until the run ends
    GET /api/artifacts/<id>          producer, upstream lineage and consumers
    GET /api/artifacts/<id>/value    the stored value, for small json artifacts
    GET /api/models                  registry: versions, cards, champion
    """
    workspace = Path(workspace)
    lineage = Lineage(workspace / "conveyor.db")
    registry = ModelRegistry(workspace)
    store = ArtifactStore(workspace)
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
            except (LookupError, FileNotFoundError) as exc:
                # FileNotFoundError: a run row whose events file was pruned
                self.send_error_json(404, str(exc))
            except ValueError as exc:
                self.send_error_json(400, str(exc))

        def route(self, parts: list[str], query: dict[str, list[str]]) -> None:
            if parts == ["runs"]:
                limit = max(1, min(int(query.get("limit", ["50"])[0]), 500))
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
                    self.stream(run_id, runs_dir / f"{run_id}.jsonl")
                else:
                    raise LookupError("unknown endpoint")
            elif len(parts) == 3 and parts[0] == "artifacts" and parts[2] == "value":
                self.send_value(parts[1])
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

        def send_value(self, artifact_id: str) -> None:
            found = lineage.artifact(artifact_id)
            if found is None or not store.exists(artifact_id, found["kind"]):
                raise LookupError(f"no artifact {artifact_id!r}")
            if found["kind"] != "json" or found["size"] > MAX_VALUE_BYTES:
                self.send_error_json(
                    415, f"only json artifacts up to {MAX_VALUE_BYTES} bytes"
                )
                return
            self.send_json(store.get(artifact_id, "json"))

        def stream(self, run_id: str, path: Path) -> None:
            if not path.exists():
                raise FileNotFoundError(f"run {run_id} has no events file")

            def still_running() -> bool:
                found = lineage.run(run_id)
                return found is not None and found["status"] == "running"

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for event in follow(path, still_running=still_running):
                    if event is None:
                        chunk = ": keepalive\n\n"
                    else:
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
            if target.name == "index.html":
                # the same page is published as a static demo of recorded runs
                body = body.replace(
                    b'name="conveyor-source" content="recorded"',
                    b'name="conveyor-source" content="live"',
                )
            kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = _Server((host, port), Handler)
    server.daemon_threads = True
    return server


class _Server(ThreadingHTTPServer):
    # A browser opens a burst of connections for the page's modules; with the
    # default backlog of 5 some of them get reset on macOS.
    request_queue_size = 64


def serve_ui(workspace: str, host: str, port: int, out: Any) -> None:
    server = make_server(workspace, host, port)
    out.line(f"conveyor ui on http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
