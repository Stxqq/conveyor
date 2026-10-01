// The frontend's pure parts, checked against the recorded demo runs.
//   node --test "tests/ui/*.test.mjs"
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { layoutGraph, NODE } from "../../conveyor/ui/static/js/layout.js";
import { Player } from "../../conveyor/ui/static/js/player.js";
import { foldRun } from "../../conveyor/ui/static/js/run.js";

const recorded = (name) =>
  JSON.parse(readFileSync(new URL(`../../docs/runs/${name}.json`, import.meta.url)));
const churn = recorded("cold")[0].graph;

test("layout keeps the longest chain on one row, left to right", () => {
  const { nodes } = layoutGraph(churn);
  const chain = ["customers", "validate", "split", "encoder", "features", "train",
    "evaluate", "gate", "register", "score", "monitor"];
  chain.forEach((name, i) => {
    assert.equal(nodes.get(name).row, 0, name);
    assert.equal(nodes.get(name).layer, i, name);
  });
});

test("sources sit next to their first consumer, side branches don't collide", () => {
  const { nodes } = layoutGraph(churn);
  assert.equal(nodes.get("new_month").layer, nodes.get("score").layer - 1);
  assert.equal(nodes.get("new_month").row, -1);
  assert.equal(nodes.get("baseline").row, 1);
  const slots = [...nodes.values()].map((n) => `${n.x},${n.y}`);
  assert.equal(new Set(slots).size, slots.length);
});

test("skip edges stacked in one channel never share a lane", () => {
  const { edges } = layoutGraph(churn);
  const lanes = edges.filter((e) => e.route === "lane");
  assert.ok(lanes.length > 0);
  for (const a of lanes) {
    for (const b of lanes) {
      if (a === b || a.channel !== b.channel || a.lane !== b.lane) continue;
      assert.ok(a.b <= b.a || b.b <= a.a, `${a.from}->${a.to} overlaps ${b.from}->${b.to}`);
    }
  }
  for (const e of edges) assert.match(e.d, /^M[\d.]+ [\d.]+/);
});

test("layout size covers every node", () => {
  const { nodes, width, height } = layoutGraph(churn);
  for (const n of nodes.values()) {
    assert.ok(n.x + NODE.w <= width && n.y + NODE.h <= height, n.name);
  }
});

test("folding the failed run marks the failure and everything downstream", () => {
  const run = foldRun(recorded("failed"));
  assert.equal(run.status, "failed");
  assert.equal(run.steps.get("validate").status, "failed");
  assert.match(run.steps.get("validate").traceback, /3 checks failed/);
  assert.equal(run.steps.get("new_month").status, "cached");
  assert.equal(run.steps.get("monitor").status, "skipped");
  assert.equal(run.steps.get("monitor").because, "validate");
});

test("a prefix of the stream is a valid earlier state", () => {
  const events = recorded("retry");
  const retry = events.findIndex((e) => e.type === "step_retry");
  const run = foldRun(events, retry + 1);
  assert.equal(run.status, "running");
  const step = run.steps.get("new_month");
  assert.equal(step.status, "retrying");
  assert.equal(step.attempts[0].outcome, "retry");
  assert.equal(run.steps.get("score").status, "pending");
});

test("replay times only move forward and the wall clock tracks the run", () => {
  const events = recorded("cold");
  const player = new Player(() => {});
  player.load(events);
  assert.equal(player.index, 1, "run_started applies at once");
  player.at.slice(1).forEach((t, i) => {
    assert.ok(t >= player.at[i]);
    // every step event gets a beat of its own, even when it shares a timestamp
    if (events[i + 1].type.startsWith("step_")) assert.ok(t > player.at[i]);
  });
  player.seek(1);
  assert.equal(player.index, events.length);
  assert.ok(Math.abs(player.wall - (events.at(-1).ts - events[0].ts)) < 1e-9);
  player.seek(0.5);
  assert.ok(player.wall > 0 && player.wall < events.at(-1).ts - events[0].ts);
});
