from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .dag import Pipeline, PipelineError
from .events import read_events
from .executor import Executor
from .lineage import Lineage
from .loader import load_pipeline
from .registry import ModelRegistry

STATUS_COLOR = {
    "succeeded": "32",
    "cached": "36",
    "failed": "31",
    "skipped": "90",
    "retry": "33",
    "running": "33",
    "cancelled": "90",
}


class Out:
    def __init__(self, stream: Any = None) -> None:
        self.stream = stream or sys.stdout
        self.color = self.stream.isatty() and "NO_COLOR" not in os.environ
        self.closed = False

    def paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def dim(self, text: str) -> str:
        return self.paint(text, "2")

    def status(self, status: str, width: int = 10) -> str:
        return self.paint(status.ljust(width), STATUS_COLOR.get(status, "0"))

    def line(self, text: str = "") -> None:
        if self.closed:
            return
        try:
            print(text, file=self.stream, flush=True)
        except BrokenPipeError:
            # Piped into `head`: keep running, stop printing, and point stdout
            # at /dev/null so the interpreter doesn't complain on exit.
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            self.closed = True


def human_size(n: int | None) -> str:
    if n is None:
        return ""
    size = float(n)
    for unit in ("B", "kB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 120:
        return f"{seconds:.2f} s"
    return f"{seconds / 60:.1f} min"


def ago(ts: float) -> str:
    delta = time.time() - ts
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if delta >= size:
            return f"{delta // size:.0f}{unit} ago"
    return "just now"


def format_number(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (int, float)):
        if float(value).is_integer() and abs(value) < 1e12:
            return str(int(value))
        return f"{value:.4g}"
    return str(value)


def format_metrics(metrics: dict[str, Any]) -> str:
    return "   ".join(f"{k} {format_number(v)}" for k, v in metrics.items())


def metric_lines(metrics: dict[str, Any], per_line: int = 4) -> list[str]:
    items = list(metrics.items())
    return [
        format_metrics(dict(items[i : i + per_line]))
        for i in range(0, len(items), per_line)
    ]


def coerce_param(raw: str, current: Any) -> Any:
    if isinstance(current, bool):
        lowered = raw.lower()
        if lowered not in ("true", "false", "1", "0", "yes", "no"):
            raise ValueError(f"expected a boolean, got {raw!r}")
        return lowered in ("true", "1", "yes")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, str):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def parse_params(pairs: list[str], pipeline: Pipeline) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep:
            raise PipelineError(f"--param expects key=value, got {pair!r}")
        try:
            params[key] = coerce_param(raw, pipeline.params.get(key))
        except ValueError as exc:
            raise PipelineError(f"--param {key}: {exc}") from None
    return params


class Progress:
    """Prints one line per finished step while a run is going, followed by
    the metrics and log lines the step produced."""

    def __init__(self, out: Out) -> None:
        self.out = out
        self.width = 12
        self.notes: dict[str, list[str]] = {}
        self.metrics: dict[str, dict[str, Any]] = {}

    def __call__(self, event: dict[str, Any]) -> None:
        kind, out = event["type"], self.out
        step = event.get("step", "")
        if kind == "run_started":
            self.width = max(len(s["name"]) for s in event["graph"]) + 2
            cache = "cache on" if event["cache"] else "cache off"
            out.line(
                f"{out.paint(event['pipeline'], '1')}  run {event['run_id']}  "
                + out.dim(f"{len(event['graph'])} steps, {event['workers']} workers, ")
                + out.dim(cache)
            )
            out.line()
        elif kind == "metric":
            self.metrics.setdefault(step, {})[event["name"]] = event["value"]
        elif kind == "log":
            self.notes.setdefault(step, []).append(event["message"])
        elif kind in ("step_succeeded", "step_cached"):
            status = "succeeded" if kind == "step_succeeded" else "cached"
            took = human_duration(event.get("duration")) if status != "cached" else ""
            self.row(
                status,
                step,
                took.rjust(9)
                + "   "
                + out.dim(f"{event['kind']:6} {human_size(event['size']):>8}"),
            )
            metrics = self.metrics.pop(step, None) or event.get("metrics") or {}
            self.details(metrics, self.notes.pop(step, []))
        elif kind == "step_retry":
            self.row(
                "retry",
                step,
                out.dim(
                    f"attempt {event['attempt']} failed ({event['error']}),"
                    f" again in {event['delay']:.2f}s"
                ),
            )
        elif kind == "step_failed":
            self.row(
                "failed",
                step,
                human_duration(event["duration"]).rjust(9)
                + "   "
                + out.paint(event["error"].splitlines()[0], "31"),
            )
        elif kind == "step_skipped":
            self.row("skipped", step, out.dim(f"upstream {event['because']} failed"))
        elif kind == "run_finished":
            counts = event["counts"]
            out.line()
            out.line(
                f"{out.status(event['status'], 0)} in "
                f"{human_duration(event['duration'])}  "
                + out.dim(
                    f"{counts['succeeded']} ran, {counts['cached']} cached, "
                    f"{counts['failed']} failed, {counts['skipped']} skipped"
                )
            )

    def row(self, status: str, step: str, rest: str) -> None:
        self.out.line(f"  {self.out.status(status)} {step.ljust(self.width)}{rest}")

    def details(self, metrics: dict[str, Any], notes: list[str]) -> None:
        indent = " " * (13 + self.width)
        for chunk in metric_lines(metrics):
            self.out.line(indent + self.out.dim(chunk))
        for note in notes:
            self.out.line(indent + self.out.dim(note))


