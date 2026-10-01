import { el, svg } from "./dom.js";
import { bytes, duration, escape, short } from "./format.js";
import { layoutGraph, NODE } from "./layout.js";
import { animate, FOLLOW, FRICTION, still } from "./motion.js";
import { DONE, SETTLED } from "./run.js";

const MIN_SCALE = 0.8;
const PACKET_MS = 560;

const META = {
  pending: () => "–",
  running: (s) => (s.attempts.length > 1 ? `attempt ${s.attempts.length}` : "running"),
  retrying: () => '<span class="chip chip-amber">retry</span>',
  succeeded: (s) => duration(s.duration),
  cached: () => '<span class="chip chip-sky">cached</span>',
  failed: () => '<span class="chip chip-rose">failed</span>',
  skipped: () => '<span class="chip chip-gray">skipped</span>',
};

export class GraphView {
  #layout = null;
  #signature = "";
  #nodes = new Map();
  #edges = [];
  #status = new Map();
  #focus = null;
  #run = null;
  #scale = 1;
  #pan = 0;
  #panTarget = 0;
  #panning = false;
  #userUntil = 0;
  #gliding = false;
  #dragged = false;

  constructor(root, { onOpen }) {
    this.onOpen = onOpen;
    this.tile = el("div", "tile graph-tile");
    this.viewport = el("div", "graph-viewport");
    this.canvas = el("div", "graph-canvas");
    this.edgeLayer = svg("svg", { class: "graph-edges", "aria-hidden": "true" });
    this.callouts = el("div", "callouts");
    this.canvas.append(this.edgeLayer, this.callouts);
    this.viewport.append(this.canvas);
    this.tile.append(this.viewport);
    root.append(this.tile);
    this.#bindDrag();
    new ResizeObserver(() => this.#fit()).observe(this.viewport);
  }

  update(run) {
    this.#run = run;
    if (!run.graph.length) return;
    const signature = run.graph.map((s) => `${s.name}<${s.inputs}`).join("|");
    if (signature !== this.#signature) this.#build(run.graph, signature);

    for (const [name, step] of run.steps) {
      const node = this.#nodes.get(name);
      const before = this.#status.get(name);
      const meta = META[step.status](step);
      if (node.meta.innerHTML !== meta) node.meta.innerHTML = meta;
      if (before === step.status) continue;
      this.#status.set(name, step.status);
      node.root.classList.replace(`is-${before ?? "pending"}`, `is-${step.status}`);
      node.root.setAttribute("aria-label", `${name}, ${step.status}`);
      if (before && SETTLED.has(step.status)) this.#settle(node);
      if (before === "pending" && (step.status === "running" || step.status === "cached")) {
        for (const edge of this.#edges) if (edge.to === name) this.#send(edge);
      }
    }
    for (const edge of this.#edges) {
      const from = run.steps.get(edge.from)?.status;
      edge.root.classList.toggle("is-carried", DONE.has(from));
    }
    if (run.steps.has(this.#focus)) this.#fillCallouts(this.#focus);
    this.#follow();
  }

  #build(graph, signature) {
    this.#signature = signature;
    this.#layout = layoutGraph(graph);
    this.#status.clear();
    for (const node of this.#nodes.values()) node.root.remove();
    this.#nodes.clear();
    this.edgeLayer.replaceChildren();
    const { width, height } = this.#layout;
    this.canvas.style.width = `${width}px`;
    this.canvas.style.height = `${height}px`;
    this.edgeLayer.setAttribute("viewBox", `0 0 ${width} ${height}`);
    this.edgeLayer.setAttribute("width", width);
    this.edgeLayer.setAttribute("height", height);

    this.#edges = this.#layout.edges.map((e) => {
      const g = svg("g", { class: e.route === "direct" ? "edge" : "edge is-lane" });
      g.append(svg("path", { d: e.d, class: "edge-base" }), svg("path", { d: e.d, class: "edge-ink" }));
      this.edgeLayer.append(g);
      return { ...e, root: g, path: g.lastChild };
    });

    for (const [name, box] of this.#layout.nodes) {
      const root = el("div", "node is-pending");
      root.tabIndex = 0;
      root.setAttribute("role", "button");
      root.style.left = `${box.x}px`;
      root.style.top = `${box.y}px`;
      root.innerHTML = `
        <div class="node-card">
          <span class="node-ring"></span>
          <span class="node-name"><i class="node-dot"></i>${escape(name)}</span>
          <span class="node-meta"></span>
          <span class="kl-grid"></span><span class="kl-box"></span>
        </div>`;
      root.addEventListener("pointerenter", (e) => e.pointerType === "mouse" && this.#setFocus(name));
      root.addEventListener("pointerleave", (e) => e.pointerType === "mouse" && this.#setFocus(null));
      root.addEventListener("focus", () => this.#setFocus(name));
      root.addEventListener("blur", () => this.#setFocus(null));
      root.addEventListener("click", () => !this.#dragged && this.onOpen(name));
      root.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          this.onOpen(name);
        }
      });
      this.canvas.insertBefore(root, this.callouts);
      this.#nodes.set(name, { root, box, card: root.firstElementChild, meta: root.querySelector(".node-meta") });
    }
    this.#pan = this.#panTarget = 0;
    this.#fit();
  }

  #settle(node) {
    if (still()) return;
    node.root.classList.remove("settle");
    void node.root.offsetWidth;
    node.root.classList.add("settle");
  }

  #send(edge) {
    if (still() || document.hidden) return;
    const dot = svg("circle", { r: 2.6, class: "packet" });
    this.edgeLayer.append(dot);
    const length = edge.path.getTotalLength();
    let t = 0;
    animate((dt) => {
      t += dt / PACKET_MS;
      const k = 1 - (1 - Math.min(1, t)) ** 3;
      const p = edge.path.getPointAtLength(k * length);
      dot.setAttribute("transform", `translate(${p.x} ${p.y})`);
      dot.style.opacity = t < 0.15 ? t / 0.15 : t > 0.85 ? (1 - t) / 0.15 : 1;
      if (t >= 1) {
        dot.remove();
        return false;
      }
    });
  }

  #setFocus(name) {
    if (name && !this.#run?.steps.has(name)) return;
    this.#focus = name;
    this.canvas.classList.toggle("focusing", !!name);
    for (const node of this.#nodes.values()) node.root.classList.remove("near", "focus");
    for (const edge of this.#edges) edge.root.classList.toggle("hot", !!name && (edge.from === name || edge.to === name));
    this.callouts.classList.remove("on");
    if (!name) return;
    const node = this.#nodes.get(name);
    node.root.classList.add("focus");
    for (const edge of this.#edges) {
      if (edge.from === name) this.#nodes.get(edge.to).root.classList.add("near");
      if (edge.to === name) this.#nodes.get(edge.from).root.classList.add("near");
    }
    this.#fillCallouts(name);
    const { x, y } = node.box;
    this.callouts.style.left = `${x}px`;
    this.callouts.style.top = `${y}px`;
    void this.callouts.offsetWidth;
    this.callouts.classList.add("on");
  }

  #fillCallouts(name) {
    const step = this.#run.steps.get(name);
    const html = callouts(step, this.#run.params)
      .map(
        ([edge, x, side, index, label, value], i) => `
        <div class="anno-item at-${edge} to-${side}" style="left:${x}px;--i:${i}">
          <i class="anno-line"></i><i class="anno-dot"></i>
          <span class="anno-label"><em>${index}</em><b>${label}</b><span>${value}</span></span>
        </div>`,
      )
      .join("");
    if (this.callouts.dataset.html !== html) {
      this.callouts.dataset.html = html;
      this.callouts.innerHTML = html;
    }
  }

  #fit() {
    if (!this.#layout) return;
    const available = this.viewport.clientWidth;
    const { width, height } = this.#layout;
    this.#scale = Math.max(MIN_SCALE, Math.min(1, available / width));
    this.#panning = width * this.#scale > available + 1;
    this.tile.classList.toggle("is-panning", this.#panning);
    this.viewport.style.height = `${height * this.#scale}px`;
    if (!this.#panning) this.#pan = this.#panTarget = (available - width * this.#scale) / 2;
    this.#panTarget = this.#clamp(this.#panTarget);
    this.#pan = this.#clamp(this.#pan);
    this.#place();
  }

  #clamp(x) {
    if (!this.#panning) return x;
    const min = this.viewport.clientWidth - this.#layout.width * this.#scale;
    return Math.min(0, Math.max(min, x));
  }

  #place() {
    this.canvas.style.transform = `translate3d(${this.#pan}px,0,0) scale(${this.#scale})`;
  }

  // On a narrow screen the camera drifts towards the newest work in the run,
  // unless someone has just dragged the graph themselves.
  #follow() {
    if (!this.#panning || performance.now() < this.#userUntil) return;
    let frontier = null;
    for (const [name, step] of this.#run.steps) {
      if (step.status === "pending" || step.status === "skipped") continue;
      const box = this.#nodes.get(name).box;
      if (step.status === "failed") {
        frontier = box;
        break;
      }
      if (!frontier || box.x > frontier.x) frontier = box;
    }
    const x = frontier ? frontier.x + NODE.w / 2 : 0;
    this.#panTarget = this.#clamp(this.viewport.clientWidth / 2 - x * this.#scale);
    if (this.#gliding) return;
    this.#gliding = true;
    animate(() => {
      this.#pan += (this.#panTarget - this.#pan) * (still() ? 1 : FOLLOW);
      this.#place();
      const done = Math.abs(this.#panTarget - this.#pan) < 0.5 || performance.now() < this.#userUntil;
      if (done) this.#gliding = false;
      return !done;
    });
  }

  #bindDrag() {
    let startX = 0;
    let startPan = 0;
    let velocity = 0;
    let lastX = 0;
    let lastT = 0;
    let down = false;
    this.viewport.addEventListener("pointerdown", (e) => {
      if (!this.#panning) return;
      down = true;
      this.#dragged = false;
      startX = lastX = e.clientX;
      startPan = this.#pan;
      lastT = performance.now();
      velocity = 0;
      this.#userUntil = Infinity;
    });
    window.addEventListener("pointermove", (e) => {
      if (!down) return;
      if (Math.abs(e.clientX - startX) > 4 && !this.#dragged) {
        this.#dragged = true;
        this.viewport.setPointerCapture(e.pointerId);
      }
      if (!this.#dragged) return;
      const now = performance.now();
      velocity = ((e.clientX - lastX) / Math.max(1, now - lastT)) * 16;
      lastX = e.clientX;
      lastT = now;
      this.#pan = this.#clamp(startPan + e.clientX - startX);
      this.#place();
    });
    const release = () => {
      if (!down) return;
      down = false;
      this.#userUntil = performance.now() + 5000;
      if (!this.#dragged) return;
      // let the click that ends a drag fall on the floor, not on a node
      setTimeout(() => (this.#dragged = false), 0);
      animate(() => {
        velocity *= FRICTION;
        this.#pan = this.#clamp(this.#pan + velocity);
        this.#place();
        return Math.abs(velocity) > 0.1 && !down;
      });
    };
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
  }
}

function callouts(step, runParams) {
  const { spec } = step;
  const inputs = spec.inputs.length ? spec.inputs.join(", ") : "none, it's a source";
  const params = spec.params.map((p) => `${p}=${runParams[p]}`).join(" ");
  const west = 22;
  const east = NODE.w - 22;
  return [
    ["top", west, "west", "01", "Inputs", escape(inputs) + (params ? `<br><code>${escape(params)}</code>` : "")],
    ["top", east, "east", "02", "Cache key", step.cacheKey ? `<code>${short(step.cacheKey)}</code>` : cacheHint(step)],
    ["bottom", west, "west", "03", "Duration", timing(step)],
    ["bottom", east, "east", "04", "Output", output(step)],
  ];
}

function cacheHint(step) {
  if (step.status === "failed") return "nothing stored";
  if (step.status === "skipped") return "never computed";
  return "once inputs exist";
}

function timing(step) {
  const tries = step.attempts.length;
  switch (step.status) {
    case "succeeded":
      return `${duration(step.duration)}${tries > 1 ? ` · ${tries} attempts` : ""}`;
    case "cached":
      return `cache hit · run <code>${escape(step.sourceRun?.slice(-4) ?? "")}</code>`;
    case "failed":
      return `${duration(step.duration)} · failed`;
    case "skipped":
      return `skipped, ${escape(step.because)} failed`;
    case "running":
    case "retrying":
      return tries > 1 ? `attempt ${tries}` : "running";
    default:
      return "waiting";
  }
}

function output(step) {
  if (step.artifact) return `${step.kind} · ${bytes(step.size)} <code>${short(step.artifact, 8)}</code>`;
  if (step.status === "failed") return escape(step.error.split("\n")[0].split(": ").at(-1));
  return "–";
}
