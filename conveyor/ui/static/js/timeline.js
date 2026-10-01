import { el } from "./dom.js";
import { duration, escape } from "./format.js";

// Gantt chart of one run on its own wall clock: one row per step, a bar per
// attempt, dashed gaps for retry backoff, ticks for cache hits.
export class TimelineView {
  #rows = new Map();
  #signature = "";
  #plotWidth = 0;
  #last = null;

  constructor(root) {
    this.tile = el("div", "tile timeline-tile");
    this.card = el("div", "card timeline-card");
    this.axis = el("div", "tl-axis");
    this.body = el("div", "tl-rows");
    this.head = el("div", "tl-head", "<i></i><span></span>");
    this.legend = el(
      "div",
      "tl-legend",
      `<span><i class="tl-key bar-ok"></i>ran</span>
       <span><i class="tl-key bar-retry"></i>failed attempt</span>
       <span><i class="tl-key tl-wait-key"></i>backoff</span>
       <span><i class="tl-key bar-cached"></i>cache hit</span>
       <span><i class="tl-key bar-failed"></i>failed</span>`,
    );
    this.plot = el("div", "tl-plot");
    this.plot.append(this.axis, this.head);
    this.card.append(this.plot, this.body, this.legend);
    this.tile.append(this.card);
    root.append(this.tile);
    // built while hidden, the plot has no width until the view is shown
    new ResizeObserver(() => {
      this.#plotWidth = this.axis.clientWidth;
      if (this.#last) this.update(...this.#last);
    }).observe(this.axis);
  }

  update(run, player) {
    this.#last = [run, player];
    if (!run.graph.length || !player.events.length) return;
    const signature = run.graph.map((s) => s.name).join("|");
    if (signature !== this.#signature) this.#build(run.graph, signature);

    const t0 = player.events[0].ts;
    const wall = player.wall;
    const total = player.live ? Math.max(wall, 0.05) * 1.15 : player.events.at(-1).ts - t0;
    const span = niceSpan(total);
    const count = Math.max(2, Math.min(6, Math.floor(this.#plotWidth / 90)));
    if (span !== this.span || count !== this.count) {
      this.span = span;
      this.count = count;
      this.axis.innerHTML = ticks(span, count)
        .map((t, _, all) => `<span style="left:${(t / span) * 100}%">${tickLabel(t, all[1])}</span>`)
        .join("");
    }
    const at = (ts) => ((ts - t0) / span) * 100;
    const now = t0 + wall;

    for (const [name, step] of run.steps) {
      const row = this.#rows.get(name);
      const html = bars(step, at, now, this.#plotWidth);
      if (row.dataset.html !== html) {
        row.dataset.html = html;
        row.innerHTML = html;
      }
      row.parentElement.className = `tl-row is-${step.status}`;
    }

    const x = (wall / span) * this.#plotWidth;
    this.head.style.transform = `translate3d(${x}px,0,0)`;
    // the playhead's label would sit on top of the nearest tick labels
    for (const tick of this.axis.children) {
      const tx = (parseFloat(tick.style.left) / 100) * this.#plotWidth;
      tick.classList.toggle("is-near", !this.head.classList.contains("is-idle") && Math.abs(tx - x) < 46);
    }
    this.head.lastChild.textContent = duration(wall) || "0";
    this.head.classList.toggle("is-idle", player.index === 0 && !player.live);
  }

  #build(graph, signature) {
    this.#signature = signature;
    this.#rows.clear();
    this.card.style.setProperty("--rows", graph.length);
    this.body.replaceChildren(
      ...graph.map((spec, i) => {
        const row = el("div", "tl-row", `<span class="tl-name">${escape(spec.name)}</span>`);
        row.style.setProperty("--i", i);
        const track = el("div", "tl-track");
        row.append(track);
        this.#rows.set(spec.name, track);
        return row;
      }),
    );
  }
}

// Notes sit right of where a step ended. Near the end of the plot that would
// run off the card, so they move left of the point instead, or above the bar
// when there is one to cover.
function note(left, text, width, { kind = "", bar = false } = {}) {
  const room = ((100 - left) / 100) * width;
  const cramped = room < text.length * 6.2 + 16;
  const place = cramped ? (bar ? " is-above" : " is-flip") : "";
  return `<span class="tl-note${kind}${place}" style="left:${left}%">${text}</span>`;
}

function bars(step, at, now, width) {
  if (step.status === "cached") {
    const left = at(step.finishedAt);
    return `<i class="tl-bar bar-cached" style="left:${left}%"></i>${note(left, "cached", width, { kind: " is-tick" })}`;
  }
  if (step.status === "skipped") return note(at(step.finishedAt), "skipped", width);
  const parts = [];
  step.attempts.forEach((attempt, i) => {
    const end = attempt.end ?? now;
    parts.push(
      `<i class="tl-bar bar-${attempt.outcome}" style="left:${at(attempt.start)}%;width:${Math.max(0, at(end) - at(attempt.start))}%"></i>`,
    );
    const next = step.attempts[i + 1];
    if (attempt.outcome === "retry") {
      const until = next ? next.start : now;
      parts.push(`<i class="tl-wait" style="left:${at(end)}%;width:${Math.max(0, at(until) - at(end))}%"></i>`);
    }
  });
  if (step.status === "succeeded" || step.status === "failed") {
    parts.push(note(at(step.finishedAt), duration(step.duration), width, { bar: true }));
  }
  return parts.join("");
}

function niceSpan(seconds) {
  const target = Math.max(seconds, 0.002);
  const base = 10 ** Math.floor(Math.log10(target));
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) {
    if (m * base >= target) return m * base;
  }
  return 10 * base;
}

function tickLabel(t, step) {
  if (!t) return "0";
  if (t >= 1) return `${t.toFixed(step < 1 ? 1 : 0)} s`;
  const ms = t * 1000;
  return `${step * 1000 < 1 ? ms.toFixed(1) : Math.round(ms)} ms`;
}

function ticks(span, count) {
  const raw = span / count;
  const base = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * base).find((s) => s >= raw);
  const out = [];
  for (let t = 0; t <= span + step * 1e-6; t += step) out.push(Number(t.toPrecision(6)));
  return out;
}
