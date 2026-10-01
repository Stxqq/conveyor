import { el } from "./dom.js";
import { duration, escape, params } from "./format.js";
import { cacheRatio } from "./run.js";

export class RunsView {
  #html = "";

  constructor(root, { onSelect }) {
    this.tile = el("div", "tile runs-tile");
    this.card = el("div", "card runs-card");
    this.card.innerHTML = `
      <div class="runs-head"><span>Run</span><span>Duration</span><span>Cache</span></div>
      <div class="runs-list" role="list"></div>`;
    this.list = this.card.lastElementChild;
    this.tile.append(this.card);
    root.append(this.tile);
    this.list.addEventListener("click", (e) => {
      const row = e.target.closest("[data-key]");
      if (row) onSelect(row.dataset.key);
    });
  }

  update(runs, selected) {
    const html = runs.map((run, i) => row(run, i, run.key === selected)).join("");
    if (html === this.#html) return;
    // only the first render staggers in; later ones just swap the rows
    const first = !this.#html;
    this.#html = html;
    this.list.innerHTML = html || '<p class="runs-empty">No runs yet. Start one with <code>conveyor run</code>.</p>';
    this.list.classList.toggle("is-entering", first);
  }
}

function row(run, i, selected) {
  const ratio = cacheRatio(run.counts);
  const share = ratio ? ratio.cached / ratio.total : 0;
  const sub = [run.id, params(run.params)].filter(Boolean).join(" · ");
  return `
    <button class="run-row${selected ? " is-selected" : ""}" type="button" role="listitem"
      data-key="${escape(run.key)}" style="--i:${i}" aria-current="${selected}">
      <i class="status-dot is-${run.status}" title="${run.status}"></i>
      <span class="run-title">${escape(run.title)}<small>${escape(sub)}</small></span>
      <span class="run-dur">${run.duration == null ? "–" : duration(run.duration)}</span>
      <span class="run-cache">
        <span class="ratio"><i style="transform:scaleX(${share})"></i></span>
        <code>${ratio ? `${ratio.cached}/${ratio.total}` : "–"}</code>
      </span>
    </button>`;
}
