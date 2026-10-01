import json
import os
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from conveyor.cli import coerce_param, main
from conveyor.events import follow
from conveyor.ui.server import make_server

CHURN = str(Path(__file__).resolve().parent.parent / "examples/churn/pipeline.py")

FLAKY = """
from conveyor import Pipeline, step

@step
def source(n=3):
    return list(range(n))

@step
def boom(source, explode=False):
    if explode:
        raise ValueError("exploded on purpose")
    return sum(source)

pipeline = Pipeline("tiny", [source, boom])
"""


@pytest.fixture
def ws(tmp_path):
    return str(tmp_path / "ws")


def test_coerce_param_uses_the_default_type():
    assert coerce_param("3", 1) == 3
    assert coerce_param("3", 1.0) == 3.0
    assert coerce_param("no", True) is False
    assert coerce_param("x", "y") == "x"
    assert coerce_param("[1, 2]", None) == [1, 2]
    assert coerce_param("plain", None) == "plain"
    with pytest.raises(ValueError):
        coerce_param("maybe", False)


def test_run_then_rerun_from_cache(ws, capsys):
    assert main(["--workspace", ws, "run", CHURN]) == 0
    out = capsys.readouterr().out
    assert "succeeded" in out and "roc_auc" in out and "13 ran" in out

    assert main(["--workspace", ws, "run", CHURN, "--quiet"]) == 0
    capsys.readouterr()
    assert main(["--workspace", ws, "show", "latest", "--json"]) == 0
    run = json.loads(capsys.readouterr().out)
    statuses = {s["step"]: s["status"] for s in run["steps"]}
    assert statuses.pop("gate") == statuses.pop("register") == "succeeded"
    assert set(statuses.values()) == {"cached"}
    assert [n["name"] for n in run["graph"] if not n["cache"]] == ["gate", "register"]

    assert main(["--workspace", ws, "runs", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2
    assert main(["--workspace", ws, "models"]) == 0
    assert "champion" in capsys.readouterr().out


def test_failure_exit_code_and_traceback(ws, tmp_path, capsys):
    file = tmp_path / "tiny.py"
    file.write_text(FLAKY)
    assert main(["--workspace", ws, "run", str(file), "-p", "explode=true"]) == 1
    out = capsys.readouterr().out
    assert "boom failed after 1 attempt(s)" in out
    assert "exploded on purpose" in out
    assert main(["--workspace", ws, "run", str(file), "-p", "nope=1"]) == 2
    assert "unknown parameter 'nope'" in capsys.readouterr().err
    assert main(["--workspace", ws, "show", "does-not-exist"]) == 1


@pytest.fixture
def api(ws):
    main(["--workspace", ws, "run", CHURN, "-q"])
    server = make_server(ws, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield lambda path: urllib.request.urlopen(base + path)
    server.shutdown()
    server.server_close()


def test_ui_api(api):
    runs = json.load(api("/api/runs"))
    assert runs[0]["status"] == "succeeded"
    run_id = runs[0]["id"]

    run = json.load(api("/api/runs/latest"))
    assert run["id"] == run_id
    assert [s["step"] for s in run["steps"]][:2] == ["new_month", "customers"]
    assert run["graph"][0]["name"] == "customers"

    events = json.load(api(f"/api/runs/{run_id}/events"))
    assert events[0]["type"] == "run_started" and events[-1]["type"] == "run_finished"

    model = next(s for s in run["steps"] if s["step"] == "train")
    lineage = json.load(api(f"/api/artifacts/{model['output']}"))
    assert lineage["producer"]["step"] == "train"
    assert {u["step"] for u in lineage["upstream"]} >= {"features", "encoder", "split"}

    gate = next(s for s in run["steps"] if s["step"] == "gate")
    decision = json.load(api(f"/api/artifacts/{gate['output']}/value"))
    assert decision["promote"] is True and decision["reason"] == "no champion yet"
    with pytest.raises(urllib.error.HTTPError) as refused:
        api(f"/api/artifacts/{model['output']}/value")
    assert refused.value.code == 415

    models = json.load(api("/api/models"))
    assert models[0]["name"] == "churn" and models[0]["champion"] == 1

    page = api("/").read()
    assert b"<title>conveyor</title>" in page
    assert b'name="conveyor-source" content="live"' in page
    assert b"export class" in api("/js/graph.js").read()
    with pytest.raises(urllib.error.HTTPError):
        api("/../pyproject.toml")
    with pytest.raises(urllib.error.HTTPError):
        api("/api/runs/nope")


def test_stream_replays_a_finished_run_and_closes(api):
    response = api("/api/runs/latest/stream")
    assert response.headers["Content-Type"] == "text/event-stream"
    frames = [f for f in response.read().decode().split("\n\n") if f.strip()]
    payloads = [json.loads(f.split("data: ", 1)[1]) for f in frames]
    assert payloads[-1]["type"] == "run_finished"
    assert frames[0].startswith("id: 1\n")


def test_api_answers_bad_requests_instead_of_hanging_up(api, ws):
    with pytest.raises(urllib.error.HTTPError) as bad:
        api("/api/runs?limit=abc")
    assert bad.value.code == 400
    assert len(json.load(api("/api/runs?limit=0"))) == 1

    run_id = json.load(api("/api/runs"))[0]["id"]
    (Path(ws) / "runs" / f"{run_id}.jsonl").unlink()
    for tail in ("events", "stream"):
        with pytest.raises(urllib.error.HTTPError) as missing:
            api(f"/api/runs/{run_id}/{tail}")
        assert missing.value.code == 404


def test_follow_gives_up_on_a_run_whose_process_died(tmp_path):
    path = tmp_path / "run.jsonl"
    path.write_text('{"seq": 1, "type": "run_started"}\n')
    stream = follow(path, poll=0.01, heartbeat=0.05, still_running=lambda: False)
    assert [e["type"] for e in stream] == ["run_started"]

    beats = follow(path, poll=0.01, heartbeat=0.05)
    assert next(beats)["type"] == "run_started"
    assert next(beats) is None


def test_stream_ends_when_the_run_process_is_gone(api, ws, monkeypatch):
    from conveyor.ui import server

    run_id = json.load(api("/api/runs"))[0]["id"]
    db = sqlite3.connect(Path(ws) / "conveyor.db")
    db.execute("UPDATE runs SET status = 'running' WHERE id = ?", (run_id,))
    db.commit()
    events = Path(ws) / "runs" / f"{run_id}.jsonl"
    first = json.loads(events.read_text().splitlines()[0])
    assert first["pid"] == os.getpid()
    events.write_text(json.dumps({**first, "pid": 2**22 + 12345}) + "\n")
    real_follow = server.follow
    monkeypatch.setattr(
        server, "follow", lambda path, **kw: real_follow(path, heartbeat=0.05, **kw)
    )
    frames = api(f"/api/runs/{run_id}/stream").read().decode().split("\n\n")
    assert frames[0].startswith("id: 1\n")
    assert ": keepalive" not in frames
