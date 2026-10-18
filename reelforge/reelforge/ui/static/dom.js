/** Small DOM, formatting and object-path helpers shared by every module. */

import { icon } from "./icons.js";

/**
 * Create an element. `attrs` keys: `class`, `style` (object or string),
 * `dataset`, `html` (trusted markup only), `on<Event>` handlers, and any
 * attribute (`true` sets a boolean attribute, `false`/`null` omits it).
 * Children may be strings, nodes, arrays or falsy (skipped).
 */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "value") continue; // set as a property once options/children exist
    if (key === "class") el.className = Array.isArray(value) ? value.filter(Boolean).join(" ") : value;
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key === "html") el.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key in el && typeof value !== "string" && key !== "list") el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  append(el, children);
  if (attrs && attrs.value !== undefined && attrs.value !== null) el.value = attrs.value;
  return el;
}

function append(el, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(el, child);
    else el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/** An element whose content is an inline icon (markup from icons.js). */
export function iconEl(name, opts) {
  const span = document.createElement("span");
  span.className = "icon-wrap";
  span.innerHTML = icon(name, opts);
  return span;
}

/** A button with an optional leading icon. `variant`: primary | secondary | ghost | danger. */
export function button(label, { icon: iconName, variant = "secondary", size, onClick, title, type = "button", iconOnly = false, class: extra, ...rest } = {}) {
  const b = h("button", {
    type,
    class: ["btn", `btn-${variant}`, size && `btn-${size}`, iconOnly && "btn-icon", extra],
    title: title || (iconOnly ? label : null),
    "aria-label": iconOnly ? label : null,
    onclick: onClick,
    ...rest,
  });
  if (iconName) b.insertAdjacentHTML("beforeend", icon(iconName, { size: size === "sm" ? 16 : 18 }));
  if (!iconOnly) b.append(h("span", { class: "btn-label" }, label));
  return b;
}

export function clear(el) {
  while (el.firstChild) el.firstChild.remove();
  return el;
}

let idCounter = 0;
export const uid = (prefix = "id") => `${prefix}-${++idCounter}`;

export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export function debounce(fn, ms) {
  let timer = null;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}

// --------------------------------------------------------------------------- object paths

/** Read "a.b.0.c" from a nested object/array. */
export function getPath(obj, path) {
  return String(path).split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

/** Write "a.b.0.c", creating containers as needed. Returns the object. */
export function setPath(obj, path, value) {
  const keys = String(path).split(".");
  let o = obj;
  keys.slice(0, -1).forEach((k, i) => {
    if (o[k] === null || typeof o[k] !== "object") o[k] = /^\d+$/.test(keys[i + 1]) ? [] : {};
    o = o[k];
  });
  o[keys[keys.length - 1]] = value;
  return obj;
}

export const clone = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));

