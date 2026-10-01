"""Run the churn pipeline a few times in a fresh workspace and save the event
streams to docs/runs/, where the static demo replays them.

    python scripts/record_demo.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from conveyor import Executor
from conveyor.events import read_events
from conveyor.loader import load_pipeline
from conveyor.registry import ModelRegistry

ROOT = Path(__file__).resolve().parent.parent

SCENARIOS = [
    ("cold", "First run on an empty workspace", {}),
    ("cached", "Same code, same params: every step is a cache hit", {}),
    (
        "param-change",
        "Stronger regularisation; the gate keeps the champion",
        {"l2": 1000.0},
    ),
    ("retry", "The warehouse read times out twice before it works", {"flaky_reads": 2}),
    ("failed", "A bad export: validation stops the run", {"corrupt": 0.03}),
]


def main() -> None:
    pipeline = load_pipeline(ROOT / "examples" / "churn" / "pipeline.py")
    out = ROOT / "docs" / "runs"
    out.mkdir(parents=True, exist_ok=True)
    index = []
    with tempfile.TemporaryDirectory() as tmp:
        ex = Executor(Path(tmp) / ".conveyor")
        for slug, title, params in SCENARIOS:
            result = ex.run(pipeline, params, source="examples/churn/pipeline.py")
            events = read_events(result.events_path)
            (out / f"{slug}.json").write_text(json.dumps(events, indent=1) + "\n")
            index.append(
                {
                    "file": f"{slug}.json",
                    "title": title,
                    "params": params,
                    "run_id": result.id,
                    "status": result.status,
                    "duration": round(result.duration, 4),
                    "counts": {
                        s: result.count(s)
                        for s in ("succeeded", "cached", "failed", "skipped")
                    },
                }
            )
            print(f"{slug:13} {result.status:10} {result.duration * 1000:7.0f} ms")
        registry = ModelRegistry(ex.workspace)
        models = [
            {
                "name": name,
                "champion": registry.champion(name).version,
                # import_root is a path on this machine; it means nothing on a website
                "versions": [
                    {k: v for k, v in version.card.items() if k != "import_root"}
                    for version in registry.versions(name)
                ],
            }
            for name in registry.names()
        ]
        ex.lineage.close()
    (out / "index.json").write_text(json.dumps(index, indent=1) + "\n")
    (out / "models.json").write_text(json.dumps(models, indent=1, default=str) + "\n")


if __name__ == "__main__":
    main()