def cmd_run(args: argparse.Namespace, out: Out) -> int:
    pipeline = load_pipeline(args.pipeline)
    params = parse_params(args.param, pipeline)
    executor = Executor(
        args.workspace,
        max_workers=args.workers,
        cache=not args.no_cache,
        listeners=[] if args.quiet else [Progress(out)],
    )
    result = executor.run(pipeline, params, source=str(Path(args.pipeline).resolve()))
    for name, outcome in result.steps.items():
        if outcome.status == "failed":
            trace = _traceback_for(result.events_path, name)
            out.line()
            out.line(
                out.paint(f"{name} failed after {outcome.attempts} attempt(s)", "31")
            )
            out.line(trace or outcome.error or "")
    if args.quiet:
        out.line(f"{result.id} {result.status}")
    return 0 if result.ok else 1


def _traceback_for(events_path: Path, step: str) -> str | None:
    for event in read_events(events_path):
        if event["type"] == "step_traceback" and event["step"] == step:
            return event["traceback"]
    return None


def _lineage(args: argparse.Namespace) -> Lineage:
    return Lineage(Path(args.workspace) / "conveyor.db")


def cmd_runs(args: argparse.Namespace, out: Out) -> int:
    runs = _lineage(args).runs(limit=args.limit)
    if args.json:
        print(json.dumps(runs, indent=2))
        return 0
    if not runs:
        out.line("no runs yet; try `conveyor run examples/churn/pipeline.py`")
        return 0
    for r in runs:
        steps = f"{r['succeeded'] or 0} ran  {r['cached'] or 0} cached"
        if r["failed"]:
            steps += f"  {r['failed']} failed  {r['skipped'] or 0} skipped"
        out.line(
            f"{r['id']}  {r['pipeline'].ljust(10)} {out.status(r['status'])} "
            f"{human_duration(r.get('duration')).rjust(9)}  {steps.ljust(40)}"
            + out.dim(ago(r["started_at"]))
        )
    return 0


