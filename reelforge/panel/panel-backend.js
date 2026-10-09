/**
 * reelforge control panel backend: the same functions as the HTTP backend in
 * api.js, but kept inside the claude.ai Artifact instead of a local server.
 *
 *   projects/<id>   {name, updated, project, files: [record], outputs: {key: assetId}}
 *   settings/defaults {project}
 *   tasks/<id>      {kind: "plan" | "render", project_id, status, stage, progress,
 *                    message, log, result, error, cancel_requested, created}
 *
 * Uploaded files live in the asset store. Types the store accepts as they
 * are (PNG, JPEG, GIF, WebP, MP4, WebM up to ~19 MiB) are stored directly;
 * everything else (MP3, WAV, MOV, big videos ...) is stored as base64 text
 * in one or more text assets and decoded again in the page.
 *
 * Plans and renders are tasks that Claude carries out: the page writes the
 * task and messages Claude's session through the Claude Code Remote
 * connector; Claude writes progress and results back into the task.
 */

import { ApiError } from "./api.js";
import { parseScript } from "./script-model.js";

const NATIVE = new Set(["image/png", "image/jpeg", "image/gif", "image/webp", "video/mp4", "video/webm", "text/plain"]);
const ASSET_MAX = 19 * 1024 * 1024;   // the asset store takes up to 20 MiB per file
const CHUNK = 13 * 1024 * 1024;       // raw bytes per base64 text asset (~17.4 MiB of text)
const PLAN_TIMEOUT_MS = 6 * 60 * 1000;
const THUMB_SIDE = 480;
const PEAK_BUCKETS = 1000;

const EXT_MIME = {
  txt: "text/plain", md: "text/markdown", markdown: "text/markdown", text: "text/plain",
  mp3: "audio/mpeg", wav: "audio/wav", m4a: "audio/mp4", aac: "audio/aac", ogg: "audio/ogg", oga: "audio/ogg",
  opus: "audio/ogg", flac: "audio/flac", aif: "audio/aiff", aiff: "audio/aiff", wma: "audio/x-ms-wma",
  mp4: "video/mp4", m4v: "video/mp4", mov: "video/quicktime", webm: "video/webm", mkv: "video/x-matroska",
  avi: "video/x-msvideo", "3gp": "video/3gpp",
  jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", gif: "image/gif", webp: "image/webp",
  bmp: "image/bmp", heic: "image/heic", heif: "image/heif", tif: "image/tiff", tiff: "image/tiff",
};

// Which kinds of file each upload role accepts, and how to say so (as server.ROLE_KINDS).
const ROLE_KINDS = {
  script: [["text"], "a .txt or .md text file"],
  voice: [["audio", "video"], "an audio file (or a video with sound)"],
  music: [["audio", "video"], "an audio file (or a video with sound)"],
  footage: [["image", "video"], "a picture or a video"],
  reference: [["image", "video"], "a picture or a video"],
};

const OUTPUT_KEYS = ["video", "srt", "cover", "credits", "timings"];

const clone = (x) => JSON.parse(JSON.stringify(x));
const nowIso = () => new Date().toISOString();
const hex = (n) => Array.from(crypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, "0")).join("");
const blobUrl = (id) => `/_blob/${id}`;
const notFound = (what) => new ApiError(`Not found: ${what}`, 404);
const unprocessable = (message, status = 422) => new ApiError(message, status);
const extOf = (name) => (String(name).match(/\.([a-z0-9]+)$/i) || [])[1]?.toLowerCase() || "";
const stemOf = (name) => String(name).replace(/\.[^.]+$/, "");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function getPath(obj, path) {
  let cur = obj;
  for (const key of String(path).split(".")) {
    if (cur == null) return undefined;
    cur = cur[/^\d+$/.test(key) ? Number(key) : key];
  }
  return cur;
}

function slug(text) {
  return (String(text).toLowerCase().match(/[a-z0-9]+/g) || []).slice(0, 5).join("-").slice(0, 40) || "reel";
}

