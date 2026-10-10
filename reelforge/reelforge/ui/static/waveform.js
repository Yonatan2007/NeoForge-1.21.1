/**
 * A horizontal time track with a draggable range: drag either handle, drag
 * the range itself to move it, or drag on empty track to draw a new range.
 * Handles are ARIA sliders (arrow keys: 0.1 s, Shift: 1 s, PageUp/Down: 5 s,
 * Home/End), so everything works without a mouse.
 *
 * Used for the music selection over a waveform and for the music's place
 * on the video timeline.
 */

import { clamp, formatTime, h } from "./dom.js";

const KEY_STEPS = { ArrowLeft: -0.1, ArrowRight: 0.1, ArrowDown: -0.1, ArrowUp: 0.1, PageDown: -5, PageUp: 5 };

export class RangeTrack {
  /**
   * @param {object} o
   * @param {number} o.duration      length of the track in seconds
   * @param {number} o.start         range start
   * @param {number} o.end           range end
   * @param {number} [o.minLength]   smallest allowed range
   * @param {object} [o.labels]      {start, end, range} accessible names
   * @param {(s:number,e:number)=>void} [o.onInput]   while dragging
   * @param {(s:number,e:number)=>void} [o.onCommit]  when a drag/key change ends
   * @param {(t:number)=>void} [o.onClick]            click on the track without dragging
   * @param {string} [o.className]
   */
  constructor(o) {
    this.duration = Math.max(o.duration || 1, 0.1);
    this.minLength = o.minLength ?? 0.5;
    this.start = o.start;
    this.end = o.end;
    this.onInput = o.onInput || (() => {});
    this.onCommit = o.onCommit || (() => {});
    this.onClick = o.onClick || null;
    const labels = { start: "Start", end: "End", range: "Range", ...(o.labels || {}) };

    this.content = h("div", { class: "track-content" });
    this.region = h("div", { class: "track-region", role: "slider", tabindex: "0", "aria-label": labels.range, "aria-valuemin": 0 },
      h("div", { class: "track-region-inner" }));
    this.handleStart = this.#handle("start", labels.start);
    this.handleEnd = this.#handle("end", labels.end);
    this.dimLeft = h("div", { class: "track-dim" });
    this.dimRight = h("div", { class: "track-dim" });
    this.el = h("div", { class: ["track", o.className] }, this.content, this.dimLeft, this.dimRight, this.region, this.handleStart, this.handleEnd);
    this.el.addEventListener("pointerdown", (e) => this.#pointerDown(e));
    this.region.addEventListener("keydown", (e) => this.#key(e, "move"));
    this.layout();
  }

  #handle(which, label) {
    const el = h("div", { class: `track-handle track-handle-${which}`, role: "slider", tabindex: "0", "aria-label": label, "aria-valuemin": 0 },
      h("span", { class: "track-handle-grip" }), h("span", { class: "track-bubble", "aria-hidden": "true" }));
    el.addEventListener("keydown", (e) => this.#key(e, which));
    return el;
  }

  /** Set range and/or duration from outside (no callbacks). */
  set({ start = this.start, end = this.end, duration = this.duration } = {}) {
    this.duration = Math.max(duration, 0.1);
    this.start = clamp(start, 0, this.duration);
    this.end = clamp(end, this.start, this.duration);
    this.layout();
  }

  pct(t) {
    return `${(clamp(t, 0, this.duration) / this.duration) * 100}%`;
  }

  layout() {
    const { start, end } = this;
    this.region.style.left = this.pct(start);
    this.region.style.width = `${((end - start) / this.duration) * 100}%`;
    this.handleStart.style.left = this.pct(start);
    this.handleEnd.style.left = this.pct(end);
    this.dimLeft.style.left = "0";
    this.dimLeft.style.width = this.pct(start);
    this.dimRight.style.left = this.pct(end);
    this.dimRight.style.right = "0";
    const max = this.duration.toFixed(1);
    for (const [el, v] of [[this.handleStart, start], [this.handleEnd, end]]) {
      el.setAttribute("aria-valuemax", max);
      el.setAttribute("aria-valuenow", v.toFixed(1));
      el.setAttribute("aria-valuetext", formatTime(v));
      el.querySelector(".track-bubble").textContent = formatTime(v);
    }
    this.region.setAttribute("aria-valuemax", max);
    this.region.setAttribute("aria-valuenow", start.toFixed(1));
    this.region.setAttribute("aria-valuetext", `${formatTime(start)} to ${formatTime(end)}`);
  }

  #timeAt(clientX) {
    const r = this.el.getBoundingClientRect();
    return clamp(((clientX - r.left) / r.width) * this.duration, 0, this.duration);
  }

  #apply(start, end) {
    const min = Math.min(this.minLength, this.duration);
    this.start = clamp(start, 0, this.duration - min);
    this.end = clamp(end, this.start + min, this.duration);
    this.layout();
    this.onInput(this.start, this.end);
  }