def cmd_show(args: argparse.Namespace, out: Out) -> int:
    lineage = _lineage(args)
    run_id = lineage.resolve_run(args.run)
    if run_id is None:
        out.line(f"no run matches {args.run!r}")
        return 1
    if args.events:
        events = read_events(Path(args.workspace) / "runs" / f"{run_id}.jsonl")
        print(json.dumps(events, indent=1))
        return 0
    run = lineage.run(run_id)
    assert run is not None
    if args.json:
        print(json.dumps(run, indent=2))
        return 0

    out.line(f"{out.paint(run['pipeline'], '1')}  run {run['id']}")
    out.line(
        f"{out.status(run['status'], 0)}  {human_duration(run.get('duration'))}  "
        + out.dim(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run["started_at"])))
    )
    if run["params"]:
        out.line(
            out.dim("params  ")
            + "  ".join(f"{k}={v}" for k, v in run["params"].items())
        )
    out.line()
    width = max(len(s["step"]) for s in run["steps"]) + 2
    for s in run["steps"]:
        took = ""
        if s["status"] == "succeeded":
            took = human_duration(s["finished_at"] - s["started_at"])
        artifact = (s["output"] or "")[:12]
        out.line(
            f"  {out.status(s['status'])} {s['step'].ljust(width)}{took.rjust(9)}  "
            + out.dim(
                f"{artifact:12}  {(s['kind'] or ''):6} {human_size(s['size']):>8}"
            )
            + (out.dim(f"  x{s['attempts']}") if s["attempts"] > 1 else "")
        )
        for chunk in metric_lines(s["metrics"]):
            out.line(out.dim(f"  {'':10} {'':{width}}{chunk}"))
        if s["error"] and s["status"] == "failed":
            for line in s["error"].splitlines():
                out.line(f"  {'':10} {'':{width}}" + out.paint(line, "31"))
    return 0


def cmd_models(args: argparse.Namespace, out: Out) -> int:
    registry = ModelRegistry(args.workspace)
    names = [args.name] if args.name else registry.names()
    if not names:
        out.line("the registry is empty")
    for name in names:
        champion = registry.champion(name)
        out.line(out.paint(name, "1"))
        for v in registry.versions(name):
            mark = "champion" if champion and v.version == champion.version else ""
            metrics = v.card.get("metrics", {})
            out.line(
                f"  v{v.version:<4} {v.digest[:12]}  "
                + out.dim(format_metrics({k: metrics[k] for k in list(metrics)[:3]}))
                + ("  " + out.paint(mark, "32") if mark else "")
            )
    return 0


def cmd_ui(args: argparse.Namespace, out: Out) -> int:
    from .ui.server import serve_ui

    serve_ui(args.workspace, args.host, args.port, out)
    return 0


def cmd_serve(args: argparse.Namespace, out: Out) -> int:
    from .serve import serve_model

    serve_model(args.workspace, args.model, args.host, args.port, out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="conveyor", description="Run and inspect ML pipelines."
    )
    parser.add_argument(
        "--version", action="version", version=f"conveyor {__version__}"
    )
    parser.add_argument(
        "--workspace",
        default=os.environ.get("CONVEYOR_HOME", ".conveyor"),
        help="where artifacts, lineage and the registry live (default: .conveyor)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a pipeline file")
    run.add_argument("pipeline", help="path to a .py file that defines a Pipeline")
    run.add_argument("--param", "-p", action="append", default=[], metavar="KEY=VALUE")
    run.add_argument("--no-cache", action="store_true", help="recompute every step")
    run.add_argument("--workers", type=int, default=None)
    run.add_argument("--quiet", "-q", action="store_true")
    run.set_defaults(fn=cmd_run)

    runs = sub.add_parser("runs", help="list recent runs")
    runs.add_argument("--limit", "-n", type=int, default=20)
    runs.add_argument("--json", action="store_true")
    runs.set_defaults(fn=cmd_runs)

    show = sub.add_parser("show", help="show one run (id, prefix or `latest`)")
    show.add_argument("run")
    fmt = show.add_mutually_exclusive_group()
    fmt.add_argument(
        "--json", action="store_true", help="run, steps and metrics as JSON"
    )
    fmt.add_argument("--events", action="store_true", help="the raw event stream")
    show.set_defaults(fn=cmd_show)

    models = sub.add_parser("models", help="list registered model versions")
    models.add_argument("name", nargs="?")
    models.set_defaults(fn=cmd_models)

    ui = sub.add_parser("ui", help="open the run viewer")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=5300)
    ui.set_defaults(fn=cmd_ui)

    serve = sub.add_parser("serve", help="serve a registered model over HTTP")
    serve.add_argument("model", help="churn, churn:champion or churn:3")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(fn=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = Out()
    try:
        return args.fn(args, out)
    except (PipelineError, LookupError, FileNotFoundError) as exc:
        print(f"conveyor: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
