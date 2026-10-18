/**
 * Footage tab: the user's photos and clips as cards (use in the video or
 * reference only, pin to a shot, trims, image motion, notes, order by
 * dragging) and the stock footage settings.
 */

import { callout, dropzone, uploadAll } from "../components.js";
import { basename, button, clamp, clear, formatTime, h, toast, toastError } from "../dom.js";
import { findField, findSection, renderFields, segmented } from "../fields.js";
import { icon } from "../icons.js";
import { lengthPlan, parseScript } from "../script-model.js";
import {
  fileUrl, on, projectBinding, removeUpload, schemaSections, setValue, state, supports, touched, uploadFor,
} from "../store.js";

const MOTIONS = ["auto", "zoom-in", "zoom-out", "pan-left", "pan-right", "none"];
const SOURCE_LABELS = { mixkit: "Mixkit", pexels: "Pexels", pixabay: "Pixabay" };
const HANDLED = ["footage.items", "footage.stock", "footage.sources", "footage.queries", "footage.picks", "footage.banned"];

const items = () => state.project.footage.items;

function kindOf(item) {
  if (item.kind && item.kind !== "auto") return item.kind;
  const up = uploadFor(item.path);
  if (up && (up.kind === "image" || up.kind === "video")) return up.kind;
  return /\.(jpe?g|png|webp|gif|heic|bmp|tiff?)$/i.test(item.path) ? "image" : "video";
}

/** Roughly how many shots the reel will have (for the "pin to shot" menu). */
function shotCount() {
  if (state.plan && state.plan.shots && state.plan.shots.length) return state.plan.shots.length;
  const parsed = parseScript(state.project.script);
  const total = lengthPlan(state.project, parsed).total;
  const vs = state.project.style.video;
  return clamp(Math.round(total / (((vs.min_shot || 3) + (vs.max_shot || 7)) / 2)), 3, 30);
}

function numberBox({ label, value, placeholder, step = 0.1, min = 0, max = 3600, onCommit, id }) {
  const input = h("input", { id, type: "text", inputmode: "decimal", class: "input input-num", value: value == null ? "" : String(value), placeholder: placeholder || "", autocomplete: "off" });
  input.addEventListener("change", () => {
    const t = input.value.trim();
    const v = t === "" ? null : Number(t.replace(",", "."));
    if (t !== "" && !isFinite(v)) {
      input.value = value == null ? "" : String(value);
      return;
    }
    value = v == null ? null : Math.min(max, Math.max(min, Math.round(v / step) * step));
    input.value = value == null ? "" : String(+value.toFixed(2));
    onCommit(value);
  });
  return h("div", { class: "field field-compact" }, h("label", { class: "field-label", for: id }, label),
    h("div", { class: "input-group" }, input, h("span", { class: "input-suffix" }, "s")));
}

