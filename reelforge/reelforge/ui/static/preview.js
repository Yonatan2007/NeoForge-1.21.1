/**
 * Live style preview: a drawn alpine scene (SVG) in the output aspect ratio
 * with the captions or the opening line on top, approximating the renderer
 * with CSS/SVG so every style change shows instantly. Colour-look settings
 * are approximated with CSS filters.
 */

import { clamp, h, prefersReducedMotion } from "./dom.js";
import { colorToHex } from "./fields.js";
import { icon } from "./icons.js";
import { applyCase, chunkWords, parseScript, splitHook } from "./script-model.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const SCENE = 1920; // the scene is drawn on a 1920x1920 square and cover-cropped to the aspect
const ASPECTS = { "9:16": [1080, 1920], "4:5": [1080, 1350], "1:1": [1080, 1080], "16:9": [1920, 1080] };
const WEIGHTS = { Thin: 100, ExtraLight: 200, Light: 300, Regular: 400, Medium: 500, SemiBold: 600, Bold: 700, ExtraBold: 800, Black: 900 };
const SMALL_WORDS = new Set("a an the of to in on at by for and or but so is it as if be".split(" "));
const SAMPLE_HOOK = "If you lost your memory, who would you trust to tell you who you are?";
const SAMPLE_BODY = "Most of us would call the same few people.";

// Skyline of the main ridge (scene coordinates).
const RIDGE = [[0, 1080], [160, 1050], [300, 1000], [400, 930], [500, 880], [590, 905], [660, 850], [740, 780],
  [800, 720], [850, 770], [905, 705], [965, 640], [1015, 690], [1060, 650], [1110, 730], [1190, 800], [1270, 780],
  [1360, 850], [1470, 905], [1590, 960], [1740, 1010], [1920, 1050]];

function fontFamily(font) {
  const key = String(font || "").toLowerCase();
  if (key === "montserrat") return "Montserrat, 'Arial Black', 'Helvetica Neue', Arial, sans-serif";
  return "Inter, 'Inter Display', system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif";
}

const rgba = (value, alpha = 1) => {
  const hex = colorToHex(value) || "#ffffff";
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
};

/** Smooth path through points (Catmull-Rom converted to cubic Béziers). */
function smoothPath(points) {
  let d = `M${points[0][0]},${points[0][1]}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i - 1] || points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] || p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1[0]},${c1[1]} ${c2[0]},${c2[1]} ${p2[0]},${p2[1]}`;
  }
  return d;
}

function ridgeY(x) {
  for (let i = 0; i < RIDGE.length - 1; i++) {
    const [x0, y0] = RIDGE[i];
    const [x1, y1] = RIDGE[i + 1];
    if (x >= x0 && x <= x1) return y0 + ((x - x0) / (x1 - x0)) * (y1 - y0);
  }
  return RIDGE[RIDGE.length - 1][1];
}

