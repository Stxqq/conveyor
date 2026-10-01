import { bytes, duration, escape, number } from "./format.js";

// The step card that opens on click: everything the run knows about one step.
export class Sheet {
  constructor(root) {
    this.root = root;
    this.card = root.querySelector(".sheet-card");
    root.querySelector(".sheet-close").addEventListener("click", () => this.close());
    root.addEventListener("click", (e) => e.target === root && this.close());
    document.addEventListener("keydown", (e) => e.key === "Escape" && this.close());
  }

  get isOpen() {
    return document.body.classList.contains("sheet-open");
  }

  open(step, run) {
    this.returnTo = document.activeElement;
    this.card.innerHTML = render(step, run);
    this.root.hidden = false;
    this.root.scrollTop = 0;
    requestAnimationFrame(() => {
      document.body.classList.add("sheet-open");
      this.root.querySelector(".sheet-close").focus({ preventScroll: true });
    });
  }

  close() {
    if (!this.isOpen) return;
    document.body.classList.remove("sheet-open");
    setTimeout(() => {
      if (!this.isOpen) this.root.hidden = true;
    }, 450);
    this.returnTo?.focus?.({ preventScroll: true });
  }
}

const CHIP = {
  pending: "chip-gray",
  running: "chip-amber",
  retrying: "chip-amber",
  succeeded: "chip-mint",
  cached: "chip-sky",
  failed: "chip-rose",
  skipped: "chip-gray",
};

function render(step, run) {
  const { spec } = step;
  const rows = [
    ["Inputs", spec.inputs.join(", ") || "none"],
    ["Parameters", spec.params.map((p) => `${p}=${run.params[p]}`).join(" ") || "none"],
    ["Retries", spec.retries ? `up to ${spec.retries}` : "none"],
    ["Attempts", step.attempts.length || (step.status === "cached" ? "0, cache hit" : "–")],
    ["Duration", step.status === "cached" ? "cache hit" : duration(step.duration) || "–"],
    ["Output", step.kind ? `${step.kind} · ${bytes(step.size)}` : "–"],
  ];
  const ids = [
    ["Cache key", step.cacheKey],
    ["Artifact", step.artifact],
    ["Cached from run", step.sourceRun],
  ].filter(([, v]) => v);
  const metrics = Object.entries(step.metrics);
  const retries = step.attempts.filter((a) => a.outcome === "retry");

  return `
    <header class="sheet-head">
      <h3 id="sheet-title">${escape(step.name)}</h3>
      <span class="chip ${CHIP[step.status]}">${step.status}</span>
    </header>
    <div class="rows">
      ${rows.map(([k, v]) => `<div class="row"><span>${k}</span><b>${escape(v)}</b></div>`).join("")}
    </div>
    ${ids.length ? `<div class="sheet-ids">${ids.map(([k, v]) => `<p><span class="spec">${k}</span><code>${escape(v)}</code></p>`).join("")}</div>` : ""}
    ${metrics.length ? `
      <p class="sheet-h">Metrics</p>
      <div class="rows">${metrics.map(([k, v]) => `<div class="row"><span>${escape(k)}</span><code>${number(v)}</code></div>`).join("")}</div>` : ""}
    ${step.logs.length ? `<p class="sheet-h">Log</p><pre class="sheet-log">${escape(step.logs.join("\n"))}</pre>` : ""}
    ${retries.length ? `<p class="sheet-h">Retries</p><pre class="sheet-log">${escape(retries.map((a) => `${a.error}, again in ${a.delay}s`).join("\n"))}</pre>` : ""}
    ${step.because ? `<p class="sheet-note">Skipped because <b>${escape(step.because)}</b> failed upstream.</p>` : ""}
    ${step.error ? `<p class="sheet-h">Error</p><pre class="sheet-error">${escape(step.traceback || step.error)}</pre>` : ""}`;
}
