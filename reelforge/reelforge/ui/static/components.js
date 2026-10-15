/** Reusable pieces: file drop zones with upload progress, callouts, field lookup. */

import { isUnsupported } from "./api.js";
import { button, formatBytes, h, toastError, uid } from "./dom.js";
import { findField, renderField } from "./fields.js";
import { icon } from "./icons.js";
import { markUnsupported, projectBinding, schemaSections, supports, uploadFile } from "./store.js";

/** A coloured note. kind: info | warn | error | success. */
export function callout(kind, ...children) {
  const iconName = kind === "warn" || kind === "error" ? "alert" : kind === "success" ? "check" : "info";
  return h("div", { class: `callout callout-${kind}`, role: kind === "error" ? "alert" : null },
    h("span", { class: "callout-icon", html: icon(iconName, { size: 18 }) }),
    h("div", { class: "callout-body" }, children));
}

/** The schema field for `path` rendered against the project, or null if unknown. */
export function fieldFor(path, overrides = {}) {
  const field = findField(schemaSections(), path);
  return field ? renderField({ ...field, ...overrides }, projectBinding) : null;
}

/**
 * Drop zone for files: click/Enter to browse, or drag files onto it.
 * `onFiles(files)` receives an array of File objects.
 */
export function dropzone({ accept, multiple = false, title, hint, iconName = "upload", onFiles, compact = false }) {
  const inputId = uid("file");
  const input = h("input", { type: "file", id: inputId, accept, multiple, class: "visually-hidden" });
  const zone = h("label", { class: ["dropzone", compact && "dropzone-compact"], for: inputId },
    h("span", { class: "dropzone-icon", html: icon(iconName, { size: compact ? 20 : 26 }) }),
    h("span", { class: "dropzone-text" },
      h("span", { class: "dropzone-title" }, title),
      hint ? h("span", { class: "dropzone-hint" }, hint) : null));
  input.addEventListener("change", () => {
    const files = [...input.files];
    input.value = "";
    if (files.length) onFiles(files);
  });
  let depth = 0;
  zone.addEventListener("dragenter", (e) => {
    if (![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    depth++;
    zone.classList.add("is-over");
  });
  zone.addEventListener("dragover", (e) => {
    if ([...e.dataTransfer.types].includes("Files")) e.preventDefault();
  });
  zone.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (!depth) zone.classList.remove("is-over");
  });
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    depth = 0;
    zone.classList.remove("is-over");
    const files = [...e.dataTransfer.files];
    if (files.length) onFiles(multiple ? files : files.slice(0, 1));
  });
  return h("div", { class: "dropzone-wrap" }, input, zone);
}

/**
 * Upload files one after another, showing a progress row for each inside
 * `list`. Resolves with the server's file infos of the successful uploads.
 */
export async function uploadAll(files, role, list) {
  if (!supports("upload")) {
    toastError("", new Error("This server does not accept uploads."));
    return [];
  }
  const done = [];
  const rows = files.map((file) => {
    const bar = h("div", { class: "progress-fill" });
    const pct = h("span", { class: "upload-pct" }, "Waiting");
    const controller = new AbortController();
    const row = h("li", { class: "upload-row" },
      h("span", { class: "upload-name" }, file.name),
      h("span", { class: "upload-size" }, formatBytes(file.size)),
      pct,
      button("Cancel upload", { icon: "x", variant: "ghost", size: "sm", iconOnly: true, onClick: () => controller.abort() }),
      h("div", { class: "progress progress-thin", role: "progressbar", "aria-label": `Uploading ${file.name}`, "aria-valuemin": 0, "aria-valuemax": 100 }, bar));
    list.append(row);
    return { file, row, bar, pct, controller };
  });
  for (const r of rows) {
    if (r.controller.signal.aborted) {
      r.row.remove();
      continue;
    }
    try {
      r.pct.textContent = "0 %";
      const info = await uploadFile(r.file, role, {
        signal: r.controller.signal,
        onProgress: (f) => {
          const v = Math.round(f * 100);
          r.bar.style.width = `${v}%`;
          r.pct.textContent = v >= 100 ? "Processing…" : `${v} %`;
          r.row.querySelector("[role=progressbar]").setAttribute("aria-valuenow", v);
        },
      });
      done.push(info);
    } catch (err) {
      if (isUnsupported(err)) markUnsupported("upload");
      toastError(`Could not upload “${r.file.name}”`, err);
    } finally {
      r.row.remove();
    }
  }
  return done;
}