/** "My clip.MOV" -> a safe file name (as server.safe_filename). */
function safeName(name) {
  const base = String(name || "upload").split(/[\\/]/).pop().replace(/[\u0000-\u001f<>:"|?*]+/g, "_").trim();
  const clean = base.replace(/^\.+/, "") || "upload";
  if (clean.length <= 120) return clean;
  const ext = extOf(clean);
  return clean.slice(0, 110 - ext.length) + (ext ? `.${ext}` : "");
}

function uniqueName(files, name) {
  const taken = new Set(files.map((f) => f.name));
  if (!taken.has(name)) return name;
  const ext = extOf(name);
  const stem = ext ? name.slice(0, -(ext.length + 1)) : name;
  for (let n = 2; ; n++) {
    const candidate = `${stem}-${n}${ext ? `.${ext}` : ""}`;
    if (!taken.has(candidate)) return candidate;
  }
}

function mimeOf(file) {
  const type = (file.type || "").split(";")[0].trim().toLowerCase();
  return type || EXT_MIME[extOf(file.name)] || "application/octet-stream";
}

function kindOf(file) {
  const mime = mimeOf(file);
  if (mime.startsWith("text/") || ["txt", "md", "markdown", "text"].includes(extOf(file.name))) return "text";
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  return "unknown";
}

/** A script file as text, whatever editor wrote it (as server.read_text). */
function decodeText(buffer, name) {
  const bytes = new Uint8Array(buffer);
  let text = null;
  if (bytes[0] === 0xff && bytes[1] === 0xfe) text = new TextDecoder("utf-16le").decode(bytes.subarray(2));
  else if (bytes[0] === 0xfe && bytes[1] === 0xff) text = new TextDecoder("utf-16be").decode(bytes.subarray(2));
  else {
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      text = new TextDecoder("windows-1252").decode(bytes);
    }
    if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
  }
  if (text.includes("\u0000")) {
    throw unprocessable(`“${name}” is not a plain-text file: save the script as text (UTF-8).`, 415);
  }
  return text;
}

// --------------------------------------------------------------------------- media in the browser

function withTimeout(promise, ms, fallback = null) {
  return Promise.race([promise, sleep(ms).then(() => fallback)]);
}

function loadImage(url) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = url;
  });
}

function loadMedia(tag, url) {
  return new Promise((resolve) => {
    const el = document.createElement(tag);
    el.preload = "auto";
    el.muted = true;
    el.playsInline = true;
    el.onloadeddata = () => resolve(el);
    el.onerror = () => resolve(null);
    el.src = url;
    if (tag === "audio") el.onloadedmetadata = () => resolve(el);
  });
}

function seek(video, t) {
  return new Promise((resolve) => {
    video.onseeked = () => resolve(true);
    video.currentTime = t;
  });
}

