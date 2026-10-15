/**
 * Settings forms generated from field metadata.
 *
 * The server describes every setting in `/api/schema` (sections of fields
 * with path, label, help, type, min/max/step, options, unit, advanced), so a
 * new setting shows up in the UI without touching this code. When the
 * backend has no schema, `inferSchema` derives a plain one from the project
 * dict itself, so everything stays editable.
 *
 * Widgets read and write through a binding `{get(path), set(path, value)}`.
 */

import { clamp, h, humanize, uid } from "./dom.js";
import { icon } from "./icons.js";

/** Where each schema section lives in the project dict. */
export const SECTION_PREFIX = {
  caption: "style.caption",
  hook: "style.hook",
  video: "style.video",
  look: "style.look",
  music: "music",
  duration: "duration",
  footage: "footage",
  voice: "voice",
};

const SECTION_LABELS = {
  caption: "Captions",
  hook: "Opening line (hook)",
  video: "Video",
  look: "Colour look",
  music: "Music",
  duration: "Length",
  footage: "Footage",
  voice: "Voice",
};

// --------------------------------------------------------------------------- schema normalisation

/** Options may arrive as strings, [value, label] pairs or {value|id, label|name}. */
export function normalizeOptions(options) {
  if (!Array.isArray(options)) return null;
  return options.map((o) => {
    if (Array.isArray(o)) return { value: o[0], label: String(o[1] ?? o[0]) };
    if (o && typeof o === "object") {
      const value = o.value !== undefined ? o.value : o.id;
      return { value, label: String(o.label ?? o.name ?? humanize(value)), help: o.help || o.description || "" };
    }
    return { value: o, label: String(o) };
  });
}

function sectionIdFromPath(path) {
  const parts = String(path || "").split(".");
  return parts[0] === "style" ? parts[1] : parts[0];
}

function normalizeField(f, sectionId) {
  let path = f.path || f.key || f.name;
  if (path && !String(path).includes(".") && SECTION_PREFIX[sectionId]) path = `${SECTION_PREFIX[sectionId]}.${path}`;
  const last = String(path).split(".").filter((p) => !/^\d+$/.test(p)).pop();
  return {
    path,
    label: f.label || f.title || humanize(last),
    help: f.help || f.description || "",
    type: f.type || (f.options ? "select" : "text"),
    min: f.min ?? null,
    max: f.max ?? null,
    step: f.step ?? null,
    options: normalizeOptions(f.options || f.choices),
    unit: f.unit || "",
    advanced: Boolean(f.advanced),
    placeholder: f.placeholder || "",
  };
}

function normalizeSection(s, fallbackId) {
  const fields = s.fields || s.items || [];
  const id = s.id || s.key || s.name || s.section || fallbackId || sectionIdFromPath(fields[0] && fields[0].path);
  return {
    id,
    label: s.label || s.title || SECTION_LABELS[id] || humanize(id),
    help: s.help || s.description || "",
    fields: fields.filter((f) => f && (f.path || f.key || f.name)).map((f) => normalizeField(f, id)),
  };
}

/**
 * Accepts `{sections: [...]}`, a list of sections, a dict of
 * section id -> fields (or section object), or a flat list of fields.
 */
export function normalizeSchema(raw) {
  if (!raw) return [];
  if (!Array.isArray(raw) && Array.isArray(raw.sections)) return normalizeSchema(raw.sections);
  let sections = [];
  if (Array.isArray(raw)) {
    if (raw.every((x) => x && typeof x === "object" && !Array.isArray(x) && (x.fields || x.items))) {
      sections = raw.map((s) => normalizeSection(s));
    } else if (raw.every((x) => Array.isArray(x))) {
      sections = raw.map((fields) => normalizeSection({ fields }));
    } else {
      const groups = new Map();
      for (const f of raw.filter((x) => x && x.path)) {
        const id = f.section || sectionIdFromPath(f.path);
        if (!groups.has(id)) groups.set(id, []);
        groups.get(id).push(f);
      }
      sections = [...groups].map(([id, fields]) => normalizeSection({ id, fields }));
    }
  } else if (typeof raw === "object") {
    sections = Object.entries(raw).map(([id, v]) =>
      normalizeSection(Array.isArray(v) ? { id, fields: v } : { id, ...v }, id));
  }
  return sections.filter((s) => s.fields.length);
}

