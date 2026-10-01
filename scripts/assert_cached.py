"""Read `conveyor show <run> --json` on stdin; exit non-zero unless every
step of that run was served from the cache."""

import json
import sys

run = json.load(sys.stdin)
missed = [s["step"] for s in run["steps"] if s["status"] != "cached"]
if run["status"] != "succeeded" or missed:
    sys.exit(
        f"run {run['id']} was not fully cached: {', '.join(missed) or run['status']}"
    )
print(f"run {run['id']}: all {len(run['steps'])} steps were cache hits")