async function canvasThumb(source, width, height) {
  if (!width || !height) return null;
  const scale = Math.min(1, THUMB_SIDE / Math.max(width, height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
}

/** {info: {kind, duration, width, height}, thumb: Blob | null} measured in the browser. */
async function probe(file, kind) {
  const info = { kind, duration: null, width: null, height: null };
  const url = URL.createObjectURL(file);
  try {
    if (kind === "image") {
      const img = await withTimeout(loadImage(url), 20000);
      if (!img) return { info, thumb: null };
      Object.assign(info, { width: img.naturalWidth, height: img.naturalHeight });
      return { info, thumb: await canvasThumb(img, img.naturalWidth, img.naturalHeight) };
    }
    if (kind === "video") {
      const video = await withTimeout(loadMedia("video", url), 20000);
      if (!video) return { info, thumb: null };
      Object.assign(info, { duration: isFinite(video.duration) ? video.duration : null,
                            width: video.videoWidth || null, height: video.videoHeight || null });
      if (info.width) await withTimeout(seek(video, Math.min(1, (info.duration || 0) / 3)), 8000);
      const thumb = info.width ? await canvasThumb(video, info.width, info.height) : null;
      video.removeAttribute("src");
      return { info, thumb };
    }
    if (kind === "audio") {
      const audio = await withTimeout(loadMedia("audio", url), 20000);
      if (audio && isFinite(audio.duration)) info.duration = audio.duration;
    }
    return { info, thumb: null };
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** {duration, peaks}: the largest absolute sample per bucket (as music.waveform_peaks). */
async function computePeaks(blob, buckets = PEAK_BUCKETS) {
  const Ctx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  if (!Ctx) return null;
  try {
    const ctx = new Ctx(1, 2, 44100);
    const audio = await ctx.decodeAudioData(await blob.arrayBuffer());
    const peaks = new Array(buckets).fill(0);
    const n = audio.length;
    for (let c = 0; c < audio.numberOfChannels; c++) {
      const data = audio.getChannelData(c);
      for (let b = 0; b < buckets; b++) {
        const lo = Math.floor((b * n) / buckets);
        const hi = Math.max(lo + 1, Math.floor(((b + 1) * n) / buckets));
        let m = peaks[b];
        for (let i = lo; i < hi && i < n; i++) {
          const v = data[i] < 0 ? -data[i] : data[i];
          if (v > m) m = v;
        }
        peaks[b] = m;
      }
    }
    return { duration: audio.duration, peaks: peaks.map((p) => Math.round(Math.min(1, p) * 1000) / 1000) };
  } catch {
    return null;
  }
}

function toBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).replace(/^data:[^,]*,/, ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

function fromBase64(text) {
  const bin = atob(text.replace(/\s+/g, ""));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// --------------------------------------------------------------------------- validation (as schema.validate)

const HEX = /^#[0-9a-f]{6}$/i;

function checkField(f, value, name) {
  if (value === undefined) return [];
  if (value === null) {
    const ok = f.nullable || f.type === "optional-number" || f.type === "lines";
    return ok ? [] : [`${name}: a value is required.`];
  }
  if (["number", "int", "optional-number"].includes(f.type)) {
    if (typeof value !== "number" || !isFinite(value)) return [`${name}: must be a number.`];
    if ((f.min != null && value < f.min) || (f.max != null && value > f.max)) {
      return [`${name}: must be between ${f.min} and ${f.max} (got ${value}).`];
    }
  } else if (f.type === "select" && !f.custom) {
    const allowed = (f.options || []).map((o) => o.value);
    if (!allowed.includes(value)) return [`${name}: ${JSON.stringify(value)} is not one of ${allowed.join(", ")}.`];
  } else if (f.type === "multiselect") {
    const allowed = new Set((f.options || []).map((o) => o.value));
    const badValues = (Array.isArray(value) ? value : [value]).filter((v) => !allowed.has(v));
    if (badValues.length) return [`${name}: unknown ${badValues.join(", ")}.`];
  } else if (f.type === "color") {
    if (typeof value === "string") {
      if (!(f.options || []).some((o) => o.value === value) && !HEX.test(value)) {
        return [`${name}: ${JSON.stringify(value)} is not a colour like #ffcc00.`];
      }
    } else if (!Array.isArray(value) || value.length !== 3
               || !value.every((c) => typeof c === "number" && c >= 0 && c <= 255)) {
      return [`${name}: a colour needs red, green and blue values of 0-255.`];
    }
  }
  return [];
}

function validate(schema, project) {
  if (!project || typeof project !== "object" || Array.isArray(project)) {
    throw unprocessable("The project must be a JSON object.");
  }
  const problems = [];
  for (const section of schema) {
    for (const f of section.fields || []) {
      const name = `${section.label} › ${f.label}`;
      if (f.path.includes("[].")) {
        const [listPath, sub] = f.path.split("[].");
        const items = getPath(project, listPath);
        if (items == null) continue;
        if (!Array.isArray(items)) {
          problems.push(`${listPath}: must be a list.`);
          continue;
        }
        items.forEach((item, n) => problems.push(...checkField(f, getPath(item, sub), `Clip ${n + 1}: ${name}`)));
      } else {
        problems.push(...checkField(f, getPath(project, f.path), name));
      }
    }
  }
  if (problems.length) throw unprocessable([...new Set(problems)].join("\n"));
}

// --------------------------------------------------------------------------- the backend

/**
 * @param {object} caps   {db, assets, mcp} namespaces from claude.use()
 * @param {object} data   {meta, schema, presets, factory, footageItem, voice, config}
 */
export function createPanelBackend({ db, assets, mcp }, data) {
  const resolved = new Map();   // first asset id -> blob: URL of a decoded base64 file
  const resolving = new Map();  // first asset id -> promise
  const taskCache = new Map();  // task id -> {data, unsub, ready}
  const P = (id) => db.doc(`projects/${id}`);
  const T = (id) => db.doc(`tasks/${id}`);

  async function retrying(fn) {
    try {
      return await fn();
    } catch (err) {
      const code = err && err.code;
      if (code === "store_unavailable" || code === "unavailable") {
        await sleep(800 + Math.random() * 800);
        return fn();
      }
      if (code === "rate_limited" || code === "resource_exhausted") {
        await sleep(3000);
        return fn();
      }
      throw err;
    }
  }

  function storeError(err) {
    if (err instanceof ApiError || (err && err.name === "AbortError")) return err;
    const code = err && err.code;
    const messages = {
      too_large: "The file is too big for the panel's storage.",
      quota_or_state: "The panel's storage is full. Delete reels or files you no longer need, then try again.",
      quota_exceeded: "The panel's storage is full. Delete reels or files you no longer need, then try again.",
      unsupported_type: "This kind of file can't be stored in the panel.",
      not_granted: "This view of the panel can't save files. Open it from your own Claude account.",
      upstream_auth: "Your Claude session expired. Reload the page and try again.",
    };
    return new ApiError(messages[code] || `Storage error: ${(err && err.message) || code || "unknown"}`, 0, err);
  }

  async function uploadAsset(blob, type) {
    if (!assets) throw new ApiError("This view of the panel can't save files. Open it from your own Claude account.", 0);
    try {
      return await retrying(() => assets.upload(blob, { type }));
    } catch (err) {
      throw storeError(err);
    }
  }

  /** Store a file: {type: "raw" | "b64", ids, mime}. */
  async function storeFile(blob, mime, onProgress, check) {
    if (NATIVE.has(mime) && blob.size <= ASSET_MAX) {
      const res = await uploadAsset(blob, mime);
      onProgress(1);
      return { type: "raw", ids: [res.id], mime };
    }
    const ids = [];
    const parts = Math.max(1, Math.ceil(blob.size / CHUNK));
    for (let i = 0; i < parts; i++) {
      check();
      const text = await toBase64(blob.slice(i * CHUNK, (i + 1) * CHUNK));
      const res = await uploadAsset(new Blob([text], { type: "text/plain" }), "text/plain");
      ids.push(res.id);
      onProgress((i + 1) / parts);
    }
    return { type: "b64", ids, mime };
  }

  /** A blob: URL for a base64-stored file (decoded once per page). */
  function resolveB64(store) {
    const key = store.ids[0];
    if (resolved.has(key)) return Promise.resolve(resolved.get(key));
    if (!resolving.has(key)) {
      resolving.set(key, (async () => {
        const parts = [];
        for (const id of store.ids) {
          const res = await fetch(blobUrl(id));
          if (!res.ok) throw new Error(`file part ${id} is missing (${res.status})`);
          parts.push(fromBase64(await res.text()));
        }
        const url = URL.createObjectURL(new Blob(parts, { type: store.mime }));
        resolved.set(key, url);
        return url;
      })().catch((err) => {
        resolving.delete(key);
        console.warn("reelforge panel: cannot read a stored file", err);
        return null;
      }));
    }
    return resolving.get(key);
  }

  function urlOf(rec) {
    if (!rec.store || !rec.store.ids || !rec.store.ids.length) return null;
    return rec.store.type === "raw" ? blobUrl(rec.store.ids[0]) : resolved.get(rec.store.ids[0]) || null;
  }

  function roleOf(project, rec) {
    if (project.voice && project.voice.file === rec.path) return "voice";
    if (project.music && project.music.file === rec.path) return "music";
    for (const item of (project.footage && project.footage.items) || []) {
      if (item.path === rec.path) return item.role;
    }
    return rec.kind === "text" ? "script" : null;
  }

  function fileView(rec, project) {
    return {
      name: rec.name, url: urlOf(rec), path: rec.path, role: roleOf(project, rec), kind: rec.kind,
      info: rec.info || { kind: rec.kind, duration: null, width: null, height: null },
      size: rec.size, uploaded: rec.uploaded, thumb_url: rec.thumb ? blobUrl(rec.thumb) : null,
    };
  }

  function outputsOf(d) {
    const out = {};
    for (const key of OUTPUT_KEYS) out[key] = d.outputs && d.outputs[key] ? blobUrl(d.outputs[key]) : null;
    return out;
  }

  async function view(id, d) {
    const files = d.files || [];
    // Audio, scripts and pictures are decoded before the view is shown;
    // big base64 videos (e.g. phone .mov clips) decode in the background.
    await Promise.all(files.filter((f) => f.store && f.store.type === "b64" && f.kind !== "video")
      .map((f) => resolveB64(f.store)));
    files.filter((f) => f.store && f.store.type === "b64" && f.kind === "video").forEach((f) => resolveB64(f.store));
    return { id, project: d.project, uploads: files.map((f) => fileView(f, d.project)), outputs: outputsOf(d), job: null };
  }

  async function load(id) {
    const snap = await retrying(() => P(id).get());
    if (!snap.exists) throw notFound(`project ${id}`);
    return clone(snap.data());
  }

  async function save(id, d) {
    d.updated = nowIso();
    d.name = (d.project && d.project.name) || d.name || "untitled";
    try {
      await retrying(() => P(id).set(d));
    } catch (err) {
      throw storeError(err);
    }
  }

  async function defaults() {
    const snap = await retrying(() => db.doc("settings/defaults").get());
    return snap.exists && snap.data().project ? clone(snap.data().project) : clone(data.factory);
  }

  /** Every asset id any project still points at. */
  async function referencedIds(exceptId) {
    const snap = await retrying(() => db.collection("projects").get());
    const ids = new Set();
    for (const doc of snap.docs) {
      if (doc.id === exceptId) continue;
      const d = doc.data();
      for (const f of d.files || []) {
        (f.store && f.store.ids || []).forEach((x) => ids.add(x));
        if (f.thumb) ids.add(f.thumb);
      }
      Object.values(d.outputs || {}).forEach((x) => x && ids.add(x));
    }
    return ids;
  }

  async function deleteAssets(ids, keep) {
    if (!assets) return;
    for (const id of ids) {
      if (!id || keep.has(id)) continue;
      try {
        await assets.delete(id);
      } catch (err) {
        console.warn("reelforge panel: could not delete a stored file", err);
      }
    }
  }

  const recordIds = (rec) => [...((rec.store && rec.store.ids) || []), rec.thumb].filter(Boolean);

  function attach(project, rel, role, kind, text, name) {
    if (role === "script") {
      project.script = text.trim();
      if (["", "untitled"].includes(project.name)) project.name = stemOf(name).replace(/_/g, " ");
    } else if (role === "voice") {
      project.voice.file = rel;
      project.voice.source = "file";
    } else if (role === "music") {
      Object.assign(project.music, { file: rel, source_in: 0, source_out: null });
    } else {
      project.footage.items.push({ ...clone(data.footageItem), path: rel, role, kind });
    }
  }

  // ---- tasks (plans and renders, done by Claude) --------------------------------------

  async function notify(kind, id, projectId) {
    const cfg = data.config || {};
    const subject = projectId ? ` for reel "${projectId}"` : "";
    const docs = projectId ? `tasks/${id} and projects/${projectId}` : `tasks/${id}`;
    const message = `reelforge panel request: ${kind} task "${id}"${subject}. `
      + `Panel: ${cfg.artifact || "(the reelforge panel artifact)"}. Read ${docs} `
      + `from the panel's database and carry it out (see .claude/skills/jackk-reel/SKILL.md, "Control panel requests").`;
    if (!mcp || !cfg.session_id) return "Claude can't be messaged from this view.";
    try {
      await mcp.callTool(cfg.connector || "Claude Code Remote", cfg.tool || "send_message",
                         { session_id: cfg.session_id, message });
      return null;
    } catch (err) {
      const why = {
        server_not_connected: "the Claude Code Remote connector isn't connected",
        not_in_manifest: "the panel isn't allowed to message Claude",
        needs_reauth: "the Claude Code Remote connector needs to be reconnected",
      }[err && err.code];
      return why ? `Claude wasn't messaged (${why}).` : `Claude wasn't messaged (${(err && (err.message || err.code)) || "unknown error"}).`;
    }
  }

  async function newTask(kind, projectId, extra = {}) {
    if (projectId) await load(projectId); // 404 for a deleted reel
    const id = `${kind}-${Date.now().toString(36)}-${hex(3)}`;
    const task = {
      kind, project_id: projectId || null, status: "queued", stage: null, progress: 0,
      message: "Waiting for Claude to start…", log: [], result: null, error: null,
      cancel_requested: false, created: nowIso(), notified: false, ...extra,
    };
    try {
      await retrying(() => T(id).set(task));
    } catch (err) {
      throw storeError(err);
    }
    const problem = await notify(kind, id, projectId);
    const ask = { plan: "plan my reel", render: "render my reel", publish: "publish my video" }[kind] || `run my ${kind}`;
    const patch = problem
      ? { notify_error: problem, message: `${problem} Send “${ask}” in your Claude chat and it starts.` }
      : { notified: true };
    await retrying(() => T(id).update(patch)).catch(() => {});
    return { id, problem };
  }

  function watchTask(id) {
    let entry = taskCache.get(id);
    if (entry) return entry.ready;
    entry = { data: null };
    entry.ready = new Promise((resolve, reject) => {
      entry.unsub = T(id).onSnapshot((snap) => {
        entry.data = snap.exists ? snap.data() : null;
        resolve();
        const st = entry.data && entry.data.status;
        if (st && !["queued", "running"].includes(st)) {
          entry.unsub();
          entry.final = true;
        }
      }, (err) => {
        taskCache.delete(id);
        reject(storeError(err));
      });
    });
    taskCache.set(id, entry);
    return entry.ready;
  }

  function jobView(id, t) {
    const result = t.result ? { ...t.result, outputs: t.result.outputs || undefined } : null;
    return {
      job_id: id, project_id: t.project_id, status: t.status, stage: t.stage || null,
      progress: t.progress || 0, message: t.message || "", log: t.log || [], result, error: t.error || null,
    };
  }

  // ---- the API (same names and answers as api.js's HTTP backend) ----------------------

  return {
    name: "panel",

    // for the control panel's own pages (publishing, accounts)
    _panel: { db, assets, mcp, data, retrying, storeError, uploadAsset, storeFile, resolveB64, probe,
              blobUrl, newTask, nowIso, hex, mimeOf, kindOf, safeName, clone, urlOf },

    meta: async () => clone(data.meta),
    schema: async () => clone(data.schema),
    getPreset: async (name) => {
      if (!data.presets[name]) throw notFound(`preset ${name}`);
      return clone(data.presets[name]);
    },

    getDefaults: () => defaults(),
    saveDefaults: async (project) => {
      validate(data.schema, project);
      await retrying(() => db.doc("settings/defaults").set({ project: clone(project), updated: nowIso() }));
      return defaults();
    },
    resetDefaults: async () => {
      await retrying(() => db.doc("settings/defaults").delete());
      return clone(data.factory);
    },

    listProjects: async () => {
      const snap = await retrying(() => db.collection("projects").get());
      const rows = snap.docs.map((doc) => {
        const d = doc.data();
        const outputs = outputsOf(d);
        let thumb = outputs.cover;
        if (!thumb) {
          for (const item of (d.project && d.project.footage && d.project.footage.items) || []) {
            const rec = (d.files || []).find((f) => f.path === item.path && f.thumb);
            if (rec) {
              thumb = blobUrl(rec.thumb);
              break;
            }
          }
        }
        return { id: doc.id, name: d.name || (d.project && d.project.name) || "untitled",
                 updated: d.updated || null, has_video: Boolean(outputs.video), thumb };
      });
      return rows.sort((a, b) => String(b.updated).localeCompare(String(a.updated)));
    },

    createProject: async ({ name, preset } = {}) => {
      let project;
      if (preset) {
        if (!data.presets[preset]) throw unprocessable(`Unknown preset ${JSON.stringify(preset)}.`);
        project = clone(data.presets[preset]);
      } else {
        project = await defaults();
      }
      project.name = String(name || "").trim() || "untitled";
      const id = `${slug(project.name !== "untitled" ? project.name : "reel")}-${hex(3)}`;
      const d = { name: project.name, project, files: [], outputs: {}, created: nowIso() };
      await save(id, d);
      return view(id, d);
    },

    getProject: async (id) => view(id, await load(id)),

    saveProject: async (id, project) => {
      validate(data.schema, project);
      await load(id); // a deleted reel is not recreated by an autosave
      try {
        await retrying(() => P(id).update({ project: clone(project), name: project.name || "untitled", updated: nowIso() }));
      } catch (err) {
        throw storeError(err);
      }
      return { id, project };
    },

    deleteProject: async (id) => {
      const d = await load(id);
      await retrying(() => P(id).delete());
      const keep = await referencedIds(id);
      await deleteAssets([...(d.files || []).flatMap(recordIds), ...Object.values(d.outputs || {})], keep);
      return { ok: true };
    },

    duplicateProject: async (id) => {
      const d = await load(id);
      const copy = clone(d);
      copy.project.name = `${d.project.name || "untitled"} (copy)`;
      copy.outputs = {};
      copy.created = nowIso();
      const newId = `${slug(copy.project.name)}-${hex(3)}`;
      await save(newId, copy);
      return view(newId, copy);
    },

    async upload(projectId, file, role, { onProgress, signal } = {}) {
      const progress = (f) => onProgress && onProgress(Math.max(0, Math.min(1, f)));
      const check = () => {
        if (signal && signal.aborted) throw new DOMException("Upload cancelled", "AbortError");
      };
      if (!ROLE_KINDS[role]) throw unprocessable(`Upload role must be one of ${Object.keys(ROLE_KINDS).join(", ")}.`);
      const [kinds, wanted] = ROLE_KINDS[role];
      const kind = kindOf(file);
      const name0 = file.name || "upload";
      if (!kinds.includes(kind)) throw unprocessable(`“${name0}” can't be used as ${role}: please upload ${wanted}.`, 415);
      if (!file.size) throw unprocessable("The uploaded file is empty.");
      const limit = data.meta.max_upload_mb * 1e6;
      if (file.size > limit) throw unprocessable(`The file is too big: the limit is ${data.meta.max_upload_mb} MB.`, 413);
      await load(projectId);
      progress(0.02);

      let text = null;
      let blob = file;
      let mime = mimeOf(file);
      let info = { kind, duration: null, width: null, height: null };
      let thumbBlob = null;
      if (kind === "text") {
        text = decodeText(await file.arrayBuffer(), name0);
        blob = new Blob([text], { type: "text/plain" });
        mime = "text/plain";
      } else {
        ({ info, thumb: thumbBlob } = await probe(file, kind));
      }
      check();
      const peaks = (role === "voice" || role === "music" || kind === "audio") ? await computePeaks(file) : null;
      if (peaks && info.duration == null) info.duration = peaks.duration;
      check();
      progress(0.08);
      const store = await storeFile(blob, mime, (f) => progress(0.08 + 0.82 * f), check);
      const thumb = thumbBlob ? (await uploadAsset(thumbBlob, "image/jpeg").catch(() => null)) : null;
      progress(0.95);

      const d = await load(projectId);
      d.files = d.files || [];
      const name = uniqueName(d.files, safeName(name0));
      const rel = `uploads/${name}`;
      const rec = { name, path: rel, kind, info, size: file.size, uploaded: nowIso(), store,
                    thumb: thumb ? thumb.id : null, peaks: peaks || null };
      d.files.push(rec);
      attach(d.project, rel, role, kind, text, name);
      await save(projectId, d);
      if (store.type === "b64") {
        // the page already has the bytes: no need to download them again
        resolved.set(store.ids[0], URL.createObjectURL(new Blob([blob], { type: store.mime })));
      }
      progress(1);
      return { project: d.project, file: { ...fileView(rec, d.project), peaks: peaks || null } };
    },

    deleteUpload: async (projectId, name) => {
      const d = await load(projectId);
      const rel = `uploads/${name}`;
      const rec = (d.files || []).find((f) => f.name === name);
      const p = d.project;
      if (p.voice.file === rel) p.voice.file = null;
      if (p.music.file === rel) Object.assign(p.music, { file: null, source_in: 0, source_out: null });
      p.footage.items = (p.footage.items || []).filter((i) => i.path !== rel);
      d.files = (d.files || []).filter((f) => f.name !== name);
      await save(projectId, d);
      if (rec) {
        const keep = await referencedIds(projectId);
        for (const f of d.files) recordIds(f).forEach((x) => keep.add(x));
        Object.values(d.outputs || {}).forEach((x) => x && keep.add(x));
        await deleteAssets(recordIds(rec), keep);
      }
      return view(projectId, d);
    },

    waveform: async (projectId, file, buckets = PEAK_BUCKETS) => {
      const d = await load(projectId);
      const rec = (d.files || []).find((f) => f.path === file || f.name === String(file).split("/").pop());
      if (!rec) throw notFound(file);
      if (rec.peaks && rec.peaks.peaks && rec.peaks.peaks.length === buckets) return rec.peaks;
      const url = rec.store.type === "raw" ? blobUrl(rec.store.ids[0]) : await resolveB64(rec.store);
      const peaks = url ? await computePeaks(await (await fetch(url)).blob(), buckets) : null;
      if (!peaks) throw unprocessable(`Cannot read the audio of ${rec.name}.`);
      return peaks;
    },

    /** URL of a project file (synchronous). Uploads carry their own URL; this is the fallback. */
    fileUrl: () => "",

    plan: async (projectId) => {
      const { id, problem } = await newTask("plan", projectId);
      if (problem) {
        await retrying(() => T(id).update({ status: "error", error: problem })).catch(() => {});
        throw new ApiError(`${problem} You can also ask for the plan in your Claude chat.`, 503);
      }
      return new Promise((resolve, reject) => {
        let unsub = () => {};
        const timer = setTimeout(() => {
          unsub();
          reject(new ApiError("Claude hasn't answered yet. It may still be busy: try “Preview plan” again in a minute.", 504));
        }, PLAN_TIMEOUT_MS);
        unsub = T(id).onSnapshot((snap) => {
          const t = snap.exists ? snap.data() : null;
          if (!t) return;
          if (t.status === "done") {
            clearTimeout(timer);
            unsub();
            resolve(clone(t.result || {}));
          } else if (t.status === "error" || t.status === "cancelled") {
            clearTimeout(timer);
            unsub();
            reject(new ApiError(t.error || "Planning failed.", 422));
          }
        }, (err) => {
          clearTimeout(timer);
          reject(storeError(err));
        });
      });
    },

    render: async (projectId) => {
      const { id } = await newTask("render", projectId);
      return { job_id: id, status: "queued" };
    },

    job: async (jobId) => {
      await watchTask(jobId);
      const entry = taskCache.get(jobId);
      if (!entry || !entry.data) {
        if (entry && entry.unsub) entry.unsub();
        taskCache.delete(jobId);
        throw notFound(`job ${jobId}`);
      }
      const v = jobView(jobId, entry.data);
      if (entry.final) taskCache.delete(jobId); // a finished job is read fresh next time
      return v;
    },

    cancelJob: async (jobId) => {
      const snap = await retrying(() => T(jobId).get());
      if (!snap.exists) throw notFound(`job ${jobId}`);
      const t = snap.data();
      const patch = t.status === "queued"
        ? { cancel_requested: true, status: "cancelled", message: "Render cancelled" }
        : { cancel_requested: true, message: "Cancelling…" };
      await retrying(() => T(jobId).update(patch));
      taskCache.delete(jobId);
      return jobView(jobId, { ...t, ...patch });
    },

    higgsfieldRequest: async (projectId) => {
      const { project } = await load(projectId);
      const parsed = parseScript(project.script);
      if (!parsed.words.length) throw unprocessable("Write or upload a script first.");
      const preset = project.voice.higgsfield_preset || "elevenlabs";
      const params = { ...(data.voice.presets[preset] || data.voice.presets.elevenlabs),
                       prompt: parsed.text, voice_type: project.voice.voice_id ? project.voice.voice_type || "preset" : "preset",
                       voice_id: project.voice.voice_id || data.voice.default_voice_id };
      return { tool: "mcp__higgsfield__generate_audio", params,
               next: "Claude makes this voice with your Higgsfield account when you render." };
    },
  };
}