/** Fallback schema derived from the project's own values (no labels/help). */
export function inferSchema(project) {
  const sections = [];
  for (const [id, prefix] of Object.entries(SECTION_PREFIX)) {
    const obj = prefix.split(".").reduce((o, k) => (o ? o[k] : undefined), project);
    if (!obj || typeof obj !== "object") continue;
    const fields = [];
    for (const [key, value] of Object.entries(obj)) {
      if (key === "items") continue;
      const path = `${prefix}.${key}`;
      let type = "text";
      if (typeof value === "boolean") type = "bool";
      else if (typeof value === "number") type = "number";
      else if (Array.isArray(value) && value.length === 3 && /colou?r/.test(key)) type = "color";
      else if (Array.isArray(value) && value.every((x) => typeof x === "number")) type = "tuple";
      else if (Array.isArray(value)) type = "lines";
      else if (value === null) type = "nullable";
      fields.push(normalizeField({ path, type, step: type === "number" ? "any" : null }, id));
    }
    sections.push({ id, label: SECTION_LABELS[id], help: "", fields });
  }
  return sections;
}

export const findSection = (sections, id) => sections.find((s) => s.id === id) || null;

export function findField(sections, path) {
  for (const s of sections) {
    const f = s.fields.find((x) => x.path === path);
    if (f) return f;
  }
  return null;
}

// --------------------------------------------------------------------------- value helpers

const toHex = (rgb) => "#" + rgb.slice(0, 3).map((c) => clamp(Math.round(c), 0, 255).toString(16).padStart(2, "0")).join("");

/** Any colour value (RGB list, "#rgb", "#rrggbb") -> "#rrggbb" or null. */
export function colorToHex(value) {
  if (Array.isArray(value)) return toHex(value);
  if (typeof value === "string" && /^#?[0-9a-f]{3}([0-9a-f]{3})?$/i.test(value)) {
    let x = value.replace("#", "");
    if (x.length === 3) x = [...x].map((c) => c + c).join("");
    return `#${x.toLowerCase()}`;
  }
  return null;
}

export const hexToRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

function decimals(step) {
  const s = String(step ?? "");
  return s.includes(".") ? s.split(".")[1].length : 0;
}

function formatNumber(value, field) {
  if (value === null || value === undefined || value === "") return "";
  const d = field.type === "int" ? 0 : Math.max(decimals(field.step), 0);
  return field.step && field.step !== "any" ? Number(value).toFixed(d) : String(Number(value));
}

function parseNumber(text, field) {
  if (text === "" || text === null) return null;
  let n = Number(String(text).replace(",", "."));
  if (!isFinite(n)) return undefined;
  if (field.type === "int") n = Math.round(n);
  if (field.min !== null && field.min !== undefined) n = Math.max(field.min, n);
  if (field.max !== null && field.max !== undefined) n = Math.min(field.max, n);
  return n;
}

// --------------------------------------------------------------------------- widgets

/** iOS-style switch around a native checkbox (keyboard + screen readers work as usual). */
export function switchControl({ id, checked, label, onChange, describedBy }) {
  const input = h("input", { type: "checkbox", role: "switch", id, checked: Boolean(checked), "aria-describedby": describedBy });
  input.addEventListener("change", () => onChange(input.checked));
  return h("label", { class: "switch", for: id }, input, h("span", { class: "switch-track", "aria-hidden": "true" }), label ? h("span", { class: "switch-label" }, label) : null);
}

/**
 * Segmented control: a radio group styled as connected buttons.
 * Options: [{value, label}]; values may be any JSON value.
 */
export function segmented({ name = uid("seg"), options, value, onChange, label, labelledBy, size }) {
  const group = h("div", { class: ["segmented", size && `segmented-${size}`], role: "radiogroup", "aria-label": labelledBy ? null : label, "aria-labelledby": labelledBy });
  options.forEach((opt, i) => {
    const id = `${name}-${i}`;
    const input = h("input", { type: "radio", name, id, class: "visually-hidden", checked: JSON.stringify(opt.value) === JSON.stringify(value) });
    input.addEventListener("change", () => input.checked && onChange(opt.value));
    group.append(input, h("label", { for: id, title: opt.help || null }, opt.icon ? h("span", { class: "seg-icon", html: icon(opt.icon, { size: 16 }) }) : null, opt.label));
  });
  return group;
}

