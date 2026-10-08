/**
 * Starts the reelforge web UI inside a claude.ai Artifact: connects the
 * Artifact's database, asset store, downloads and the Claude Code Remote
 * connector, swaps them in as the UI's backend, then loads the app.
 */

import { useBackend } from "./api.js";
import { createPanelBackend } from "./panel-backend.js";

const SAVE_EXTS = new Set(["gif", "png", "jpg", "jpeg", "webp", "mp4", "webm", "txt", "json", "md", "csv", "pdf", "zip"]);

function showMessage(html) {
  const box = document.getElementById("panel-boot");
  if (box) box.innerHTML = `<p>${html}</p>`;
}

async function readJson(name) {
  const res = await fetch(`panel-data/${name}`);
  if (!res.ok) throw new Error(`${name}: ${res.status}`);
  return res.json();
}

/** Save a file through the viewer's download prompt (plain download links don't work in an Artifact). */
function saver(downloads) {
  return async (url, filename) => {
    const { toast, toastError } = await import("./dom.js");
    if (!downloads) {
      toast("Downloads aren't available in this view. Ask Claude in the chat to send you the file.", { kind: "error" });
      return;
    }
    let name = filename || "reelforge-file";
    const ext = (name.match(/\.([a-z0-9]+)$/i) || [])[1];
    if (!ext || !SAVE_EXTS.has(ext.toLowerCase())) name += ".txt"; // e.g. captions.srt -> captions.srt.txt
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`the file could not be read (${res.status})`);
      await downloads.save({ filename: name, data: await res.blob() });
    } catch (err) {
      if (err && err.code === "declined") return;
      toastError("Could not save the file", err);
    }
  };
}

(async function boot() {
  const claude = window.claude;
  if (!claude || typeof claude.use !== "function") {
    showMessage("Open this page in the <strong>Claude app</strong> or on claude.ai: it keeps your reels in your Claude account.");
    return;
  }
  const use = (name) => claude.use(name).catch(() => null);
  const [db, assets, downloads, mcp] = await Promise.all([use("db"), use("assets"), use("downloads"), use("mcp")]);
  if (!db) {
    showMessage("reelforge can't reach its storage in this view. Sign in to Claude and open the panel from your own account.");
    return;
  }
  let data;
  try {
    const [meta, schema, presets, factory, footageItem, voice, config] = await Promise.all(
      ["meta.json", "schema.json", "presets.json", "factory.json", "footage-item.json", "voice.json", "config.json"].map(readJson));
    data = { meta, schema, presets, factory, footageItem, voice, config };
  } catch (err) {
    showMessage(`reelforge could not load its settings (${String(err.message || err)}). Reload the page.`);
    return;
  }
  if (!assets) data.meta.capabilities.uploads = false;
  useBackend(createPanelBackend({ db, assets, mcp }, data));
  globalThis.reelforgeSaveFile = saver(downloads);
  await import("./app.js");
})();
