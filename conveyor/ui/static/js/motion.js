export const FOLLOW = 0.085;
export const FRICTION = 0.94;

const reduced = matchMedia("(prefers-reduced-motion: reduce)");
export const still = () => reduced.matches;

// Runs fn every frame until it returns false.
export function animate(fn) {
  let last = performance.now();
  const frame = (now) => {
    const dt = Math.min(64, now - last);
    last = now;
    if (fn(dt) !== false) requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

// A number that eases towards its target with the per-frame lerp.
export class Counter {
  constructor(render, value = 0) {
    this.render = render;
    this.value = value;
    this.target = value;
    this.running = false;
  }

  set(target) {
    this.target = target;
    if (still()) {
      this.value = target;
      this.render(target);
      return;
    }
    if (this.running) return;
    this.running = true;
    animate(() => {
      this.value += (this.target - this.value) * FOLLOW;
      const done = Math.abs(this.target - this.value) < Math.abs(this.target) * 1e-4 + 1e-6;
      if (done) this.value = this.target;
      this.render(this.value);
      this.running = !done;
      return !done;
    });
  }
}