function fieldShell(field, id, control, { inline = false, extraHead = null } = {}) {
  const helpId = field.help ? `${id}-help` : null;
  const wrap = h("div", { class: ["field", inline && "field-inline", `field-${field.type}`], dataset: { path: field.path } });
  wrap.append(h("div", { class: "field-head" }, h("label", { class: "field-label", for: id }, field.label), extraHead));
  wrap.append(control);
  if (helpId) wrap.append(h("p", { class: "field-help", id: helpId }, field.help));
  return { wrap, helpId };
}

function numberInput(field, id, value, onCommit, { placeholder = "", describedBy = null, ariaLabel = null } = {}) {
  const input = h("input", {
    type: "text", inputmode: field.type === "int" ? "numeric" : "decimal", id, class: "input input-num",
    value: formatNumber(value, field), placeholder, autocomplete: "off", "aria-describedby": describedBy, "aria-label": ariaLabel,
  });
  const commit = () => {
    const n = parseNumber(input.value, field);
    if (n === undefined) {
      input.value = formatNumber(value, field);
      return;
    }
    value = n;
    input.value = formatNumber(n, field);
    onCommit(n);
  };
  input.addEventListener("change", commit);
  input.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
    e.preventDefault();
    const step = Number(field.step) || (field.type === "int" ? 1 : 0.1);
    const cur = Number(input.value) || 0;
    input.value = formatNumber(cur + (e.key === "ArrowUp" ? step : -step) * (e.shiftKey ? 10 : 1), field);
    commit();
  });
  input.setValue = (v) => {
    value = v;
    input.value = formatNumber(v, field);
  };
  return input;
}

function withUnit(input, unit) {
  return unit ? h("div", { class: "input-group" }, input, h("span", { class: "input-suffix" }, unit)) : input;
}

function numberField(field, bind) {
  const id = uid("f");
  const value = bind.get(field.path);
  const optionalNumber = field.type === "optional-number" || field.type === "nullable";
  const hasRange = isFinite(field.min) && isFinite(field.max) && field.min !== null && field.max !== null && !optionalNumber;
  if (!hasRange) {
    const helpId = field.help ? `${id}-help` : null;
    const input = numberInput(field, id, value, (n) => bind.set(field.path, n), {
      placeholder: optionalNumber ? field.placeholder || "Auto" : "", describedBy: helpId,
    });
    return fieldShell(field, id, withUnit(input, field.unit)).wrap;
  }
  const helpId = field.help ? `${id}-help` : null;
  const range = h("input", {
    type: "range", id, class: "range", min: field.min, max: field.max, step: field.step || (field.type === "int" ? 1 : "any"),
    value: value ?? field.min, "aria-describedby": helpId,
  });
  const num = numberInput(field, null, value, (n) => {
    range.value = n;
    paintRange(range);
    bind.set(field.path, n);
  }, { ariaLabel: `${field.label} value` });
  range.addEventListener("input", () => {
    const n = parseNumber(range.value, field);
    num.setValue(n);
    paintRange(range);
    bind.set(field.path, n);
  });
  paintRange(range);
  const head = h("span", { class: "field-value" }, num, field.unit ? h("span", { class: "unit" }, field.unit) : null);
  return fieldShell(field, id, range, { extraHead: head }).wrap;
}

/** Fill the slider track up to the thumb (CSS reads --fill). */
export function paintRange(range) {
  const min = Number(range.min) || 0;
  const max = Number(range.max) || 1;
  const pct = ((Number(range.value) - min) / (max - min || 1)) * 100;
  range.style.setProperty("--fill", `${clamp(pct, 0, 100)}%`);
}

function boolField(field, bind) {
  const id = uid("f");
  const helpId = field.help ? `${id}-help` : null;
  const wrap = h("div", { class: "field field-bool", dataset: { path: field.path } },
    switchControl({ id, checked: bind.get(field.path), label: field.label, describedBy: helpId, onChange: (v) => bind.set(field.path, v) }));
  if (helpId) wrap.append(h("p", { class: "field-help", id: helpId }, field.help));
  return wrap;
}

