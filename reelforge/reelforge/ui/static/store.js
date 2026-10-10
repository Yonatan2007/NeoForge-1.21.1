/**
 * Application state: reference data (meta, schema), the open project, its
 * files, autosave and which optional backend features exist.
 *
 * Every edit goes through `setValue(path, value)`, which updates
 * `state.project` in place, notifies listeners and schedules a debounced save.
 */

import { api, isUnsupported } from "./api.js";
import { basename, clone, debounce, getPath, setPath, toastError } from "./dom.js";
import { normalizeSchema, inferSchema } from "./fields.js";

const SAVE_DELAY_MS = 800;

export const state = {
  meta: {},
  schema: [],             // normalised sections, see fields.normalizeSchema
  schemaInferred: false,  // true when /api/schema was unavailable
  projectId: null,
  project: null,          // the project dict exactly as the server stores it
  uploads: [],            // file info objects from the server
  outputs: {},            // {video, srt, cover, credits, timings} -> url
  save: { status: "saved", error: null }, // saved | dirty | saving | error
  plan: null,             // last plan result for the open project
};

// --------------------------------------------------------------------------- events

const listeners = new Map();

/** Subscribe to `event`; returns an unsubscribe function. */
export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event).delete(fn);
}

export function emit(event, detail) {
  for (const fn of listeners.get(event) || []) {
    try {
      fn(detail);
    } catch (err) {
      console.error(err);
    }
  }
}

// --------------------------------------------------------------------------- optional features

const unsupported = new Set();

export const supports = (feature) => !unsupported.has(feature);

export function markUnsupported(feature) {
  if (unsupported.has(feature)) return;
  unsupported.add(feature);
  emit("features", feature);
}

/**
 * Call an optional endpoint. When the backend answers 404/501 the feature
 * is switched off (UI hides it) and `undefined` is returned.
 */
export async function optional(feature, fn) {
  if (!supports(feature)) return undefined;
  try {
    return await fn();
  } catch (err) {
    if (isUnsupported(err)) {
      markUnsupported(feature);
      return undefined;
    }
    throw err;
  }
}

// --------------------------------------------------------------------------- reference data

export async function loadReference() {
  const [meta, schema] = await Promise.all([
    optional("meta", () => api.meta()).catch(() => undefined),
    optional("schema", () => api.schema()).catch(() => undefined),
    optional("defaults", () => api.getDefaults()).catch(() => undefined), // probe: hides "my defaults" if absent
  ]);
  state.meta = meta || {};
  state.schema = normalizeSchema(schema);
  state.schemaInferred = !state.schema.length;
}

/** The schema to build forms from: the server's, or one inferred from the project. */
export function schemaSections() {
  if (!state.schemaInferred) return state.schema;
  return state.project ? inferSchema(state.project) : [];
}

// --------------------------------------------------------------------------- editing + autosave

let version = 0;        // bumped on every local edit
let savedVersion = 0;   // the version the server has
let inflight = null;    // promise of the running save

function setSaveStatus(status, error = null) {
  state.save = { status, error };
  emit("save", state.save);
}

const scheduleSave = debounce(() => flush().catch(() => {}), SAVE_DELAY_MS);

/** Change one setting of the open project. */
export function setValue(path, value) {
  if (!state.project) return;
  setPath(state.project, path, value);
  touched([path]);
}

/** Mark `paths` as edited (after mutating state.project directly). */
export function touched(paths) {
  version++;
  setSaveStatus("dirty");
  scheduleSave();
  emit("change", { paths });
}