export function deepEqual(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

// --------------------------------------------------------------------------- formatting

/** 75.4 -> "1:15.4" (tenths only when `precise`). */
export function formatTime(seconds, precise = true) {
  if (seconds == null || !isFinite(seconds)) return "–";
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  const sec = precise ? rest.toFixed(1).padStart(4, "0") : String(Math.floor(rest)).padStart(2, "0");
  return `${m}:${sec}`;
}

/** 24.6 -> "25 s", 95 -> "1 min 35 s". */
export function formatSeconds(seconds) {
  if (seconds == null || !isFinite(seconds)) return "–";
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  return s % 60 ? `${m} min ${s % 60} s` : `${m} min`;
}

export function formatBytes(n) {
  if (!n && n !== 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toFixed(n < 10 && i ? 1 : 0)} ${units[i]}`;
}

/** Accepts epoch seconds, epoch ms or an ISO string. */
export function toDate(value) {
  if (value == null || value === "") return null;
  if (typeof value === "number") return new Date(value < 1e12 ? value * 1000 : value);
  const d = new Date(value);
  return isNaN(d) ? null : d;
}

export function relativeTime(value) {
  const d = toDate(value);
  if (!d) return "";
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 45) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  if (diff < 7 * 86400) return `${Math.round(diff / 86400)} d ago`;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/** Last path segment: "uploads/a b.mp3" -> "a b.mp3". */
export const basename = (p) => String(p || "").split(/[\\/]/).pop();

/** Humanise an identifier: "max_clip_seconds" -> "Max clip seconds". */
export function humanize(id) {
  const s = String(id).replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// --------------------------------------------------------------------------- feedback

let toastRegion = null;

/**
 * Non-blocking notification. `kind`: info | success | error.
 * `action` ({label, onClick}) adds a button, e.g. "Undo".
 */
export function toast(message, { kind = "info", timeout, action } = {}) {
  if (!toastRegion) {
    toastRegion = h("div", { class: "toasts", role: "region", "aria-label": "Notifications" });
    document.body.append(toastRegion);
  }
  const iconName = kind === "error" ? "alert" : kind === "success" ? "check" : "info";
  const close = h("button", { class: "toast-close", type: "button", "aria-label": "Dismiss", html: icon("x", { size: 16 }) });
  const el = h("div", { class: `toast toast-${kind}`, role: kind === "error" ? "alert" : "status" },
    h("span", { class: "toast-icon", html: icon(iconName, { size: 18 }) }),
    h("div", { class: "toast-text" }, message),
    action ? h("button", { type: "button", class: "toast-action", onclick: () => {
      remove();
      action.onClick();
    } }, action.label) : null,
    close);
  const remove = () => {
    el.classList.add("leaving");
    setTimeout(() => el.remove(), 200);
  };
  close.addEventListener("click", remove);
  toastRegion.append(el);
  setTimeout(remove, timeout ?? (kind === "error" ? 8000 : action ? 7000 : 3500));
  return remove;
}

/** Show `err` (an ApiError or anything) as an error toast, prefixed with what failed. */
export function toastError(what, err) {
  if (err && err.name === "AbortError") return;
  const msg = err && err.message ? err.message : String(err);
  toast(what ? `${what}: ${msg}` : msg, { kind: "error" });
}

/**
 * Modal dialog built on <dialog> (focus trapping and Esc come for free).
 * `body` is a node; `actions` a list of {label, value, variant}. Resolves
 * with the chosen action's value (or null when dismissed).
 */
export function dialog({ title, body, actions, initialFocus }) {
  return new Promise((resolve) => {
    const titleId = uid("dlg-title");
    const dlg = h("dialog", { class: "dialog", "aria-labelledby": titleId });
    const form = h("form", { method: "dialog", class: "dialog-form" });
    form.append(h("h2", { id: titleId, class: "dialog-title" }, title), body || "");
    const row = h("div", { class: "dialog-actions" });
    for (const a of actions) {
      const b = button(a.label, { variant: a.variant || "secondary", type: a.submit ? "submit" : "button" });
      b.value = a.value ?? "";
      if (!a.submit) b.addEventListener("click", () => close(a.value ?? null));
      row.append(b);
    }
    form.append(row);
    dlg.append(form);
    let result = null;
    const close = (value) => {
      result = value;
      dlg.close();
    };
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const submit = actions.find((a) => a.submit);
      close(submit ? (typeof submit.value === "function" ? submit.value(form) : submit.value) : null);
    });
    dlg.addEventListener("close", () => {
      dlg.remove();
      resolve(result);
    });
    document.body.append(dlg);
    dlg.showModal();
    const focus = initialFocus ? dlg.querySelector(initialFocus) : null;
    if (focus) focus.focus();
  });
}

export async function confirmDialog({ title, message, confirmLabel = "OK", danger = false }) {
  const value = await dialog({
    title,
    body: h("p", { class: "dialog-text" }, message),
    actions: [
      { label: "Cancel", value: false, variant: "ghost" },
      { label: confirmLabel, value: true, variant: danger ? "danger" : "primary", submit: true },
    ],
  });
  return value === true;
}

/** Copy text to the clipboard (with a fallback for non-secure origins). */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = h("textarea", { style: { position: "fixed", opacity: "0" } }, text);
    document.body.append(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

/** Respect the OS "reduce motion" setting for decorative animation. */
export const prefersReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * A button that opens a small menu. `items`: [{label, icon, onSelect, danger}].
 * Keyboard: Enter/Space/ArrowDown open, arrows move, Esc closes.
 */
export function menuButton({ label, items, iconName = "more", variant = "ghost", size }) {
  const menuId = uid("menu");
  const trigger = button(label, { icon: iconName, variant, size, iconOnly: true, "aria-haspopup": "menu", "aria-expanded": "false", "aria-controls": menuId });
  const menu = h("div", { class: "menu", role: "menu", id: menuId, hidden: true });
  const wrap = h("div", { class: "menu-wrap" }, trigger, menu);
  const entries = () => [...menu.querySelectorAll("[role=menuitem]")];

  const close = (focusTrigger = false) => {
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", outside, true);
    if (focusTrigger) trigger.focus();
  };
  const outside = (e) => {
    if (!wrap.contains(e.target)) close();
  };
  const open = () => {
    clear(menu);
    for (const item of items().filter(Boolean)) {
      const b = h("button", { type: "button", role: "menuitem", class: ["menu-item", item.danger && "danger"], tabindex: "-1" });
      if (item.icon) b.insertAdjacentHTML("beforeend", icon(item.icon, { size: 16 }));
      b.append(h("span", {}, item.label));
      b.addEventListener("click", () => {
        close(true);
        item.onSelect();
      });
      menu.append(b);
    }
    menu.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", outside, true);
    const first = entries()[0];
    if (first) first.focus();
  };
  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    e.preventDefault();
    if (menu.hidden) open();
    else close();
  });
  menu.addEventListener("keydown", (e) => {
    const list = entries();
    const i = list.indexOf(document.activeElement);
    if (e.key === "Escape") {
      e.preventDefault();
      close(true);
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = list[(i + (e.key === "ArrowDown" ? 1 : -1) + list.length) % list.length];
      if (next) next.focus();
    } else if (e.key === "Tab") {
      close();
    }
  });
  return wrap;
}