function selectField(field, bind) {
  const id = uid("f");
  const value = bind.get(field.path);
  const options = [...(field.options || [])];
  if (!options.some((o) => JSON.stringify(o.value) === JSON.stringify(value)) && value !== undefined) {
    options.push({ value, label: value === null ? "None" : String(value) });
  }
  const short = options.length <= 3 && options.every((o) => o.label.length <= 12);
  if (short) {
    const labelId = `${id}-label`;
    const helpId = field.help ? `${id}-help` : null;
    const wrap = h("div", { class: "field field-select", dataset: { path: field.path } },
      h("div", { class: "field-head" }, h("span", { class: "field-label", id: labelId }, field.label)),
      segmented({ name: id, options, value, labelledBy: labelId, onChange: (v) => bind.set(field.path, v) }));
    if (helpId) wrap.append(h("p", { class: "field-help", id: helpId }, field.help));
    return wrap;
  }
  const select = h("select", { id, class: "input select", "aria-describedby": field.help ? `${id}-help` : null },
    options.map((o, i) => h("option", { value: String(i), selected: JSON.stringify(o.value) === JSON.stringify(value) }, o.label)));
  select.addEventListener("change", () => bind.set(field.path, options[Number(select.value)].value));
  return fieldShell(field, id, select).wrap;
}

function colorField(field, bind) {
  const id = uid("f");
  const value = bind.get(field.path);
  const autoOption = (field.options || []).find((o) => typeof o.value === "string" && !colorToHex(o.value));
  const isAuto = autoOption && value === autoOption.value;
  const wasArray = Array.isArray(value);
  let hex = colorToHex(value) || "#ffffff";
  const write = (hx) => bind.set(field.path, wasArray || (value == null && !autoOption) ? hexToRgb(hx) : hx);

  const picker = h("input", { type: "color", id, class: "color-swatch", value: hex, "aria-label": `${field.label} colour` });
  const text = h("input", { type: "text", class: "input input-hex", value: hex.toUpperCase(), maxlength: 7, spellcheck: false, "aria-label": `${field.label} hex value` });
  picker.addEventListener("input", () => {
    hex = picker.value;
    text.value = hex.toUpperCase();
    write(hex);
  });
  text.addEventListener("change", () => {
    const hx = colorToHex(text.value.trim());
    if (!hx) {
      text.value = hex.toUpperCase();
      return;
    }
    hex = hx;
    picker.value = hx;
    text.value = hx.toUpperCase();
    write(hx);
  });
  const row = h("div", { class: "color-row" }, picker, text);
  if (autoOption) {
    const setAuto = (auto) => {
      row.classList.toggle("is-disabled", auto);
      picker.disabled = text.disabled = auto;
      bind.set(field.path, auto ? autoOption.value : hex);
    };
    const seg = segmented({
      options: [{ value: true, label: autoOption.label || "Auto" }, { value: false, label: "Custom" }],
      value: Boolean(isAuto), label: `${field.label} mode`, size: "sm", onChange: setAuto,
    });
    row.classList.toggle("is-disabled", Boolean(isAuto));
    picker.disabled = text.disabled = Boolean(isAuto);
    return fieldShell(field, id, h("div", { class: "color-auto" }, seg, row)).wrap;
  }
  return fieldShell(field, id, row).wrap;
}

function textField(field, bind) {
  const id = uid("f");
  const value = bind.get(field.path);
  const input = h("input", {
    type: "text", id, class: "input", value: value ?? "", placeholder: field.placeholder || (value === null ? "None" : ""),
    autocomplete: "off", spellcheck: false, "aria-describedby": field.help ? `${id}-help` : null,
  });
  const wasNull = value === null || value === undefined;
  input.addEventListener("change", () => {
    const v = input.value.trim();
    bind.set(field.path, v === "" && (wasNull || field.type === "nullable") ? null : v);
  });
  return fieldShell(field, id, withUnit(input, field.unit)).wrap;
}

function multiField(field, bind) {
  const id = uid("f");
  const current = new Set(bind.get(field.path) || []);
  const labelId = `${id}-label`;
  const group = h("div", { class: "chips", role: "group", "aria-labelledby": labelId });
  for (const opt of field.options || []) {
    const cid = uid("chip");
    const input = h("input", { type: "checkbox", id: cid, class: "visually-hidden", checked: current.has(opt.value) });
    input.addEventListener("change", () => {
      if (input.checked) current.add(opt.value);
      else current.delete(opt.value);
      bind.set(field.path, (field.options || []).map((o) => o.value).filter((v) => current.has(v)));
    });
    group.append(input, h("label", { class: "chip", for: cid }, h("span", { class: "chip-check", html: icon("check", { size: 14 }) }), opt.label));
  }
  const wrap = h("div", { class: "field field-multi", dataset: { path: field.path } },
    h("div", { class: "field-head" }, h("span", { class: "field-label", id: labelId }, field.label)), group);
  if (field.help) wrap.append(h("p", { class: "field-help" }, field.help));
  return wrap;
}