function sceneSvg() {
  const ridge = RIDGE.map(([x, y]) => `${x},${y}`).join(" ");
  return `
<svg class="scene" viewBox="0 0 ${SCENE} ${SCENE}" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false">
  <defs>
    <linearGradient id="pv-sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2f9fb3"/><stop offset="0.45" stop-color="#6cc3cf"/><stop offset="0.62" stop-color="#bfe3e2"/>
    </linearGradient>
    <radialGradient id="pv-sun" cx="0.78" cy="0.3" r="0.45">
      <stop offset="0" stop-color="#fff6d8" stop-opacity="0.85"/><stop offset="1" stop-color="#fff6d8" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="pv-rock" x1="0" y1="0" x2="0.3" y2="1">
      <stop offset="0" stop-color="#d9c7a6"/><stop offset="0.5" stop-color="#b19f82"/><stop offset="1" stop-color="#7f7562"/>
    </linearGradient>
    <linearGradient id="pv-far" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#9fbfc8"/><stop offset="1" stop-color="#b9cfc9"/>
    </linearGradient>
    <linearGradient id="pv-meadow" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#b9c75a"/><stop offset="0.4" stop-color="#8fae3c"/><stop offset="1" stop-color="#4f7a26"/>
    </linearGradient>
    <linearGradient id="pv-grass" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#7fa236"/><stop offset="1" stop-color="#3e6220"/>
    </linearGradient>
    <filter id="pv-blur" x="-20%" y="-50%" width="140%" height="200%"><feGaussianBlur stdDeviation="18"/></filter>
    <filter id="pv-grain"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/></filter>
    <radialGradient id="pv-vignette" cx="0.5" cy="0.5" r="0.75">
      <stop offset="0.55" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity="0.65"/>
    </radialGradient>
  </defs>
  <g class="scene-art">
    <rect width="${SCENE}" height="${SCENE}" fill="url(#pv-sky)"/>
    <rect width="${SCENE}" height="${SCENE}" fill="url(#pv-sun)"/>
    <g fill="#ffffff" opacity="0.75" filter="url(#pv-blur)">
      <ellipse cx="560" cy="520" rx="170" ry="38"/><ellipse cx="660" cy="500" rx="110" ry="44"/>
      <ellipse cx="1380" cy="640" rx="210" ry="34"/><ellipse cx="1260" cy="380" rx="120" ry="26"/>
    </g>
    <path d="M0,1090 L180,1010 L330,1040 L520,960 L700,1010 L900,930 L1120,990 L1330,920 L1560,1000 L1760,950 L1920,1000 L1920,1300 L0,1300Z" fill="url(#pv-far)"/>
    <polygon points="${ridge} 1920,1500 0,1500" fill="url(#pv-rock)"/>
    <path d="M800,720 L830,860 L870,780 L905,705 L940,820 L965,640 L990,780 L1015,690 L1040,800 L1060,650 L1090,800" fill="none" stroke="#eee3cf" stroke-opacity="0.55" stroke-width="10" stroke-linejoin="round"/>
    <path d="M0,1250 C380,1170 760,1190 1100,1240 C1420,1290 1700,1250 1920,1210 L1920,1920 L0,1920Z" fill="url(#pv-meadow)"/>
    <path d="M0,1560 C420,1450 980,1480 1920,1660 L1920,1920 L0,1920Z" fill="url(#pv-grass)"/>
  </g>
  <rect class="scene-lift" width="${SCENE}" height="${SCENE}" fill="#5a5a5a" opacity="0"/>
  <rect class="scene-grain" width="${SCENE}" height="${SCENE}" filter="url(#pv-grain)" opacity="0"/>
  <rect class="scene-vignette" width="${SCENE}" height="${SCENE}" fill="url(#pv-vignette)" opacity="0"/>
  <g class="scene-hook"></g>
</svg>`;
}

/** Visible part of the square scene for an output aspect (cover crop). */
function visibleBox(aspect) {
  const [w, hgt] = ASPECTS[aspect] || ASPECTS["9:16"];
  const r = w / hgt;
  const vw = r < 1 ? SCENE * r : SCENE;
  const vh = r < 1 ? SCENE : SCENE / r;
  return { x: (SCENE - vw) / 2, y: (SCENE - vh) / 2, w: vw, h: vh, ratio: r };
}

function svgEl(tag, attrs = {}, text) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) el.setAttribute(k, v);
  if (text !== undefined) el.textContent = text;
  return el;
}