/** Save now (if anything changed). Resolves once the server has it. */
export async function flush({ keepalive = false } = {}) {
  scheduleSave.cancel();
  if (inflight) await inflight.catch(() => {});
  if (!state.projectId || version === savedVersion) return;
  const id = state.projectId;
  const v = version;
  setSaveStatus("saving");
  inflight = api.saveProject(id, clone(state.project), { keepalive })
    .then(() => {
      if (id !== state.projectId) return;
      savedVersion = Math.max(savedVersion, v);
      if (version === savedVersion) setSaveStatus("saved");
      else scheduleSave();
    })
    .catch((err) => {
      if (id === state.projectId) setSaveStatus("error", err);
      throw err;
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** Retry after a failed save (from the header indicator). */
export function retrySave() {
  flush().catch((err) => toastError("Could not save", err));
}

export const hasUnsavedChanges = () => version !== savedVersion;

// --------------------------------------------------------------------------- open / close

/** Load a project into the editor. Throws ApiError (404 when it is gone). */
export async function openProject(id) {
  if (state.projectId && state.projectId !== id) await flush().catch(() => {});
  const res = await api.getProject(id);
  adoptProjectResponse(id, res);
}

/** Take a full GET/POST project response as the current project. */
export function adoptProjectResponse(id, res) {
  state.projectId = res.id || id;
  state.project = res.project || res;
  state.uploads = res.uploads || [];
  state.outputs = res.outputs || {};
  state.plan = null;
  version = savedVersion = 0;
  setSaveStatus("saved");
  emit("project", state.project);
}

export function closeProject() {
  state.projectId = null;
  state.project = null;
  state.uploads = [];
  state.outputs = {};
  state.plan = null;
  emit("project", null);
}

/** Re-read files and outputs from the server without touching local edits. */
export async function refreshFiles() {
  if (!state.projectId) return;
  const id = state.projectId;
  const res = await api.getProject(id);
  if (id !== state.projectId) return;
  state.uploads = res.uploads || [];
  state.outputs = res.outputs || {};
  emit("uploads");
  emit("outputs");
}

/** Replace parts of the project (e.g. after applying a preset) and save. */
export function replaceParts(parts) {
  for (const [path, value] of Object.entries(parts)) setPath(state.project, path, clone(value));
  touched(Object.keys(parts));
}

// --------------------------------------------------------------------------- files

/** The upload record for a project-relative path such as "uploads/a.jpg". */
export function uploadFor(path) {
  if (!path) return null;
  const name = basename(path);
  return state.uploads.find((u) => u.path === path || u.name === name || basename(u.url || "") === name) || null;
}

/** Where a project file can be fetched (server URL, or http(s) links as-is). */
export function fileUrl(path) {
  if (!path) return null;
  if (/^https?:\/\//.test(path)) return path;
  const up = uploadFor(path);
  return (up && up.url) || api.fileUrl(state.projectId, path);
}

// Which parts of the project an upload of each role changes on the server.
const UPLOAD_KEYS = {
  script: ["script", "name"], // an untitled reel is named after its script file
  voice: ["voice.file"],
  music: ["music.file", "music.source_in", "music.source_out"],
  footage: ["footage.items"],
  reference: ["footage.items"],
};

/**
 * Upload `file` with `role`; merges the server's project changes for that
 * role into the local project (edits made meanwhile elsewhere are kept).
 * Resolves with the server's file info.
 */
export async function uploadFile(file, role, { onProgress, signal } = {}) {
  const id = state.projectId;
  await flush().catch(() => {});
  const before = version;
  const res = await api.upload(id, file, role, { onProgress, signal });
  if (id !== state.projectId) return res.file;
  const server = res.project;
  if (server) {
    const keys = UPLOAD_KEYS[role] || [];
    const local = clone(state.project);
    for (const key of keys) {
      if (key === "footage.items" && version !== before) {
        // Keep local edits to existing cards; take membership and order from the server.
        const mine = new Map((local.footage.items || []).map((it) => [it.path, it]));
        setPath(state.project, key, server.footage.items.map((it) => mine.get(it.path) || it));
      } else {
        setPath(state.project, key, clone(getPath(server, key)));
      }
    }
    if (role === "voice" && !["file", "higgsfield"].includes(state.project.voice.source)) {
      state.project.voice.source = server.voice.source;
    }
    // An autosave sent while the server was still processing the upload may
    // have replaced the project without the new file: send it again.
    if (version !== before) touched(keys);
  }
  if (res.file) {
    state.uploads = state.uploads.filter((u) => u.name !== res.file.name).concat([res.file]);
  }
  emit("uploads");
  emit("change", { paths: UPLOAD_KEYS[role] || [] });
  return res.file;
}

/** Delete an uploaded file and every reference to it. */
export async function removeUpload(path) {
  const id = state.projectId;
  const name = basename(path);
  await flush().catch(() => {});
  const before = version;
  const res = await optional("deleteUpload", () => api.deleteUpload(id, name));
  if (id !== state.projectId) return;
  const p = state.project;
  if (res && res.project) {
    p.voice.file = res.project.voice.file;
    p.music.file = res.project.music.file;
    p.footage.items = res.project.footage.items;
    // edits made during the request were saved (or will be) with the old references
    if (version !== before) touched(["voice.file", "music.file", "footage.items"]);
  } else {
    // Backend cannot delete files: just drop the references.
    if (p.voice.file === path) p.voice.file = null;
    if (p.music.file === path) p.music.file = null;
    p.footage.items = p.footage.items.filter((it) => it.path !== path);
    touched(["voice.file", "music.file", "footage.items"]);
  }
  state.uploads = state.uploads.filter((u) => u.name !== name);
  emit("uploads");
  emit("change", { paths: ["voice.file", "music.file", "footage.items"] });
}

// Save before the page goes away (keepalive lets the request outlive the page).
window.addEventListener("pagehide", () => {
  if (hasUnsavedChanges()) flush({ keepalive: true }).catch(() => {});
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden" && hasUnsavedChanges()) flush().catch(() => {});
});

/** Binding used by generated forms: read and write project settings by path. */
export const projectBinding = {
  get: (path) => getPath(state.project, path),
  set: (path, value) => setValue(path, value),
};
