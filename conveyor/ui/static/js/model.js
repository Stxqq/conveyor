import { el } from "./dom.js";
import { escape, short } from "./format.js";
import { Counter } from "./motion.js";
import { DONE, SETTLED } from "./run.js";

const HEADLINE = [
  { key: "roc_auc", label: "ROC AUC", better: 1 },
  { key: "pr_auc", label: "PR AUC", better: 1 },
  { key: "log_loss", label: "Log loss", better: -1 },
];

// Which json output is which is read off its shape, not the step's name:
// a registration says {model, version, champion}, a gate {promote, reason},
// a drift report {features: {name: {psi}}, thresholds}.
const isRegistration = (v) => v && "model" in v && "version" in v && "champion" in v;
const isGate = (v) => v && "promote" in v && "reason" in v;
const isDrift = (v) => v && v.features && v.thresholds && "drift" in v.thresholds;

export class ModelView {
  #values = new Map();
  #models = [];
  #html = "";
  #run = null;

  constructor(root, { source }) {
    this.source = source;
    this.tile = el("div", "tile model-tile");
    root.append(this.tile);
  }

  setModels(models) {
    this.#models = models;
    if (this.#run) this.update(this.#run);
  }

  update(run) {
    this.#run = run;
    const found = {};
    for (const step of run.steps.values()) {
      if (!DONE.has(step.status) || step.kind !== "json") continue;
      if (!this.#values.has(step.artifact)) {
        this.#values.set(step.artifact, undefined);
        this.source
          .value(step.artifact)
          .then((value) => this.#values.set(step.artifact, value))
          .catch(() => this.#values.set(step.artifact, null))
          .then(() => this.#run === run && this.update(run));
      }
      const value = this.#values.get(step.artifact);
      if (isRegistration(value)) found.registration = value;
      else if (isGate(value)) found.gate = value;
      else if (isDrift(value)) found.drift = value;
    }
    this.#render(run, found);
  }

  #render(run, { registration, gate, drift }) {
    const family = this.#models.find((m) => m.name === registration?.model) ?? this.#models[0];
    const finished = SETTLED.has(run.status);
    let card = null;
    let champion = null;
    let note = "";
    if (registration && family) {
      card = family.versions.find((v) => v.version === registration.version);
      champion = family.versions.find((v) => v.version === registration.champion);
    } else if (finished && family) {
      champion = card = family.versions.find((v) => v.version === family.champion);
      note = `This run didn't register a model. The champion is still v${family.champion}.`;
    }

    const html = card
      ? modelCard(card, champion, gate, drift, note)
      : `<div class="model-wait"><span class="spec">Model card</span><p>Appears once the run registers a model.</p></div>`;
    if (html === this.#html) return;
    this.#html = html;
    this.tile.innerHTML = html;
    for (const node of this.tile.querySelectorAll("[data-count]")) {
      const digits = Number(node.dataset.digits);
      new Counter((v) => (node.textContent = v.toFixed(digits))).set(Number(node.dataset.count));
    }
    requestAnimationFrame(() => this.tile.querySelector(".model-card")?.classList.add("is-in"));
  }
}

function modelCard(card, champion, gate, drift, note) {
  const isChampion = champion && champion.version === card.version;
  const status = isChampion
    ? '<span class="chip chip-dark">champion</span>'
    : `<span class="chip chip-gray">challenger · champion is v${champion?.version}</span>`;
  const metrics = card.metrics;
  const headline = HEADLINE.map(({ key, label, better }) => {
    let delta = '<span class="delta">first champion</span>';
    if (!isChampion && champion) delta = deltaTag(metrics[key] - champion.metrics[key], better);
    else if (isChampion && gate?.champion_auc != null && key === "roc_auc") {
      delta = deltaTag(gate.gain, better, `vs v${gate.champion}`);
    } else if (isChampion && card.version > 1) delta = '<span class="delta">champion</span>';
    return `
      <div class="big">
        <span class="spec">${label}</span>
        <strong data-count="${metrics[key]}" data-digits="4">0.0000</strong>
        ${delta}
      </div>`;
  }).join("");

  const decision = gate
    ? `<p class="gate-line"><span class="spec">Gate</span>${escape(gate.reason)}. ${
        gate.promote ? `v${card.version} is promoted.` : `v${gate.champion ?? champion?.version} stays champion.`
      }</p>`
    : "";

  return `
    <article class="card model-card">
      <header class="model-head">
        <div class="model-name"><b>${escape(card.name)}</b><code>v${card.version}</code>${status}</div>
        <p>${escape(card.description ?? "")}</p>
        ${note ? `<p class="model-note">${escape(note)}</p>` : ""}
      </header>
      <div class="big-row">${headline}</div>
      <div class="minor-row">
        <span>Brier <code>${metrics.brier.toFixed(4)}</code></span>
        <span>ECE <code>${metrics.ece.toFixed(3)}</code></span>
        <span>Top-decile lift <code>${metrics.lift_top_decile.toFixed(2)}×</code></span>
      </div>
      <div class="model-figs">
        <figure class="fig">
          ${calibration(card.calibration)}
          <figcaption><b>Calibration</b><span>Observed churn rate per predicted-risk bin on the test months. Dots sized by rows.</span></figcaption>
        </figure>
        <figure class="fig">
          ${drift ? psi(drift) : '<div class="psi-wait">Drift report appears once monitor runs.</div>'}
          <figcaption><b>Drift</b><span>Population stability of each feature in the month being scored, against the training months.</span></figcaption>
        </figure>
      </div>
      ${decision}
      <dl class="spec-rows">
        <div><dt>Algorithm</dt><dd>${escape(card.algorithm)}</dd></div>
        <div><dt>Parameters</dt><dd><code>${escape(Object.entries(card.params).map(([k, v]) => `${k}=${v}`).join(" "))}</code></dd></div>
        <div><dt>Train · test months</dt><dd><code>${card.train_months.join("–")} · ${card.test_months.join("–")}</code></dd></div>
        <div><dt>Features</dt><dd><code>${card.features.length}</code></dd></div>
        <div><dt>Digest</dt><dd><code>${short(card.digest)}</code></dd></div>
      </dl>
    </article>`;
}

function deltaTag(diff, better, suffix = "vs champion") {
  if (Math.abs(diff) < 5e-5) return `<span class="delta">± 0 ${suffix}</span>`;
  const up = diff > 0;
  const good = up === better > 0;
  return `<span class="delta ${good ? "good" : "bad"}">${up ? "▲" : "▼"} ${Math.abs(diff).toFixed(4)} <em>${suffix}</em></span>`;
}

function calibration(bins) {
  const size = 220;
  const pad = 28;
  const scale = (v) => pad + v * (size - pad - 8);
  const y = (v) => size - scale(v);
  const most = Math.max(...bins.map((b) => b.count));
  const line = bins.map((b) => `${scale(b.predicted)},${y(b.observed)}`).join(" ");
  const dots = bins
    .map(
      (b, i) =>
        `<circle cx="${scale(b.predicted)}" cy="${y(b.observed)}" r="${(2 + Math.sqrt(b.count / most) * 5).toFixed(2)}" style="--i:${i}"><title>${b.count} rows · predicted ${b.predicted} · observed ${b.observed}</title></circle>`,
    )
    .join("");
  const grid = [0, 0.5, 1]
    .map(
      (v) => `
      <line class="grid" x1="${scale(0)}" x2="${scale(1)}" y1="${y(v)}" y2="${y(v)}"/>
      <text x="${scale(0) - 6}" y="${y(v) + 3}" text-anchor="end">${v}</text>
      <text x="${scale(v)}" y="${size - 10}" text-anchor="middle">${v}</text>`,
    )
    .join("");
  return `
    <svg class="calib" viewBox="0 0 ${size} ${size}" role="img" aria-label="Calibration plot">
      ${grid}
      <line class="ideal" x1="${scale(0)}" y1="${y(0)}" x2="${scale(1)}" y2="${y(1)}"/>
      <polyline class="curve" points="${line}"/>
      <g class="dots">${dots}</g>
    </svg>`;
}

function psi(report) {
  const entries = Object.entries(report.features).sort((a, b) => b[1].psi - a[1].psi);
  const { watch, drift } = report.thresholds;
  const top = Math.max(drift * 2, ...entries.map(([, f]) => f.psi)) * 1.08;
  const pct = (v) => `${(v / top) * 100}%`;
  const rows = entries
    .map(
      ([name, f], i) => `
      <div class="psi-row is-${f.status}" style="--i:${i}">
        <span class="psi-name">${escape(name)}</span>
        <span class="psi-track"><i style="--w:${f.psi / top}"></i></span>
        <code>${f.psi.toFixed(3)}</code>
      </div>`,
    )
    .join("");
  return `
    <div class="psi" style="--watch:${watch / top};--drift:${drift / top}">
      <div class="psi-marks">
        <span style="left:${pct(watch)}"><em>watch </em><b>${watch}</b></span>
        <span style="left:${pct(drift)}"><b>${drift}</b><em> drift</em></span>
      </div>
      ${rows}
    </div>`;
}