/** Lay the hook words along the ridge, like hook.py does on real footage. */
function drawHook(group, words, project, box) {
  group.replaceChildren();
  const hs = project.style.hook;
  const cs = project.style.caption;
  if (!words.length || hs.mode !== "terrain") return;
  const unit = box.w / 1080; // scene units per output pixel at 1080 wide
  const margin = (hs.margin ?? 36) * unit;
  const offset = (hs.offset ?? 8) * unit;
  const x0 = box.x + margin;
  const x1 = box.x + box.w - margin;
  const pts = [];
  for (let x = x0; x <= x1 + 1; x += (x1 - x0) / 24) pts.push([x, ridgeY(x) - offset - 12 * unit]);
  const pathId = "pv-hook-path";
  group.append(svgEl("path", { id: pathId, d: smoothPath(pts), fill: "none" }));

  const n = words.length;
  const dropLast = hs.last_word_below && n > 1;
  const onPath = dropLast ? words.slice(0, -1) : words;
  const sizeOf = (w, i) => {
    const big = i === 0 || i === n - 1 || w.emphasis > 0 || w.norm.length >= 7;
    const small = !big && SMALL_WORDS.has(w.norm);
    return (hs.size ?? 58) * (big ? hs.big_scale ?? 2 : small ? hs.small_scale ?? 0.72 : 1);
  };
  const raw = onPath.map((w, i) => sizeOf(w, i));
  // Fit the line to the path: shrink when too long, grow (max 1.4x) when much shorter.
  const pathLen = Math.hypot(x1 - x0, 0) * 1.08;
  const textLen = onPath.reduce((s, w, i) => s + (w.text.length * 0.56 + 0.3) * raw[i] * unit, 0);
  const fit = clamp(pathLen / Math.max(textLen, 1), 0.3, 1.4);
  const color = hs.color === "auto" || !colorToHex(hs.color) ? rgba(hs.dark_color || [17, 17, 17]) : hs.color;
  const attrs = {
    "font-family": fontFamily(hs.font), "font-weight": WEIGHTS[hs.font_weight] || 700, fill: color,
    style: hs.shadow ? "filter: drop-shadow(0 4px 6px rgba(0,0,0,.45))" : null,
  };
  const text = svgEl("text", attrs);
  const tp = svgEl("textPath", { href: `#${pathId}` });
  onPath.forEach((w, i) => {
    tp.append(svgEl("tspan", { "font-size": (raw[i] * fit * unit).toFixed(1) }, applyCase(w.text, cs.case) + (i < onPath.length - 1 ? " " : "")));
  });
  text.append(tp);
  group.append(text);
  if (dropLast) {
    const last = words[n - 1];
    const size = sizeOf(last, n - 1) * Math.min(fit, 1.1) * 1.15 * unit;
    group.append(svgEl("text", {
      ...attrs, "font-size": size.toFixed(1), x: (x1).toFixed(1), y: (ridgeY(x1) - offset + size * 0.9).toFixed(1), "text-anchor": "end",
    }, applyCase(last.text, cs.case)));
  }
}