  #pointerDown(e) {
    if (e.button !== 0) return;
    const t0 = this.#timeAt(e.clientX);
    const target = e.target.closest(".track-handle, .track-region");
    const mode = target === this.handleStart ? "start" : target === this.handleEnd ? "end" : target === this.region ? "move" : "draw";
    const { start, end } = this;
    const x0 = e.clientX;
    let moved = false;
    e.preventDefault();
    this.el.setPointerCapture(e.pointerId);
    this.el.classList.add("is-dragging");
    (mode === "start" ? this.handleStart : mode === "end" ? this.handleEnd : this.region).focus({ preventScroll: true });

    const move = (ev) => {
      if (!moved && Math.abs(ev.clientX - x0) < 3) return;
      moved = true;
      const t = this.#timeAt(ev.clientX);
      if (mode === "start") this.#apply(Math.min(t, end - this.minLength), end);
      else if (mode === "end") this.#apply(start, Math.max(t, start + this.minLength));
      else if (mode === "move") {
        const len = end - start;
        const s = clamp(start + (t - t0), 0, this.duration - len);
        this.#apply(s, s + len);
      } else this.#apply(Math.min(t0, t), Math.max(t0, t));
    };
    const up = () => {
      this.el.removeEventListener("pointermove", move);
      this.el.removeEventListener("pointerup", up);
      this.el.removeEventListener("pointercancel", up);
      this.el.classList.remove("is-dragging");
      if (moved) this.onCommit(this.start, this.end);
      else if (this.onClick) this.onClick(t0);
    };
    this.el.addEventListener("pointermove", move);
    this.el.addEventListener("pointerup", up);
    this.el.addEventListener("pointercancel", up);
  }

  #key(e, which) {
    let delta = KEY_STEPS[e.key];
    let { start, end } = this;
    const len = end - start;
    if (e.key === "Home") delta = -Infinity;
    else if (e.key === "End") delta = Infinity;
    else if (delta === undefined) return;
    else if (e.shiftKey) delta *= 10;
    e.preventDefault();
    if (which === "start") start = clamp(start + delta, 0, end - this.minLength);
    else if (which === "end") end = clamp(end + delta, start + this.minLength, this.duration);
    else {
      start = clamp(start + delta, 0, this.duration - len);
      end = start + len;
    }
    this.#apply(start, end);
    this.onCommit(this.start, this.end);
  }
}

/** Nice tick spacing so a track of `duration` seconds gets at most ~`max` labels. */
export function tickStep(duration, max = 8) {
  for (const s of [0.5, 1, 2, 5, 10, 15, 20, 30, 60, 120, 300]) if (duration / s <= max) return s;
  return 600;
}

/** Time labels under a track. */
export function axis(duration, max = 8) {
  const step = tickStep(duration, max);
  const el = h("div", { class: "axis", "aria-hidden": "true" });
  for (let t = 0; t <= duration + 1e-6; t += step) {
    el.append(h("span", { class: "axis-tick", style: { left: `${(t / duration) * 100}%` } }, formatTime(t, false)));
  }
  return el;
}

/**
 * Draw mirrored waveform bars into `canvas`; bars inside [selStart, selEnd]
 * (seconds) use the accent colour. Reads colours from CSS variables.
 */
export function drawWaveform(canvas, peaks, duration, selStart, selEnd) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const hgt = canvas.clientHeight;
  if (!w || !hgt) return;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(hgt * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(hgt * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, hgt);
  const css = getComputedStyle(canvas);
  const base = css.getPropertyValue("--wave-color").trim() || "#4b5563";
  const active = css.getPropertyValue("--wave-active").trim() || "#3fc1c9";
  const barW = 2;
  const gap = 1;
  const bars = Math.max(1, Math.floor(w / (barW + gap)));
  const mid = hgt / 2;
  const data = peaks && peaks.length ? peaks : null;
  for (let i = 0; i < bars; i++) {
    const t = ((i + 0.5) / bars) * duration;
    let v = 0.06;
    if (data) {
      const a = Math.floor((i / bars) * data.length);
      const b = Math.max(a + 1, Math.floor(((i + 1) / bars) * data.length));
      v = 0;
      for (let k = a; k < b; k++) v = Math.max(v, data[k] || 0);
      v = Math.max(0.03, Math.pow(v, 0.8));
    }
    const bh = v * (hgt - 4);
    ctx.fillStyle = t >= selStart && t <= selEnd ? active : base;
    ctx.fillRect(i * (barW + gap), mid - bh / 2, barW, Math.max(1, bh));
  }
}