function clipCard(item, index, ctx) {
  const up = uploadFor(item.path);
  const kind = kindOf(item);
  const isRef = item.role === "reference";
  const name = basename(item.path);
  const set = (key, value) => {
    item[key] = value;
    touched([`footage.items.${index}.${key}`]);
  };
  const idp = `clip-${index}`;

  const thumbSrc = up && up.thumb_url;
  const media = thumbSrc
    ? h("img", { src: thumbSrc, alt: "", loading: "lazy", draggable: "false" })
    : kind === "image"
      ? h("img", { src: fileUrl(item.path), alt: "", loading: "lazy", draggable: "false" })
      : h("video", { src: `${fileUrl(item.path)}#t=0.5`, muted: true, preload: "metadata", playsinline: true, "aria-hidden": "true" });
  const duration = up && up.info && up.info.duration;
  const dims = up && up.info && up.info.width ? `${up.info.width}×${up.info.height}` : "";
  const order = ctx.orderOf(item);

  const handle = h("button", { type: "button", class: "clip-handle", "aria-label": `Reorder ${name}: drag, or use the arrow buttons`, title: "Drag to reorder", html: icon("grip", { size: 18 }) });
  const mediaBox = h("div", { class: "clip-media" }, media,
    h("span", { class: "clip-kind badge badge-dark", html: `${icon(kind === "image" ? "image" : "film", { size: 12 })}${kind === "image" ? "Photo" : `Video${duration ? ` · ${formatTime(duration, false)}` : ""}`}` }),
    isRef ? h("span", { class: "clip-order is-ref", title: "Reference only" }, h("span", { html: icon("eye", { size: 14 }) })) : h("span", { class: "clip-order", title: `Shot order ${order}` }, String(order)),
    handle);

  const roleSeg = segmented({
    name: `${idp}-role`, label: `How to use ${name}`, value: item.role,
    options: [{ value: "footage", label: "Use in video" }, { value: "reference", label: "Reference only" }],
    onChange: (v) => {
      set("role", v);
      ctx.redraw();
    },
  });

  const details = h("div", { class: "clip-fields" });
  if (!isRef) {
    const shots = shotCount();
    const pinId = `${idp}-pin`;
    const pin = h("select", { id: pinId, class: "input select" },
      h("option", { value: "" }, "In order"),
      Array.from({ length: Math.max(shots, (item.shot ?? 0) + 1) }, (_, k) => h("option", { value: String(k), selected: item.shot === k }, k === 0 ? "Shot 1 · opening" : `Shot ${k + 1}`)));
    pin.addEventListener("change", () => set("shot", pin.value === "" ? null : Number(pin.value)));
    details.append(h("div", { class: "field field-compact" }, h("label", { class: "field-label", for: pinId }, h("span", { html: icon("pin", { size: 13 }) }), " Shot"), pin));
    if (kind === "video") {
      details.append(
        numberBox({ id: `${idp}-in`, label: "Trim start", value: item.trim_in || 0, onCommit: (v) => set("trim_in", v ?? 0) }),
        numberBox({ id: `${idp}-out`, label: "Trim end", value: item.trim_out, placeholder: duration ? formatTime(duration).replace(/^0:/, "") : "End", onCommit: (v) => set("trim_out", v) }));
    } else {
      const motionId = `${idp}-motion`;
      const motion = h("select", { id: motionId, class: "input select" },
        MOTIONS.map((m) => h("option", { value: m, selected: item.motion === m }, m === "auto" ? "Auto" : m.replace("-", " ").replace(/^./, (c) => c.toUpperCase()))));
      motion.addEventListener("change", () => set("motion", motion.value));
      details.append(
        numberBox({ id: `${idp}-secs`, label: "Seconds", min: 1, max: 30, value: item.seconds, placeholder: String(state.project.style.video.image_seconds ?? 4), onCommit: (v) => set("seconds", v) }),
        h("div", { class: "field field-compact" }, h("label", { class: "field-label", for: motionId }, "Motion"), motion));
    }
  }
  const noteId = `${idp}-note`;
  const note = h("input", { id: noteId, type: "text", class: "input", value: item.note || "", placeholder: isRef ? "What should stock clips look like?" : "Note (optional)", autocomplete: "off" });
  note.addEventListener("change", () => set("note", note.value));

  const actions = h("div", { class: "clip-actions" },
    button("Move earlier", { icon: "chevronLeft", variant: "ghost", size: "sm", iconOnly: true, disabled: index === 0, onClick: () => ctx.move(index, index - 1) }),
    button("Move later", { icon: "chevronRight", variant: "ghost", size: "sm", iconOnly: true, disabled: index === items().length - 1, onClick: () => ctx.move(index, index + 1) }),
    h("span", { class: "clip-meta muted small", title: name }, dims || name),
    button(`Delete ${name}`, { icon: "trash", variant: "ghost", size: "sm", iconOnly: true, class: "danger-hover", onClick: () => ctx.remove(item) }));

  const card = h("li", { class: ["clip", isRef && "is-ref"], dataset: { index: String(index) } },
    mediaBox,
    h("div", { class: "clip-body" },
      h("div", { class: "clip-name", title: name }, name),
      roleSeg,
      isRef ? h("p", { class: "muted small" }, "Not shown in the video. Steers the stock search and the colour match.") : null,
      details,
      h("div", { class: "field field-compact" }, h("label", { class: "visually-hidden", for: noteId }, "Note"), note)),
    actions);
  handle.addEventListener("pointerdown", (e) => ctx.startDrag(e, card, index));
  handle.addEventListener("keydown", (e) => {
    const delta = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 }[e.key];
    if (delta) {
      e.preventDefault();
      ctx.move(index, index + delta, true);
    }
  });
  return card;
}

