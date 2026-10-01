export function duration(seconds) {
  if (seconds == null) return "";
  if (seconds < 0.001) return "<1 ms";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  return `${seconds.toFixed(2)} s`;
}

export function bytes(n) {
  if (n == null) return "";
  if (n < 1000) return `${n} B`;
  if (n < 1e6) return `${(n / 1000).toFixed(n < 1e4 ? 1 : 0)} kB`;
  return `${(n / 1e6).toFixed(1)} MB`;
}

export const short = (hash, n = 12) => (hash ? hash.slice(0, n) : "");

export function number(value) {
  if (value == null) return "–";
  if (Number.isInteger(value)) return value.toLocaleString("en-US");
  const abs = Math.abs(value);
  if (abs >= 100) return value.toFixed(1);
  if (abs >= 1) return value.toFixed(3);
  return value.toPrecision(4);
}

export function params(values) {
  return Object.entries(values || {})
    .map(([k, v]) => `${k}=${v}`)
    .join(" ");
}

export function escape(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}
