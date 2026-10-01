import { el } from "./dom.js";
import { duration, escape } from "./format.js";
import { GraphView } from "./graph.js";
import { ModelView } from "./model.js";
import { animate } from "./motion.js";
import { Player, STRETCH } from "./player.js";
import { foldRun } from "./run.js";
import { RunsView } from "./runs.js";
import { Sheet } from "./sheet.js";
import { openSource } from "./source.js";
import { TimelineView } from "./timeline.js";

const VIEWS = ["runs", "graph", "timeline", "model"];
const SPEEDS = [
  [0.5, "½×"],
  [1, "1×"],
  [2, "2×"],
];
// The recorded lede is already in index.html, so the page doesn't reflow on load.
const LIVE_LEDE = "Runs in this workspace. Start one with <code>conveyor run</code> and it shows up here while it happens.";
const PILL = { width: 84, gap: 6, narrow: 74 };
const narrow = matchMedia("(max-width: 720px)");

const source = openSource();
const query = new URLSearchParams(location.search);
const $ = (selector, root = document) => root.querySelector(selector);
const section = (view) => $(`.view[data-view="${view}"]`);

const state = { runs: [], key: null, view: null, run: foldRun([]), folded: -1, closeStream: null, ticking: false };

const sheet = new Sheet($("#sheet"));
const views = {
  runs: new RunsView(section("runs"), { onSelect: (key) => select(key, { autoplay: true, view: "graph" }) }),
  graph: new GraphView(section("graph"), {
    onOpen: (name) => sheet.open(state.run.steps.get(name), state.run),
  }),
  timeline: new TimelineView(section("timeline")),
  model: new ModelView(section("model"), { source }),
};

const player = new Player(render);
const transport = bindTransport();

if (source.live) $("#lede").innerHTML = LIVE_LEDE;
document.body.classList.add(source.live ? "is-live-source" : "is-recorded-source");

function render() {
  if (state.folded !== player.index || player.live) {
    state.folded = player.index;
    state.run = foldRun(player.events, player.index);
  }
  const { view, run } = state;
  if (view === "graph") views.graph.update(run);
  if (view === "timeline") views.timeline.update(run, player);
  if (view === "model") views.model.update(run);
  if (view === "runs") views.runs.update(state.runs, state.key);
  transport.update();
  caption();
}

function caption() {
  const summary = state.runs.find((r) => r.key === state.key);
  if (!summary) return;
  const run = state.run;
  const counts = run.counts ?? summary.counts;
  const bits = [];
  if (run.status === "running" && player.live) bits.push("running now");
  else if (counts) {
    bits.push(`${counts.succeeded} ran`, `${counts.cached} cached`);
    if (counts.failed) bits.push(`${counts.failed} failed`);
    if (counts.skipped) bits.push(`${counts.skipped} skipped`);
  }
  const took = run.duration ?? summary.duration;
  if (took != null && !player.live) bits.push(duration(took));
  const box = $("#caption");
  box.querySelector("h2").textContent = summary.title;
  box.querySelector("p").textContent = bits.join(" · ");
}

async function select(key, { autoplay = false, view = null } = {}) {
  state.closeStream?.();
  state.closeStream = null;
  state.key = key;
  if (view) location.hash = view;
  const summary = state.runs.find((r) => r.key === key);
  if (source.live && summary?.status === "running") {
    player.load([], { live: true });
    state.closeStream = source.stream(
      key,
      (event) => player.append(event),
      () => state.key === key && finishLive(),
    );
    tickWhileLive();
    return;
  }
  const events = await source.events(key);
  if (state.key !== key) return;
  player.load(events);
  if (autoplay) player.play();
  else player.seek(1);
}

// The timeline's playhead follows the wall clock while a run is streaming,
// even when no event has arrived for a while.
function tickWhileLive() {
  if (state.ticking) return;
  state.ticking = true;
  animate(() => {
    if (!player.live) {
      state.ticking = false;
      return false;
    }
    if (state.view === "timeline") render();
  });
}

async function finishLive() {
  state.closeStream = null;
  await refreshRuns();
  views.model.setModels(await source.models());
  render();
}

async function refreshRuns() {
  const known = new Set(state.runs.map((r) => r.key));
  state.runs = await source.runs();
  const newest = state.runs[0];
  if (source.live && state.runs.length && newest.status === "running" && !known.has(newest.key) && known.size) {
    select(newest.key);
  }
  render();
}

function show(view) {
  if (!VIEWS.includes(view)) view = "graph";
  if (view === state.view) return;
  const previous = state.view && section(state.view);
  state.view = view;
  for (const link of document.querySelectorAll(".pill-btn")) {
    const active = link.dataset.view === view;
    link.classList.toggle("active", active);
    link.toggleAttribute("aria-current", active);
  }
  placePill();
  const stage = $("#stage");
  const next = section(view);
  const enter = () => {
    // the caption's width follows the view, so it only changes once the old
    // view is gone
    stage.dataset.view = view;
    next.hidden = false;
    next.classList.add("entering");
    render();
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        next.classList.remove("entering");
        stage.classList.remove("is-switching");
      }),
    );
  };
  if (!previous) return enter();
  stage.classList.add("is-switching");
  previous.classList.add("leaving");
  setTimeout(() => {
    previous.hidden = true;
    previous.classList.remove("leaving");
    if (state.view === view) enter();
  }, 280);
}

