// Replays an event stream on a slowed-down clock. A cold churn run takes
// about 300 ms, far too quick to watch, so time is stretched and every step
// event gets a minimum beat of its own; a cache hit still reads as a ripple.

export const STRETCH = 15;
const BEAT = {
  step_started: 110,
  step_cached: 80,
  step_succeeded: 110,
  step_failed: 180,
  step_skipped: 45,
  step_retry: 160,
  run_finished: 260,
};
const LEAD_IN = 500;

export class Player {
  events = [];
  at = [];
  t = 0;
  index = 0;
  speed = 1;
  playing = false;
  live = false;
  #frame = 0;
  #last = 0;

  constructor(onChange) {
    this.onChange = onChange;
  }

  load(events, { live = false } = {}) {
    this.pause();
    this.events = events;
    this.live = live;
    this.at = [];
    events.forEach((event, i) => this.at.push(this.#timeOf(event, i)));
    this.t = live ? this.length : 0;
    this.#sync();
    this.onChange(this);
  }

  append(event) {
    this.events.push(event);
    this.at.push(this.#timeOf(event, this.events.length - 1));
    this.t = this.length;
    this.index = this.events.length;
    if (event.type === "run_finished") this.live = false;
    this.onChange(this);
  }

  #timeOf(event, i) {
    if (i === 0) return 0;
    const gap = (event.ts - this.events[i - 1].ts) * 1000 * STRETCH;
    // the graph sits there pending for a moment before the first step starts
    return this.at[i - 1] + Math.max(gap, BEAT[event.type] ?? 0) + (i === 1 ? LEAD_IN : 0);
  }

  get length() {
    return this.at.at(-1) ?? 0;
  }

  get ended() {
    return !this.live && this.length > 0 && this.t >= this.length;
  }

  get progress() {
    return this.length ? Math.min(1, this.t / this.length) : 0;
  }

  // Seconds of real run time at the replay position, interpolated between events.
  get wall() {
    const { events, at, t } = this;
    if (!events.length) return 0;
    const t0 = events[0].ts;
    if (this.live) return Math.max(0, Date.now() / 1000 - t0);
    const i = this.index - 1;
    if (i < 0) return 0;
    if (i >= events.length - 1) return events.at(-1).ts - t0;
    const span = at[i + 1] - at[i];
    const k = span > 0 ? (t - at[i]) / span : 0;
    return events[i].ts - t0 + (events[i + 1].ts - events[i].ts) * k;
  }

  play() {
    if (this.live || this.playing) return;
    if (this.ended) this.seek(0);
    this.playing = true;
    this.#last = performance.now();
    this.#frame = requestAnimationFrame((now) => this.#tick(now));
    this.onChange(this);
  }

  pause() {
    if (!this.playing) return;
    cancelAnimationFrame(this.#frame);
    this.playing = false;
    this.onChange(this);
  }

  toggle() {
    if (this.playing) this.pause();
    else this.play();
  }

  seek(progress) {
    this.t = Math.max(0, Math.min(1, progress)) * this.length;
    this.#sync();
    this.onChange(this);
  }

  #sync() {
    let i = 0;
    while (i < this.at.length && this.at[i] <= this.t) i++;
    this.index = i;
  }

  #tick(now) {
    // clamped so a tab coming back from the background doesn't skip the run
    const dt = Math.min(64, now - this.#last);
    this.#last = now;
    this.t = Math.min(this.length, this.t + dt * this.speed);
    this.#sync();
    if (this.t >= this.length) this.playing = false;
    this.onChange(this);
    if (this.playing) this.#frame = requestAnimationFrame((n) => this.#tick(n));
  }
}