function stockCard() {
  const fs = state.project.footage;
  const caps = state.meta.capabilities || {};
  const sourceField = findField(schemaSections(), "footage.sources");
  const options = (sourceField && sourceField.options && sourceField.options.length)
    ? sourceField.options
    : ["mixkit", "pexels", "pixabay"].map((v) => ({ value: v, label: SOURCE_LABELS[v] }));
  const chips = h("div", { class: "chips", role: "group", "aria-labelledby": "stock-sources-label" });
  for (const opt of options) {
    const id = `src-${opt.value}`;
    const missingKey = caps[opt.value] === false;
    const input = h("input", { type: "checkbox", id, class: "visually-hidden", checked: (fs.sources || []).includes(opt.value) });
    input.addEventListener("change", () => {
      const set = new Set(state.project.footage.sources || []);
      if (input.checked) set.add(opt.value);
      else set.delete(opt.value);
      setValue("footage.sources", options.map((o) => o.value).filter((v) => set.has(v)));
    });
    chips.append(input, h("label", { class: "chip", for: id, title: missingKey ? "Needs an API key in .env" : null },
      h("span", { class: "chip-check", html: icon("check", { size: 14 }) }), opt.label || SOURCE_LABELS[opt.value] || opt.value,
      missingKey ? h("span", { class: "chip-note" }, "no key") : null));
  }
  const queries = h("textarea", { id: "stock-queries", class: "input textarea", rows: 4, spellcheck: false, placeholder: "Leave empty to choose searches from the script\ne.g. mountains\nforest\nlake sunrise" },
    (fs.queries || []).join("\n"));
  queries.addEventListener("change", () => {
    const lines = queries.value.split("\n").map((s) => s.trim()).filter(Boolean);
    setValue("footage.queries", lines.length ? lines : null);
  });

  const stockToggle = h("input", { type: "checkbox", role: "switch", id: "stock-on", checked: fs.stock });
  const body = h("div", { class: "stack", hidden: !fs.stock });
  stockToggle.addEventListener("change", () => {
    setValue("footage.stock", stockToggle.checked);
    body.hidden = !stockToggle.checked;
  });
  const section = findSection(schemaSections(), "footage");
  const rest = section && renderFields(section.fields, projectBinding, { exclude: HANDLED, advancedLabel: "More stock settings" });
  body.append(
    h("div", { class: "field" }, h("span", { class: "field-label", id: "stock-sources-label" }, "Sources"), chips,
      caps.pexels === false || caps.pixabay === false ? h("p", { class: "field-help" }, "Pexels and Pixabay need a free API key in the .env file; Mixkit works without one.") : null),
    rest || "",
    h("div", { class: "field" }, h("label", { class: "field-label", for: "stock-queries" }, "Custom searches"),
      queries, h("p", { class: "field-help" }, "One search per line, used in shot order. Short searches (1–2 words) find more clips.")));

  return h("section", { class: "card", "aria-labelledby": "stock-title" },
    h("div", { class: "card-head card-head-row" },
      h("div", {}, h("h2", { class: "card-title", id: "stock-title" }, "Stock footage"),
        h("p", { class: "card-sub" }, "Free clips fill every shot your own footage doesn’t cover.")),
      h("label", { class: "switch", for: "stock-on" }, stockToggle, h("span", { class: "switch-track", "aria-hidden": "true" }), h("span", { class: "visually-hidden" }, "Use stock footage"))),
    body);
}

