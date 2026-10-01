// Folds a run's event stream into per-step state. Replays call this with a
// prefix of the stream, so nothing here may look ahead.

export const DONE = new Set(["succeeded", "cached"]);
export const SETTLED = new Set(["succeeded", "cached", "failed", "skipped"]);

export function foldRun(events, upto = events.length) {
  const run = {
    id: null,
    pipeline: "",
    params: {},
    graph: [],
    status: "pending",
    startedAt: events[0]?.ts ?? null,
    finishedAt: null,
    duration: null,
    counts: null,
    steps: new Map(),
  };
  for (let i = 0; i < upto; i++) apply(run, events[i]);
  return run;
}

function apply(run, event) {
  if (event.type === "run_started") {
    Object.assign(run, {
      id: event.run_id,
      pipeline: event.pipeline,
      params: event.params,
      graph: event.graph,
      status: "running",
      startedAt: event.ts,
    });
    for (const spec of event.graph) {
      run.steps.set(spec.name, {
        name: spec.name,
        spec,
        status: "pending",
        attempts: [],
        metrics: {},
        logs: [],
      });
    }
    return;
  }
  if (event.type === "run_finished") {
    Object.assign(run, {
      status: event.status,
      duration: event.duration,
      counts: event.counts,
      finishedAt: event.ts,
    });
    return;
  }
  const step = run.steps.get(event.step);
  if (!step) return;
  const last = step.attempts.at(-1);
  switch (event.type) {
    case "step_started":
      step.status = "running";
      step.startedAt ??= event.ts;
      step.attempts.push({ start: event.ts, end: null, outcome: "running" });
      break;
    case "step_retry":
      step.status = "retrying";
      Object.assign(last, { end: event.ts, outcome: "retry", error: event.error, delay: event.delay });
      break;
    case "step_succeeded":
    case "step_cached":
      Object.assign(step, {
        status: event.type === "step_cached" ? "cached" : "succeeded",
        finishedAt: event.ts,
        duration: event.duration ?? 0,
        cacheKey: event.cache_key,
        artifact: event.artifact,
        kind: event.kind,
        size: event.size,
        sourceRun: event.source_run,
      });
      step.startedAt ??= event.ts;
      if (event.metrics) Object.assign(step.metrics, event.metrics);
      if (last) Object.assign(last, { end: event.ts, outcome: "ok" });
      break;
    case "step_failed":
      Object.assign(step, {
        status: "failed",
        finishedAt: event.ts,
        duration: event.duration,
        error: event.error,
      });
      if (last) Object.assign(last, { end: event.ts, outcome: "failed" });
      break;
    case "step_skipped":
      Object.assign(step, { status: "skipped", finishedAt: event.ts, because: event.because });
      break;
    case "step_traceback":
      step.traceback = event.traceback;
      break;
    case "metric":
      step.metrics[event.name] = event.value;
      break;
    case "log":
      step.logs.push(event.message);
      break;
  }
}

export function cacheRatio(counts) {
  if (!counts) return null;
  const total = counts.succeeded + counts.cached + counts.failed + counts.skipped;
  return total ? { cached: counts.cached, total } : null;
}
