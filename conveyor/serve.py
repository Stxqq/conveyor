from __future__ import annotations

from http.server import ThreadingHTTPServer
from typing import Any

from .httpjson import JSONHandler
from .registry import ModelRegistry, ModelVersion


def make_server(version: ModelVersion, host: str, port: int) -> ThreadingHTTPServer:
    """JSON prediction endpoint for one registered model.

    The model object needs a ``predict(rows)`` method that takes a list of
    dicts (one per record) and returns one score per row.
    """
    model = version.load()
    if not callable(getattr(model, "predict", None)):
        raise TypeError(f"{version.ref} has no predict(rows) method")

    class Handler(JSONHandler):
        def do_GET(self) -> None:
            if self.path == "/health":
                self.send_json({"status": "ok", "model": version.ref})
            elif self.path in ("/", "/card"):
                self.send_json(version.card)
            else:
                self.send_error_json(404, "try POST /predict, GET /card or GET /health")

        def do_POST(self) -> None:
            if self.path != "/predict":
                self.send_error_json(404, "not found")
                return
            try:
                body = self.read_json()
                rows = body["rows"] if isinstance(body, dict) else None
                if not isinstance(rows, list) or not all(
                    isinstance(r, dict) for r in rows
                ):
                    raise ValueError('expected {"rows": [{...}, ...]}')
                scores: Any = model.predict(rows)
            except (ValueError, KeyError, TypeError) as exc:
                self.send_error_json(400, str(exc))
                return
            self.send_json(
                {"model": version.ref, "predictions": [float(s) for s in scores]}
            )

    return ThreadingHTTPServer((host, port), Handler)


def serve_model(workspace: str, ref: str, host: str, port: int, out: Any) -> None:
    version = ModelRegistry(workspace).resolve(ref)
    server = make_server(version, host, port)
    out.line(f"serving {version.ref} on http://{host}:{port}  (POST /predict)")
    try:
        server.serve_forever()
    finally:
        server.server_close()