function linesField(field, bind) {
  const id = uid("f");
  const value = bind.get(field.path);
  const ta = h("textarea", {
    id, class: "input textarea", rows: 4, spellcheck: false, placeholder: field.placeholder || "One per line",
    "aria-describedby": field.help ? `${id}-help` : null,
  }, Array.isArray(value) ? value.join("\n") : value ?? "");
  ta.addEventListener("change", () => {
    const lines = ta.value.split("\n").map((s) => s.trim()).filter(Boolean);
    bind.set(field.path, lines.length ? lines : null);
  });
  return fieldShell(field, id, ta).wrap;
}

function tupleField(field, bind) {
  const id = uid("f");
  const value = [...(bind.get(field.path) || [])];
  const inputs = value.map((v, i) => numberInput({ ...field, type: "number" }, i === 0 ? id : null, v, (n) => {
    value[i] = n ?? 0;
    bind.set(field.path, [...value]);
  }, { ariaLabel: `${field.label} ${i + 1}` }));
  return fieldShell(field, id, h("div", { class: "tuple" }, inputs)).wrap;
}

function jsonField(field, bind) {
  const id = uid("f");
  const input = h("input", { type: "text", id, class: "input mono", value: JSON.stringify(bind.get(field.path)) });
  input.addEventListener("change", () => {
    try {
      bind.set(field.path, JSON.parse(input.value));
      input.setCustomValidity("");
    } catch {
      input.setCustomValidity("Not valid JSON");
      input.reportValidity();
    }
  });
  return fieldShell(field, id, input).wrap;
}

/** The widget for one field. */
export function renderField(field, bind) {
  const value = bind.get(field.path);
  switch (field.type) {
    case "bool":
    case "boolean":
      return boolField(field, bind);
    case "int":
    case "integer":
    case "number":
    case "float":
    case "optional-number":
      return numberField({ ...field, type: field.type === "integer" ? "int" : field.type }, bind);
    case "select":
    case "enum":
      return selectField(field, bind);
    case "color":
    case "colour":
      return colorField(field, bind);
    case "multiselect":
    case "list":
    case "lines":
    case "text-list":
      return field.options && field.options.length ? multiField(field, bind) : linesField(field, bind);
    case "tuple":
      return Array.isArray(value) ? tupleField(field, bind) : jsonField(field, bind);
    case "nullable":
      return typeof value === "number" ? numberField(field, bind) : textField(field, bind);
    case "text":
    case "string":
    case "optional-text":
      return textField(field, bind);
    default:
      if (field.options) return selectField(field, bind);
      if (typeof value === "number") return numberField({ ...field, type: "number" }, bind);
      if (typeof value === "boolean") return boolField(field, bind);
      if (value !== null && typeof value === "object") return jsonField(field, bind);
      return textField(field, bind);
  }
}

/**
 * A form for a list of fields: the basic ones in a grid, advanced ones
 * behind a disclosure. `exclude` lists paths rendered elsewhere.
 */
export function renderFields(fields, bind, { exclude = [], advancedLabel = "Advanced" } = {}) {
  const skip = new Set(exclude);
  const shown = fields.filter((f) => !skip.has(f.path) && !skip.has(f.path.replace(/\.\d+$/, "")));
  const basic = shown.filter((f) => !f.advanced);
  const advanced = shown.filter((f) => f.advanced);
  const frag = h("div", { class: "form" });
  if (basic.length) frag.append(h("div", { class: "form-grid" }, basic.map((f) => renderField(f, bind))));
  if (advanced.length) {
    const details = h("details", { class: "advanced" },
      h("summary", {}, h("span", { html: icon("chevronRight", { size: 16 }) }), `${advancedLabel}`, h("span", { class: "count" }, String(advanced.length))),
      h("div", { class: "form-grid" }, advanced.map((f) => renderField(f, bind))));
    frag.append(details);
  }
  return shown.length ? frag : null;
}