export function render(panel) {
  const grid = h("ol", { class: "clip-grid", "aria-label": "Your footage, in shot order" });
  const progress = h("ul", { class: "upload-list" });
  const summary = h("p", { class: "card-sub", role: "status" });
  const yours = h("section", { class: "card", "aria-labelledby": "yours-title" },
    h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "yours-title" }, "Your photos and clips"), summary),
    supports("upload")
      ? dropzone({
        accept: "image/*,video/*", multiple: true, title: "Drop photos and videos here, or browse",
        hint: "Several at once is fine. Photos get a slow Ken Burns move.", iconName: "footage",
        onFiles: async (files) => {
          const done = await uploadAll(files, "footage", progress);
          if (done.length) toast(`${done.length} file${done.length > 1 ? "s" : ""} added.`, { kind: "success" });
          draw();
        },
      })
      : callout("info", "This server does not accept uploads."),
    progress, grid);
  // the control panel adds a review of the stock clips (chosen before rendering)
  const ex = globalThis.reelforgeExtras;
  const review = ex && typeof ex.footageReview === "function" ? ex.footageReview() : null;
  panel.append(h("div", { class: "stack-lg" }, yours, stockCard(), review ? review.el : null));

  const ctx = {
    orderOf: (item) => items().filter((i) => i.role !== "reference").indexOf(item) + 1,
    redraw: () => draw(),
    move(from, to, keepFocus = false) {
      const list = items();
      if (to < 0 || to >= list.length || from === to) return;
      const [it] = list.splice(from, 1);
      list.splice(to, 0, it);
      touched(["footage.items"]);
      draw();
      if (keepFocus) {
        const h2 = grid.querySelector(`[data-index="${to}"] .clip-handle`);
        if (h2) h2.focus();
      }
    },
    async remove(item) {
      try {
        await removeUpload(item.path);
        draw();
      } catch (err) {
        toastError("Could not delete", err);
      }
    },
    startDrag,
  };

  /** Pointer-based reordering (works with mouse, pen and touch). */
  function startDrag(e, card, from) {
    if (e.button !== 0) return;
    e.preventDefault();
    const handle = e.currentTarget;
    handle.setPointerCapture(e.pointerId);
    const rect = card.getBoundingClientRect();
    const ghost = card.cloneNode(true);
    ghost.classList.add("clip-ghost");
    ghost.setAttribute("aria-hidden", "true");
    ghost.querySelectorAll("[id]").forEach((n) => n.removeAttribute("id"));
    Object.assign(ghost.style, { width: `${rect.width}px`, height: `${rect.height}px`, left: `${rect.left}px`, top: `${rect.top}px` });
    document.body.append(ghost);
    card.classList.add("is-placeholder");
    const dx = e.clientX - rect.left;
    const dy = e.clientY - rect.top;
    let to = from;
    const single = getComputedStyle(grid).gridTemplateColumns.split(" ").length === 1;
    const move = (ev) => {
      ghost.style.left = `${ev.clientX - dx}px`;
      ghost.style.top = `${ev.clientY - dy}px`;
      // Insert next to the nearest card, before or after it depending on the pointer side.
      const cards = [...grid.children].filter((c) => c !== card);
      let best = null;
      cards.forEach((c, i) => {
        const r = c.getBoundingClientRect();
        const d = Math.hypot(ev.clientX - (r.left + r.width / 2), (ev.clientY - (r.top + r.height / 2)) * 1.5);
        if (!best || d < best.d) best = { i, r, d };
      });
      let target = cards.length;
      if (best) {
        const after = single ? ev.clientY > best.r.top + best.r.height / 2 : ev.clientX > best.r.left + best.r.width / 2;
        target = best.i + (after ? 1 : 0);
      }
      if (target !== to) {
        to = target;
        grid.insertBefore(card, cards[target] || null);
      }
      const edge = 60;
      if (ev.clientY < edge) window.scrollBy(0, -12);
      else if (ev.clientY > window.innerHeight - edge) window.scrollBy(0, 12);
    };
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
      ghost.remove();
      card.classList.remove("is-placeholder");
      if (to !== from) ctx.move(from, to);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  }

  function draw() {
    clear(grid);
    const list = items();
    const own = list.filter((i) => i.role !== "reference").length;
    const refs = list.length - own;
    summary.textContent = list.length
      ? `${own} in the video${refs ? ` · ${refs} reference only` : ""}. Drag cards to change the order.`
      : "Optional. Without your own footage every shot comes from stock.";
    list.forEach((item, i) => grid.append(clipCard(item, i, ctx)));
    grid.hidden = !list.length;
  }

  draw();
  const off = on("uploads", draw);
  return () => {
    off();
    if (review) review.cleanup();
  };
}