export function createPreview() {
  const stage = h("div", { class: "preview-stage", html: sceneSvg() });
  const caption = h("div", { class: "pv-caption", "aria-hidden": "true" });
  const frame = h("div", { class: "preview-frame" }, stage, caption);
  const playBtn = h("button", { type: "button", class: "btn btn-ghost btn-sm btn-icon preview-play", "aria-label": "Pause preview animation", html: icon("pause", { size: 16 }) });
  const modeSeg = h("div", { class: "segmented segmented-sm", role: "radiogroup", "aria-label": "Preview" });
  const modes = [["caption", "Captions"], ["hook", "Opening line"]];
  const modeInputs = {};
  for (const [value, label] of modes) {
    const id = `pv-mode-${value}`;
    const input = h("input", { type: "radio", name: "pv-mode", id, class: "visually-hidden", checked: value === "caption" });
    input.addEventListener("change", () => input.checked && setMode(value));
    modeInputs[value] = input;
    modeSeg.append(input, h("label", { for: id }, label));
  }
  const note = h("p", { class: "preview-note muted small" });
  const el = h("figure", { class: "preview" },
    h("div", { class: "preview-bar" }, modeSeg, playBtn), frame, note);

  let project = null;
  let mode = "caption";
  let chunks = [];
  let k = 0;
  let timer = null;
  let playing = !prefersReducedMotion();

  const svg = stage.querySelector("svg");
  const hookGroup = svg.querySelector(".scene-hook");

  function setMode(m) {
    mode = m;
    modeInputs[m].checked = true;
    render();
  }

  function captionSequence() {
    const parsed = parseScript(project.script || "");
    const words = parsed.words.length ? parsed.words : parseScript(`${SAMPLE_HOOK} ${SAMPLE_BODY}`).words;
    const { hook, body } = splitHook(words, project);
    const pool = body.length ? body : words;
    return { hook: hook.length ? hook : project.style.hook.mode === "center" ? [] : parseScript(SAMPLE_HOOK).words, chunks: chunkWords(pool.slice(0, 40), project.style.caption) };
  }

  function styleCaption() {
    const cs = project.style.caption;
    const w = frame.clientWidth || 280;
    const s = w / 1080;
    const shadow = cs.shadow_opacity > 0
      ? `${(cs.shadow_offset?.[0] ?? 0) * s}px ${(cs.shadow_offset?.[1] ?? 3) * s}px ${(cs.shadow_blur ?? 9) * s}px rgba(0,0,0,${cs.shadow_opacity})`
      : "none";
    Object.assign(caption.style, {
      fontFamily: fontFamily(cs.font),
      fontWeight: WEIGHTS[cs.font_weight] || 700,
      fontSize: `${(cs.font_size || 70) * s}px`,
      color: rgba(cs.text_color || [255, 255, 255]),
      top: `${clamp(cs.y_center ?? 0.5, 0, 1) * 100}%`,
      maxWidth: `${Math.min(100, ((cs.max_line_width || 920) / 1080) * 100)}%`,
      lineHeight: String(cs.line_spacing || 1.05),
      textShadow: shadow,
      webkitTextStroke: cs.stroke_width > 0 ? `${cs.stroke_width * 2 * s}px ${rgba(cs.stroke_color || [0, 0, 0])}` : "0",
    });
    caption.style.setProperty("--pop-from", String(cs.pop_start_scale ?? 0.85));
    caption.style.setProperty("--pop-time", `${Math.max(0.05, cs.pop_duration ?? 0.1)}s`);
  }

  function showChunk() {
    const cs = project.style.caption;
    const chunk = chunks.length ? chunks[k % chunks.length] : [];
    const colour = cs.emphasis === "color";
    caption.replaceChildren(...chunk.map((w, i) => {
      const span = h("span", { class: "pv-word" }, applyCase(w.text, cs.case));
      if (colour && w.emphasis) span.style.color = rgba(w.emphasis === 2 ? cs.alert_color : cs.highlight_color);
      if (cs.reveal === "build" && playing) span.style.animationDelay = `${i * 0.28}s`;
      return [i ? " " : "", span];
    }).flat());
  }

  function schedule() {
    clearTimeout(timer);
    if (!playing || mode !== "caption" || chunks.length < 2) return;
    const chunk = chunks[k % chunks.length];
    timer = setTimeout(() => {
      k = (k + 1) % chunks.length;
      showChunk();
      schedule();
    }, 650 + chunk.length * 320);
  }

  function applyLook() {
    const look = project.style.look;
    const art = svg.querySelector(".scene-art");
    const sat = look.saturation ?? 1;
    const con = look.contrast ?? 1;
    const bri = 1 + (look.brightness ?? 0) * 1.5;
    const gamma = look.gamma ?? 1;
    art.style.filter = `saturate(${sat}) contrast(${con}) brightness(${bri / Math.pow(gamma, 0.25)})`;
    svg.querySelector(".scene-lift").setAttribute("opacity", String(clamp((look.lift || 0) * 2.2, 0, 0.35)));
    svg.querySelector(".scene-grain").setAttribute("opacity", String(clamp((look.grain || 0) / 40, 0, 0.35)));
    svg.querySelector(".scene-vignette").setAttribute("opacity", look.vignette ? "1" : "0");
  }

  function render() {
    if (!project) return;
    const aspect = project.style.video.aspect;
    const [aw, ah] = ASPECTS[aspect] || ASPECTS["9:16"];
    frame.style.aspectRatio = `${aw} / ${ah}`;
    frame.dataset.aspect = aspect;
    const seq = captionSequence();
    chunks = seq.chunks;
    k = Math.min(k, Math.max(chunks.length - 1, 0));
    applyLook();
    styleCaption();
    const hookOn = project.style.hook.mode === "terrain";
    modeInputs.hook.disabled = !hookOn;
    if (!hookOn && mode === "hook") mode = "caption";
    modeInputs[mode].checked = true;
    caption.hidden = mode !== "caption";
    playBtn.hidden = mode !== "caption";
    drawHook(hookGroup, mode === "hook" ? seq.hook : [], project, visibleBox(aspect));
    note.textContent = mode === "hook"
      ? "The real reel finds the skyline in your first shot."
      : project.style.hook.mode === "center" ? "The opening line uses these captions too." : "An approximation of the finished frame.";
    showChunk();
    schedule();
  }

  playBtn.addEventListener("click", () => {
    playing = !playing;
    playBtn.innerHTML = icon(playing ? "pause" : "play", { size: 16 });
    playBtn.setAttribute("aria-label", playing ? "Pause preview animation" : "Play preview animation");
    showChunk();
    schedule();
  });
  if (!playing) {
    playBtn.innerHTML = icon("play", { size: 16 });
    playBtn.setAttribute("aria-label", "Play preview animation");
  }

  const ro = new ResizeObserver(() => project && styleCaption());
  ro.observe(frame);

  return {
    el,
    /** Re-render for `p`; `focus` = "caption" | "hook" switches the view. */
    update(p, focus) {
      project = p;
      if (focus === "hook" && p.style.hook.mode === "terrain") mode = "hook";
      else if (focus === "caption") mode = "caption";
      render();
    },
    destroy() {
      clearTimeout(timer);
      ro.disconnect();
    },
  };
}