function placePill() {
  const step = (narrow.matches ? PILL.narrow : PILL.width) + PILL.gap;
  $(".pill-ind").style.transform = `translateX(${VIEWS.indexOf(state.view) * step}px)`;
}
narrow.addEventListener("change", placePill);

function bindTransport() {
  const root = $("#transport");
  const play = $(".t-play", root);
  const scrub = $(".t-scrub", root);
  const fill = $(".t-fill", root);
  const rail = $(".t-rail", root);
  const time = $(".t-time", root);
  const picker = $(".t-picker", root);
  const menu = $(".t-menu", root);
  const runButton = $(".t-run", root);
  const speed = $(".t-speed", root);
  const live = $(".t-live", root);

  for (const [value, label] of SPEEDS) {
    const button = el("button", "t-chip", label);
    button.type = "button";
    button.title = `${label} plays the run ${STRETCH / value}× slower than it ran`;
    button.addEventListener("click", () => {
      player.speed = value;
      render();
    });
    button.dataset.speed = value;
    speed.append(button);
  }

  play.addEventListener("click", () => player.toggle());
  document.addEventListener("keydown", (e) => {
    if (e.key !== " " || e.target.closest("button, a, input, [role=button], [role=slider]") || sheet.isOpen) return;
    e.preventDefault();
    player.toggle();
  });

  let scrubbing = false;
  const seekTo = (e) => {
    const box = scrub.getBoundingClientRect();
    player.seek((e.clientX - box.left) / box.width);
  };
  scrub.addEventListener("pointerdown", (e) => {
    scrubbing = true;
    scrub.setPointerCapture(e.pointerId);
    player.pause();
    seekTo(e);
  });
  scrub.addEventListener("pointermove", (e) => scrubbing && seekTo(e));
  scrub.addEventListener("pointerup", () => (scrubbing = false));
  scrub.addEventListener("keydown", (e) => {
    const step = { ArrowLeft: -0.05, ArrowRight: 0.05 }[e.key];
    if (step == null) return;
    e.preventDefault();
    player.pause();
    player.seek(player.progress + step);
  });

  const setMenu = (open) => {
    picker.classList.toggle("open", open);
    runButton.setAttribute("aria-expanded", open);
  };
  runButton.addEventListener("click", () => setMenu(!picker.classList.contains("open")));
  menu.addEventListener("click", (e) => {
    const option = e.target.closest("[data-key]");
    if (!option) return;
    setMenu(false);
    select(option.dataset.key, { autoplay: true });
  });
  document.addEventListener("click", (e) => !picker.contains(e.target) && setMenu(false));
  document.addEventListener("keydown", (e) => e.key === "Escape" && setMenu(false));

  let menuHtml = "";
  return {
    update() {
      const summary = state.runs.find((r) => r.key === state.key);
      root.classList.toggle("is-playing", player.playing);
      root.classList.toggle("is-ended", player.ended && !player.playing);
      root.classList.toggle("is-live", player.live);
      root.classList.toggle("is-empty", !player.events.length && !player.live);
      live.hidden = !player.live;
      play.setAttribute("aria-label", player.playing ? "Pause" : player.ended ? "Replay" : "Play");
      const k = player.progress;
      fill.style.transform = `scaleX(${k})`;
      rail.style.transform = `translate3d(${k * 100}%,0,0)`;
      scrub.setAttribute("aria-valuenow", Math.round(k * 100));
      const total = player.events.length ? player.events.at(-1).ts - player.events[0].ts : 0;
      time.textContent = player.live ? duration(player.wall) : `${duration(player.wall) || "0 ms"} / ${duration(total)}`;
      for (const chip of speed.children) chip.classList.toggle("active", Number(chip.dataset.speed) === player.speed);
      if (summary) {
        $(".t-run-name", root).textContent = summary.title;
        $(".t-run-dot", root).className = `t-run-dot status-dot is-${summary.status}`;
      }
      const html = state.runs
        .map(
          (r) => `<button type="button" role="option" class="t-option" data-key="${escape(r.key)}" aria-selected="${r.key === state.key}">
            <i class="status-dot is-${r.status}"></i><span>${escape(r.title)}</span></button>`,
        )
        .join("");
      if (html !== menuHtml) menu.innerHTML = menuHtml = html;
    },
  };
}

async function boot() {
  window.addEventListener("hashchange", () => show(location.hash.slice(1)));
  show(location.hash.slice(1) || "graph");
  try {
    const [runs, models] = await Promise.all([source.runs(), source.models()]);
    state.runs = runs;
    views.model.setModels(models);
  } catch (error) {
    $("#caption h2").textContent = "Couldn't load runs";
    $("#caption p").textContent = String(error.message);
    return;
  }
  if (!state.runs.length) return render();
  const wanted = state.runs.find((r) => r.key === query.get("run")) ?? state.runs[0];
  const atEnd = query.get("at") === "end" || source.live;
  // give the intro a moment to settle before the first replay starts
  setTimeout(() => select(wanted.key, { autoplay: !atEnd }), atEnd ? 0 : 650);
  if (source.live) setInterval(refreshRuns, 1500);
}

boot();
