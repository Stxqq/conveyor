// Where runs come from. `conveyor ui` serves a JSON API and streams running
// pipelines over server-sent events; the GitHub Pages copy of this page has
// no server and reads the runs recorded by scripts/record_demo.py instead.

async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

const STATUSES = ["succeeded", "cached", "failed", "skipped"];

export function openSource() {
  const mode = document.querySelector('meta[name="conveyor-source"]')?.content;
  return mode === "live" ? new LiveSource() : new RecordedSource();
}

class RecordedSource {
  live = false;
  #files = new Map();

  async runs() {
    const index = await getJSON("runs/index.json");
    return index.map((entry) => {
      const key = entry.file.replace(/\.json$/, "");
      this.#files.set(key, entry.file);
      return {
        key,
        id: entry.run_id,
        title: entry.title,
        params: entry.params,
        status: entry.status,
        duration: entry.duration,
        counts: entry.counts,
      };
    });
  }

  events(key) {
    return getJSON(`runs/${this.#files.get(key)}`);
  }

  models() {
    return getJSON("runs/models.json");
  }

  value(artifact) {
    return getJSON(`runs/artifacts/${artifact}.json`);
  }
}

class LiveSource {
  live = true;

  async runs() {
    const rows = await getJSON("api/runs?limit=30");
    return rows.map((row) => ({
      key: row.id,
      id: row.id,
      title: `${row.pipeline} ${row.id.slice(-4)}`,
      params: {},
      status: row.status,
      duration: row.duration ?? null,
      startedAt: row.started_at,
      counts: Object.fromEntries(STATUSES.map((k) => [k, row[k] ?? 0])),
    }));
  }

  events(key) {
    return getJSON(`api/runs/${encodeURIComponent(key)}/events`);
  }

  // Calls onEvent for every event from the start of the run, then onEnd once
  // run_finished has arrived or the connection drops.
  stream(key, onEvent, onEnd) {
    const source = new EventSource(`api/runs/${encodeURIComponent(key)}/stream`);
    let open = true;
    const close = () => {
      if (!open) return;
      open = false;
      source.close();
      onEnd();
    };
    source.onmessage = (message) => {
      const event = JSON.parse(message.data);
      onEvent(event);
      if (event.type === "run_finished") close();
    };
    // EventSource reconnects on its own and would replay the run from the top.
    source.onerror = close;
    return close;
  }

  models() {
    return getJSON("api/models");
  }

  value(artifact) {
    return getJSON(`api/artifacts/${artifact}/value`);
  }
}
