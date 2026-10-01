// Left-to-right layered layout for a pipeline graph.
//
// Layers come from the longest path, and sources move right to sit next to
// their first consumer. The longest chain gets row 0; side branches take the
// nearest free row, sources above and everything else below. Edges that skip
// layers ride in lanes above their row (stacked like nested brackets), or
// along an empty row when there is one.

export const NODE = { w: 96, h: 60 };
const GAP_X = 30;
const GAP_Y = 30;
const LANE = 12;
const LANE_PAD = 18;
// Edges meeting at a node spread out by this much instead of landing on one point.
const PORT = 7;

export function layoutGraph(graph, pad = { x: 24, y: 24 }) {
  const order = topological(graph);
  const parents = new Map(graph.map((s) => [s.name, s.inputs]));
  const children = new Map(graph.map((s) => [s.name, []]));
  for (const s of graph) for (const p of s.inputs) children.get(p)?.push(s.name);

  const layer = new Map();
  for (const name of order) {
    const ps = parents.get(name);
    layer.set(name, ps.length ? Math.max(...ps.map((p) => layer.get(p) + 1)) : 0);
  }
  for (const name of order) {
    const cs = children.get(name);
    if (!parents.get(name).length && cs.length) {
      layer.set(name, Math.min(...cs.map((c) => layer.get(c))) - 1);
    }
  }

  const row = new Map();
  const taken = new Set();
  const place = (name, r) => {
    row.set(name, r);
    taken.add(`${layer.get(name)}:${r}`);
  };
  for (const name of longestChain(order, parents, layer)) place(name, 0);
  for (const name of order) {
    if (row.has(name)) continue;
    const anchor = [...parents.get(name), ...children.get(name)].find((n) => row.has(n));
    const preferred = anchor ? row.get(anchor) : 0;
    const away = parents.get(name).length ? 1 : -1;
    for (let k = 0; ; k++) {
      const r = preferred + (k % 2 ? away : -away) * Math.ceil(k / 2);
      if (!taken.has(`${layer.get(name)}:${r}`)) {
        place(name, r);
        break;
      }
    }
  }

  const rows = [...row.values()];
  const minRow = Math.min(...rows);
  const maxRow = Math.max(...rows);
  const free = (r, from, to) => {
    for (let l = from + 1; l < to; l++) if (taken.has(`${l}:${r}`)) return false;
    return true;
  };

  const edges = [];
  for (const s of graph) {
    for (const p of s.inputs) {
      const e = { from: p, to: s.name, a: layer.get(p), b: layer.get(s.name) };
      const ra = row.get(p);
      const rb = row.get(s.name);
      if (e.b - e.a === 1) e.route = "direct";
      else if (ra !== rb && free(ra, e.a, e.b)) e.route = "along-source";
      else if (ra !== rb && free(rb, e.a, e.b)) e.route = "along-target";
      else if (ra === rb && free(ra, e.a, e.b)) e.route = "direct";
      else {
        e.route = "lane";
        e.channel = Math.min(ra, rb);
      }
      edges.push(e);
    }
  }

  const lanes = new Map();
  const laneEdges = edges.filter((e) => e.route === "lane").sort((x, y) => x.b - x.a - (y.b - y.a));
  for (const e of laneEdges) {
    const stack = lanes.get(e.channel) ?? [];
    let k = 0;
    while (stack[k]?.some((o) => e.a < o.b && o.a < e.b)) k++;
    (stack[k] ??= []).push(e);
    e.lane = k;
    lanes.set(e.channel, stack);
  }

  const rowTop = new Map();
  let y = pad.y;
  for (let r = minRow; r <= maxRow; r++) {
    const used = lanes.get(r)?.length ?? 0;
    const channel = used ? used * LANE + LANE_PAD * 2 : 0;
    y += r === minRow ? Math.max(0, channel - LANE_PAD) : Math.max(GAP_Y, channel);
    rowTop.set(r, y);
    y += NODE.h;
  }

  const nodes = new Map();
  for (const name of order) {
    const x = pad.x + layer.get(name) * (NODE.w + GAP_X);
    const top = rowTop.get(row.get(name));
    nodes.set(name, { name, layer: layer.get(name), row: row.get(name), x, y: top, cy: top + NODE.h / 2 });
  }

  for (const e of edges) {
    if (e.route === "lane") e.ly = rowTop.get(e.channel) - LANE_PAD - e.lane * LANE;
  }
  // Ports are ordered by where the edge comes from or heads to, top to bottom,
  // so the fanned-out ends don't cross each other.
  const heading = (e, end) => {
    if (e.route === "lane") return e.ly;
    if (e.route === "along-source") return nodes.get(e.from).cy;
    if (e.route === "along-target") return nodes.get(e.to).cy;
    return nodes.get(end).cy;
  };
  fan(edges, "to", "dyIn", (e) => heading(e, e.from));
  fan(edges, "from", "dyOut", (e) => heading(e, e.to));

  for (const e of edges) {
    const s = nodes.get(e.from);
    const t = nodes.get(e.to);
    const x1 = s.x + NODE.w;
    const x2 = t.x;
    const y1 = s.cy + e.dyOut;
    const y2 = t.cy + e.dyIn;
    if (e.route === "direct") e.d = curve(x1, y1, x2, y2);
    else if (e.route === "along-source") e.d = `M${x1} ${y1}H${x2 - GAP_X}` + tail(x2 - GAP_X, y1, x2, y2);
    else if (e.route === "along-target") e.d = `M${x1} ${y1}` + tail(x1, y1, x1 + GAP_X, y2) + `H${x2}`;
    else {
      e.d = `M${x1} ${y1}` + tail(x1, y1, x1 + GAP_X, e.ly) + `H${x2 - GAP_X}` + tail(x2 - GAP_X, e.ly, x2, y2);
    }
  }

  const layers = Math.max(...layer.values()) + 1;
  return {
    nodes,
    edges,
    width: pad.x * 2 + layers * NODE.w + (layers - 1) * GAP_X,
    height: y + pad.y,
  };
}

function fan(edges, end, key, order) {
  const groups = new Map();
  for (const e of edges) groups.set(e[end], [...(groups.get(e[end]) ?? []), e]);
  for (const group of groups.values()) {
    group.sort((a, b) => order(a) - order(b) || a.b - a.a - (b.b - b.a));
    group.forEach((e, i) => (e[key] = (i - (group.length - 1) / 2) * PORT));
  }
}

function curve(x1, y1, x2, y2) {
  return `M${x1} ${y1}` + tail(x1, y1, x2, y2);
}

function tail(x1, y1, x2, y2) {
  const mx = (x1 + x2) / 2;
  return `C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}`;
}

function topological(graph) {
  const pending = new Map(graph.map((s) => [s.name, new Set(s.inputs)]));
  const order = [];
  while (pending.size) {
    const ready = [...pending].filter(([, deps]) => !deps.size).map(([n]) => n);
    if (!ready.length) throw new Error("graph has a cycle");
    for (const name of ready) {
      order.push(name);
      pending.delete(name);
      for (const deps of pending.values()) deps.delete(name);
    }
  }
  return order;
}

function longestChain(order, parents, layer) {
  let end = order[0];
  for (const name of order) if (layer.get(name) > layer.get(end)) end = name;
  const chain = [end];
  for (let at = end; parents.get(at).length; ) {
    at = parents.get(at).find((p) => layer.get(p) === layer.get(at) - 1) ?? parents.get(at)[0];
    chain.push(at);
  }
  return chain;
}
