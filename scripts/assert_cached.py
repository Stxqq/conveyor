"""Read `conveyor show <run> --json` on stdin; exit non-zero unless every
step of that run was served from the cache, apart from steps marked
``cache=False``, which run every time by design."""

import json
import sys

run = json.load(sys.stdin)
always = {n["name"] for n in run["graph"] if not n.get("cache", True)}
missed = [
    s["step"]
    for s in run["steps"]
    if s["status"] != "cached" and s["step"] not in always
]
if run["status"] != "succeeded" or missed:
    sys.exit(
        f"run {run['id']} was not fully cached: {', '.join(missed) or run['status']}"
    )
hits = len(run["steps"]) - len(always)
print(f"run {run['id']}: {hits} cache hits, {', '.join(sorted(always))} always run")
