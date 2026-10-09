/**
 * Control panel pages for posting: Publish (your videos, and one post per
 * video with a section for each platform) and Accounts (connected accounts,
 * and profile details edited for one account or many at once).
 *
 *   videos/<id>     your own uploaded videos {title, store, thumb, info, size, created}
 *   posts/<key>     post settings per video (key "reel:<project id>" or "video:<id>")
 *   accounts/<id>   {platform, handle, connector_id?, status, profile: {name, bio, link, picture}}
 *   tasks/<id>      kind "publish": Claude posts the video and writes results back
 *
 * TikTok accounts are connected and read through the Higgsfield connector
 * (which also lists TikTok's trending licensed sounds). Instagram and
 * YouTube posting goes through a social-media connector added in Claude.
 */

import { api } from "./api.js";
import { callout, dropzone } from "./components.js";
import {
  button, clear, confirmDialog, copyText, debounce, dialog, formatBytes, formatSeconds, h, relativeTime, toast, toastError,
} from "./dom.js";
import { paintRange, switchControl } from "./fields.js";
import { icon } from "./icons.js";

const PLATFORMS = {
  tiktok: { label: "TikTok", captionMax: 2200, bioMax: 80,
            settings: (a) => (a && a.handle ? `https://www.tiktok.com/@${a.handle.replace(/^@/, "")}` : "https://www.tiktok.com/") },
  instagram: { label: "Instagram", captionMax: 2200, bioMax: 150, settings: () => "https://www.instagram.com/accounts/edit/" },
  youtube: { label: "YouTube Shorts", captionMax: 5000, bioMax: 1000, settings: () => "https://studio.youtube.com/" },
};
const ORDER = ["tiktok", "instagram", "youtube"];
const TIKTOK_PRIVACY = [
  { value: "PUBLIC_TO_EVERYONE", label: "Everyone" }, { value: "FOLLOWER_OF_CREATOR", label: "Followers" },
  { value: "MUTUAL_FOLLOW_FRIENDS", label: "Friends" }, { value: "SELF_ONLY", label: "Only me" },
];
const YT_PRIVACY = [{ value: "public", label: "Public" }, { value: "unlisted", label: "Unlisted" }, { value: "private", label: "Private" }];
const GENRES = ["ALL", "POP", "HIP_HOP/RAP", "LO-FI", "EDM", "COUNTRY", "K-POP", "CHILL_BEATS", "EPIC"];

const P = () => api._panel;
const higgsfield = (tool, input) => P().mcp.callTool("higgsfield", tool, input || {});

function newPost(video) {
  const sound = { sound: null, sound_volume: 0, original_volume: 100 };
  return {
    video,
    common: { caption: "", hashtags: [] },
    tiktok: { enabled: true, accounts: [], caption: null, hashtags: null, privacy: "PUBLIC_TO_EVERYONE",
              allow_comment: true, allow_duet: true, allow_stitch: true, is_aigc: true, mode: "DIRECT_POST", ...sound },
    instagram: { enabled: true, accounts: [], caption: null, hashtags: null, share_to_feed: true, ...sound },
    youtube: { enabled: true, accounts: [], title: "", caption: null, hashtags: null, privacy: "public",
               made_for_kids: false, ...sound },
  };
}

const tagsOf = (text) => [...new Set(String(text || "").split(/[\s,]+/).map((t) => t.replace(/^#+/, "").trim()).filter(Boolean))];
const tagText = (tags) => (tags || []).map((t) => `#${t}`).join(" ");

// --------------------------------------------------------------------------- data

async function listAccounts() {
  const snap = await P().retrying(() => P().db.collection("accounts").get());
  return snap.docs.map((d) => ({ id: d.id, ...d.data() }))
    .sort((a, b) => ORDER.indexOf(a.platform) - ORDER.indexOf(b.platform) || String(a.handle).localeCompare(String(b.handle)));
}

async function listVideos() {
  const { db, retrying, blobUrl } = P();
  const [projects, uploads] = await Promise.all([
    retrying(() => db.collection("projects").get()), retrying(() => db.collection("videos").get())]);
  const rows = [];
  for (const doc of projects.docs) {
    const d = doc.data();
    if (!d.outputs || !d.outputs.video) continue;
    rows.push({ key: `reel:${doc.id}`, kind: "reel", id: doc.id, title: d.name || "Untitled reel", updated: d.updated,
                thumb: d.outputs.cover ? blobUrl(d.outputs.cover) : null, url: blobUrl(d.outputs.video) });
  }
  for (const doc of uploads.docs) {
    const d = doc.data();
    rows.push({ key: `video:${doc.id}`, kind: "upload", id: doc.id, title: d.title || "My video", updated: d.created,
                thumb: d.thumb ? blobUrl(d.thumb) : null, store: d.store, info: d.info, size: d.size });
  }
  return rows.sort((a, b) => String(b.updated).localeCompare(String(a.updated)));
}

async function videoUrl(v) {
  if (v.url) return v.url;
  if (!v.store) return null;
  return v.store.type === "raw" ? P().blobUrl(v.store.ids[0]) : P().resolveB64(v.store);
}

async function uploadVideo(file, onProgress) {
  const panel = P();
  const kind = panel.kindOf(file);
  if (kind !== "video") throw new Error(`“${file.name}” isn't a video.`);
  const { info, thumb } = await panel.probe(file, "video");
  const store = await panel.storeFile(file, panel.mimeOf(file), onProgress, () => {});
  const thumbRes = thumb ? await panel.uploadAsset(thumb, "image/jpeg").catch(() => null) : null;
  const id = `v-${Date.now().toString(36)}-${panel.hex(3)}`;
  await panel.retrying(() => panel.db.doc(`videos/${id}`).set({
    title: panel.safeName(file.name).replace(/\.[^.]+$/, ""), store, thumb: thumbRes ? thumbRes.id : null,
    info, size: file.size, created: panel.nowIso() }));
  return id;
}

// --------------------------------------------------------------------------- small UI pieces

function head(title, lead, ...actions) {
  return h("header", { class: "home-head" },
    h("div", {}, h("h1", { class: "page-title" }, title), lead ? h("p", { class: "page-lead" }, lead) : null),
    actions.length ? h("div", { class: "row" }, actions) : null);
}

function card(title, sub, ...children) {
  return h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", { class: "card-title" }, title), sub ? h("p", { class: "card-sub" }, sub) : null),
    ...children);
}

function textField({ label, value, placeholder, onInput, multiline = false, max, rows = 3, help }) {
  const id = `f-${Math.random().toString(36).slice(2, 8)}`;
  const input = multiline
    ? h("textarea", { id, class: "input textarea", rows, placeholder: placeholder || "", value: value || "" })
    : h("input", { id, class: "input", type: "text", placeholder: placeholder || "", value: value || "", autocomplete: "off" });
  const count = max ? h("span", { class: "field-value" }) : null;
  const paint = () => {
    if (!count) return;
    count.textContent = `${input.value.length} / ${max}`;
    count.classList.toggle("is-over", input.value.length > max);
  };
  input.addEventListener("input", () => {
    paint();
    onInput(input.value);
  });
  paint();
  return h("div", { class: "field" },
    h("div", { class: "field-head" }, h("label", { class: "field-label", for: id }, label), count),
    input, help ? h("p", { class: "field-help" }, help) : null);
}

function slider({ label, value, onChange, help }) {
  const id = `s-${Math.random().toString(36).slice(2, 8)}`;
  const out = h("span", { class: "field-value" }, `${value} %`);
  const range = h("input", { id, type: "range", class: "range", min: 0, max: 100, step: 1, value });
  range.addEventListener("input", () => {
    out.textContent = `${range.value} %`;
    paintRange(range);
    onChange(Number(range.value));
  });
  requestAnimationFrame(() => paintRange(range));
  return h("div", { class: "field" },
    h("div", { class: "field-head" }, h("label", { class: "field-label", for: id }, label), out),
    range, help ? h("p", { class: "field-help" }, help) : null);
}

function toggle(label, checked, onChange) {
  return h("div", { class: "field field-bool" },
    switchControl({ id: `t-${Math.random().toString(36).slice(2, 8)}`, checked, label, onChange }));
}

function selectField(label, options, value, onChange) {
  const id = `sel-${Math.random().toString(36).slice(2, 8)}`;
  const sel = h("select", { id, class: "input select" },
    options.map((o) => h("option", { value: o.value, selected: o.value === value }, o.label)));
  sel.addEventListener("change", () => onChange(sel.value));
  return h("div", { class: "field" }, h("label", { class: "field-label", for: id }, label), sel);
}

function accountName(a) {
  return a.handle ? `@${String(a.handle).replace(/^@/, "")}` : a.label || PLATFORMS[a.platform].label;
}

// --------------------------------------------------------------------------- Publish: video library

function renderLibrary(container) {
  const grid = h("ul", { class: "project-grid", "aria-busy": "true" });
  const progress = h("div", { class: "stack" });
  const drop = dropzone({
    accept: "video/*", multiple: true, iconName: "upload", compact: true,
    title: "Upload your own videos", hint: "MP4, MOV or WebM. They appear here next to the reels you made.",
    onFiles: async (files) => {
      for (const file of files) {
        const fill = h("div", { class: "progress-fill", style: { width: "0%" } });
        const row = h("div", { class: "upload-row" }, h("span", { class: "upload-name" }, file.name),
          h("span", { class: "upload-size" }, formatBytes(file.size)), h("div", { class: "progress progress-thin" }, fill));
        progress.append(row);
        try {
          await uploadVideo(file, (f) => (fill.style.width = `${Math.round(f * 100)}%`));
          row.remove();
        } catch (err) {
          row.remove();
          toastError(`Could not upload ${file.name}`, err);
        }
      }
      paint();
    },
  });
  container.append(
    head("Publish", "Post your reels and your own videos to TikTok, Instagram and YouTube, all at once.",
      h("a", { class: "btn btn-secondary", href: "#/x/accounts", html: `${icon("link", { size: 18 })}<span class="btn-label">Accounts</span>` })),
    h("div", { class: "stack-lg" }, drop, progress, grid));

  async function paint() {
    let videos = [];
    try {
      videos = await listVideos();
    } catch (err) {
      toastError("Could not load your videos", err);
    }
    grid.removeAttribute("aria-busy");
    if (!videos.length) {
      grid.replaceChildren(h("li", { class: "empty-inline muted" }, "No videos yet. Render a reel, or upload a video above."));
      return;
    }
    grid.replaceChildren(...videos.map((v) => h("li", { class: "project-card" },
      h("a", { class: "project-link", href: `#/x/publish/${encodeURIComponent(v.key)}` },
        h("div", { class: "project-thumb" },
          v.thumb ? h("img", { src: v.thumb, alt: "", loading: "lazy" }) : h("div", { class: "thumb-empty", html: icon("film", { size: 28 }) }),
          h("span", { class: ["badge", v.kind === "reel" && "badge-accent"] }, v.kind === "reel" ? "Made with reelforge" : "Uploaded")),
        h("div", { class: "project-meta" },
          h("h2", { class: "project-title" }, v.title),
          h("p", { class: "project-sub" }, [v.updated ? relativeTime(v.updated) : null,
            v.info && v.info.duration ? formatSeconds(v.info.duration) : null].filter(Boolean).join(" · ")))),
      v.kind === "upload" ? h("div", { class: "project-actions" },
        button("Delete video", { icon: "trash", variant: "ghost", size: "sm", iconOnly: true, class: "danger-hover",
          onClick: async () => {
            if (!(await confirmDialog({ title: "Delete this video?", message: `“${v.title}” is removed from the panel. Posts already published stay online.`, confirmLabel: "Delete", danger: true }))) return;
            await P().retrying(() => P().db.doc(`videos/${v.id}`).delete());
            for (const id of [...((v.store && v.store.ids) || [])]) await P().assets.delete(id).catch(() => {});
            paint();
          } })) : null)));
  }
  paint();
}

// --------------------------------------------------------------------------- Publish: one post

async function trendingSounds(account, filters) {
  const res = await higgsfield("tiktok_music_trending", { connector_id: account.connector_id, limit: 20, ...filters });
  const p = res.payload;
  const items = Array.isArray(p) ? p : (p && (p.tracks || p.music || p.items || p.sounds || p.data || p.results)) || [];
  return items.map((t) => ({
    id: String(t.music_sound_id || t.sound_id || t.music_id || t.id || ""),
    title: t.title || t.name || t.song_name || t.music_name || "Untitled sound",
    artist: t.artist || t.author || t.artist_name || t.owner || "",
    link: t.listen_url || t.play_url || t.share_url || t.link || t.url || t.preview_url || null,
    duration: t.duration || t.duration_seconds || null,
  })).filter((t) => t.id);
}

function soundSection(platform, settings, accounts, save) {
  const box = h("div", { class: "stack" });
  const paintChosen = () => {
    const s = settings.sound;
    chosen.replaceChildren(s && (s.title || s.name)
      ? h("div", { class: "sound-chosen" }, h("span", { html: icon("music", { size: 16 }) }),
          h("span", { class: "sound-text" }, h("strong", {}, s.title || s.name), s.artist ? ` · ${s.artist}` : ""),
          s.link ? h("a", { class: "btn btn-ghost btn-sm", href: s.link, target: "_blank", rel: "noopener" }, "Listen") : null,
          button("Remove", { variant: "ghost", size: "sm", onClick: () => { settings.sound = null; save(); paintChosen(); } }))
      : h("p", { class: "muted small" }, "No sound chosen."));
  };
  const chosen = h("div", {});
  paintChosen();
  const sliders = h("div", { class: "form-grid" },
    slider({ label: "Sound volume", value: settings.sound_volume, help: "0 % = the sound is on the post but can't be heard.",
             onChange: (v) => { settings.sound_volume = v; save(); } }),
    slider({ label: "Your video's own audio", value: settings.original_volume, help: "The voice and music of your video.",
             onChange: (v) => { settings.original_volume = v; save(); } }));

  if (platform === "tiktok") {
    const tiktokAccounts = accounts.filter((a) => a.platform === "tiktok" && a.connector_id);
    const results = h("ul", { class: "sound-list" });
    const genre = h("select", { class: "input select", "aria-label": "Genre" }, GENRES.map((g) => h("option", { value: g }, g.replace(/_/g, " "))));
    const range = h("select", { class: "input select", "aria-label": "Trending over" },
      [["1DAY", "Today"], ["7DAY", "This week"], ["30DAY", "This month"], ["90DAY", "3 months"]].map(([v, l]) => h("option", { value: v, selected: v === "7DAY" }, l)));
    const country = h("input", { class: "input", value: "US", maxlength: 2, "aria-label": "Country code", style: { width: "64px" } });
    const find = button("Find trending sounds", { icon: "search", size: "sm" });
    find.addEventListener("click", async () => {
      const account = tiktokAccounts[0];
      if (!account) return toast("Connect a TikTok account first (Accounts page).", { kind: "error" });
      find.classList.add("is-loading");
      try {
        const filters = { genre: genre.value, date_range: range.value, country_code: country.value.toUpperCase() || "US" };
        const tracks = await trendingSounds(account, filters);
        results.replaceChildren(...(tracks.length ? tracks.map((t) => h("li", { class: "sound-row" },
          h("span", { class: "sound-text" }, h("strong", {}, t.title), t.artist ? ` · ${t.artist}` : "",
            t.duration ? h("span", { class: "muted small" }, ` · ${formatSeconds(t.duration)}`) : null),
          t.link ? h("a", { class: "btn btn-ghost btn-sm", href: t.link, target: "_blank", rel: "noopener" }, "Listen") : null,
          button("Use", { size: "sm", onClick: () => {
            settings.sound = { ...t, ...filters };
            save();
            paintChosen();
            toast(`“${t.title}” chosen for TikTok.`, { kind: "success", timeout: 2000 });
          } }))) : [h("li", { class: "muted small" }, "No sounds found for these filters.")]));
      } catch (err) {
        toastError("Could not load TikTok sounds", err);
      } finally {
        find.classList.remove("is-loading");
      }
    });
    box.append(chosen,
      tiktokAccounts.length
        ? h("div", { class: "row row-wrap sound-filters" }, genre, range, country, find)
        : callout("info", "Connect a TikTok account on the Accounts page to browse TikTok's trending sounds."),
      results, sliders,
      callout("info", "TikTok only lets music be added in its own publish form, which opens in your Claude chat when you publish. Claude shows you this sound and these volumes there so you can set them in one tap."));
  } else {
    const name = textField({ label: "Sound to add", value: settings.sound && settings.sound.name, placeholder: "e.g. the trending audio's name or link",
      onInput: (v) => { settings.sound = v.trim() ? { name: v.trim() } : null; save(); } });
    box.append(name, sliders,
      callout("info", `${PLATFORMS[platform].label} doesn't let apps add its in-app sounds. Add this sound in the app when you post, at the volume set here.`));
  }
  return box;
}

function platformCard(platform, post, accounts, save, repaintBar) {
  const s = post[platform];
  const meta = PLATFORMS[platform];
  const mine = accounts.filter((a) => a.platform === platform);
  const body = h("div", { class: "stack-lg" });
  const sw = switchControl({ id: `on-${platform}`, checked: s.enabled, label: s.enabled ? "On" : "Off", onChange: (v) => {
    s.enabled = v;
    sw.querySelector(".switch-label").textContent = v ? "On" : "Off";
    body.hidden = !v;
    save();
    repaintBar();
  } });
  const acc = mine.length
    ? h("div", { class: "chips", role: "group", "aria-label": `${meta.label} accounts` }, mine.map((a) => {
        const id = `acc-${platform}-${a.id}`;
        const input = h("input", { type: "checkbox", id, class: "visually-hidden", checked: s.accounts.includes(a.id) });
        input.addEventListener("change", () => {
          s.accounts = input.checked ? [...new Set([...s.accounts, a.id])] : s.accounts.filter((x) => x !== a.id);
          save();
          repaintBar();
        });
        return [input, h("label", { class: "chip", for: id }, h("span", { class: "chip-check", html: icon("check", { size: 14 }) }), accountName(a),
          a.status && a.status !== "active" && a.status !== "manual" ? h("span", { class: "chip-note" }, a.status) : null)];
      }))
    : callout("warn", h("span", {}, `No ${meta.label} account yet. `, h("a", { href: "#/x/accounts" }, "Connect one on the Accounts page.")));

  const fields = [];
  if (platform === "youtube") {
    fields.push(textField({ label: "Title", value: s.title, max: 100, placeholder: "Shown under the Short", onInput: (v) => { s.title = v; save(); } }));
  }
  fields.push(textField({ label: platform === "youtube" ? "Description" : "Caption", value: s.caption, multiline: true, max: meta.captionMax,
    placeholder: post.common.caption ? `Same as above: ${post.common.caption.slice(0, 80)}` : "Leave empty to use the shared caption",
    onInput: (v) => { s.caption = v.trim() ? v : null; save(); } }));
  fields.push(textField({ label: platform === "youtube" ? "Tags" : "Hashtags", value: s.hashtags ? tagText(s.hashtags) : "",
    placeholder: post.common.hashtags.length ? `Same as above: ${tagText(post.common.hashtags)}` : "#motivation #quotes",
    onInput: (v) => { s.hashtags = v.trim() ? tagsOf(v) : null; save(); } }));

  const options = h("div", { class: "form-grid" });
  if (platform === "tiktok") {
    options.append(selectField("Who can watch", TIKTOK_PRIVACY, s.privacy, (v) => { s.privacy = v; save(); }),
      selectField("Post", [{ value: "DIRECT_POST", label: "Post now" }, { value: "UPLOAD_TO_DRAFT", label: "Save as a TikTok draft" }], s.mode, (v) => { s.mode = v; save(); }),
      toggle("Allow comments", s.allow_comment, (v) => { s.allow_comment = v; save(); }),
      toggle("Allow duets", s.allow_duet, (v) => { s.allow_duet = v; save(); }),
      toggle("Allow stitches", s.allow_stitch, (v) => { s.allow_stitch = v; save(); }),
      toggle("Label as AI-generated", s.is_aigc, (v) => { s.is_aigc = v; save(); }));
  } else if (platform === "instagram") {
    options.append(toggle("Also show in the feed", s.share_to_feed, (v) => { s.share_to_feed = v; save(); }));
  } else {
    options.append(selectField("Visibility", YT_PRIVACY, s.privacy, (v) => { s.privacy = v; save(); }),
      toggle("Made for kids", s.made_for_kids, (v) => { s.made_for_kids = v; save(); }));
  }

  body.append(
    h("div", { class: "stack" }, h("p", { class: "subsection-title" }, "Accounts"), acc),
    h("div", { class: "stack" }, ...fields),
    options,
    h("div", { class: "stack" }, h("p", { class: "subsection-title" }, "Sound"), soundSection(platform, s, accounts, save)));
  body.hidden = !s.enabled;
  return h("section", { class: ["card", "platform-card", `platform-${platform}`] },
    h("div", { class: "card-head card-head-row" },
      h("div", {}, h("h2", { class: "card-title" }, meta.label),
        h("p", { class: "card-sub" }, mine.length ? `${mine.length} account${mine.length === 1 ? "" : "s"} connected` : "Not connected")),
      sw),
    body);
}

function resultsCard(task) {
  if (!task) return null;
  const rows = Object.entries(task.result || {}).map(([platform, r]) => h("li", { class: ["check", r.status === "done" ? "is-ok" : r.status === "error" ? "is-missing" : "is-optional"] },
    h("span", { class: "check-icon", html: icon(r.status === "done" ? "check" : r.status === "error" ? "alert" : "info", { size: 14 }) }),
    h("span", {}, h("strong", {}, (PLATFORMS[platform] || { label: platform }).label), ` · ${r.message || r.status}`,
      r.url ? [" · ", h("a", { href: r.url, target: "_blank", rel: "noopener" }, "Open post")] : null)));
  const running = ["queued", "running"].includes(task.status);
  return h("section", { class: ["card", "job-card", `job-${task.status}`] },
    h("div", { class: "card-head" }, h("h2", { class: "card-title" },
      running ? "Publishing…" : task.status === "error" ? "Publishing stopped"
        : Object.values(task.result || {}).every((r) => r.status === "done") ? "Published" : "Check each platform"),
      h("p", { class: "card-sub", role: "status", "aria-live": "polite" }, task.message || "")),
    rows.length ? h("ul", { class: "checklist" }, rows) : null,
    task.error ? callout("error", task.error) : null);
}

async function renderComposer(container, key) {
  const { db, retrying, newTask, nowIso } = P();
  container.append(h("p", {}, h("a", { class: "btn btn-ghost btn-sm", href: "#/x/publish", html: `${icon("chevronLeft", { size: 16 })}<span class="btn-label">All videos</span>` })));
  const [videos, accounts, snap] = await Promise.all([listVideos(), listAccounts(), retrying(() => db.doc(`posts/${key}`).get())]);
  const video = videos.find((v) => v.key === key);
  if (!video) {
    container.append(callout("error", "This video is no longer in the panel."));
    return null;
  }
  const [kind, id] = key.split(":");
  const post = snap.exists ? { ...newPost({ kind, id }), ...snap.data() } : newPost({ kind, id });
  for (const p of ORDER) post[p] = { ...newPost({ kind, id })[p], ...(post[p] || {}) };
  if (!snap.exists) {
    // start with every connected account ticked
    for (const p of ORDER) post[p].accounts = accounts.filter((a) => a.platform === p).map((a) => a.id);
    post.youtube.title = video.title;
  }
  const save = debounce(() => retrying(() => db.doc(`posts/${key}`).set({ ...post, updated: nowIso() }))
    .catch((err) => toastError("Could not save the post settings", err)), 600);

  const player = h("video", { class: "result-video", controls: true, playsinline: true, preload: "metadata", poster: video.thumb || null });
  videoUrl(video).then((url) => url && (player.src = url));
  const common = card("For every platform", "Each platform below uses this unless you write something else there.",
    h("div", { class: "stack" },
      textField({ label: "Caption", value: post.common.caption, multiline: true, rows: 4, max: 2200, placeholder: "What the post says",
        onInput: (v) => { post.common.caption = v; save(); } }),
      textField({ label: "Hashtags", value: tagText(post.common.hashtags), placeholder: "#motivation #mindset #quotes",
        help: "Separate with spaces or commas; the # is optional.", onInput: (v) => { post.common.hashtags = tagsOf(v); save(); } })));

  const bar = h("div", { class: "publish-bar" });
  const results = h("div", {});
  const publishBtn = button("Publish", { icon: "upload", variant: "primary", size: "lg" });
  const targets = () => ORDER.filter((p) => post[p].enabled && post[p].accounts.length);
  const repaintBar = () => {
    const t = targets();
    const n = t.reduce((sum, p) => sum + post[p].accounts.length, 0);
    publishBtn.querySelector(".btn-label").textContent = t.length ? `Publish to ${t.map((p) => PLATFORMS[p].label).join(", ")}` : "Publish";
    publishBtn.disabled = !t.length;
    summary.textContent = t.length ? `${n} account${n === 1 ? "" : "s"} in one go.` : "Turn on a platform and pick an account.";
  };
  const summary = h("span", { class: "muted small" });
  bar.append(summary, publishBtn);

  let unsub = null;
  let reveal = false; // scroll to the results once the new task shows up
  const watch = (taskId) => {
    if (unsub) unsub();
    unsub = db.doc(`tasks/${taskId}`).onSnapshot((s) => {
      results.replaceChildren(resultsCard(s.exists ? s.data() : null) || "");
      if (reveal && s.exists) {
        reveal = false;
        results.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }, () => {});
  };
  if (post.last_task) watch(post.last_task);

  publishBtn.addEventListener("click", async () => {
    const t = targets();
    if (t.includes("youtube") && !post.youtube.title.trim()) return toast("YouTube needs a title.", { kind: "error" });
    const missingCaption = !post.common.caption.trim() && t.some((p) => !post[p].caption);
    if (missingCaption && !(await confirmDialog({ title: "Publish without a caption?", message: "Some platforms have no caption. Publish anyway?", confirmLabel: "Publish" }))) return;
    publishBtn.classList.add("is-loading");
    try {
      const { id: taskId, problem } = await newTask("publish", null, { post_id: key, platforms: t, video: { kind, id, title: video.title } });
      post.last_task = taskId;
      await retrying(() => db.doc(`posts/${key}`).set({ ...post, updated: nowIso() }));
      reveal = true;
      watch(taskId);
      toast(problem ? "Saved. Claude wasn't reached: send “publish my video” in your Claude chat." : "Sent to Claude: publishing now.", { kind: problem ? "error" : "success" });
    } catch (err) {
      toastError("Could not start publishing", err);
    } finally {
      publishBtn.classList.remove("is-loading");
    }
  });

  container.append(
    head(video.title, video.kind === "reel" ? "Made with reelforge" : "Uploaded video"),
    h("div", { class: "stack-lg" },
      h("div", { class: "result-grid" }, h("div", { class: "result-frame", style: { aspectRatio: "9 / 16" } }, player),
        h("div", { class: "stack-lg" }, common,
          callout("info", "Publishing is done by Claude in your chat: TikTok asks you to confirm each post there. Instagram and YouTube need a social-media connector in Claude (for example Metricool).")) ),
      ...ORDER.map((p) => platformCard(p, post, accounts, save, repaintBar)),
      results,
      bar));
  repaintBar();
  return () => unsub && unsub();
}

// --------------------------------------------------------------------------- Accounts

async function syncTiktok() {
  const res = await higgsfield("tiktok_accounts");
  const list = (res.payload && res.payload.accounts) || [];
  const { db, retrying, nowIso } = P();
  for (const a of list) {
    const id = `tiktok-${a.connector_id}`;
    const snap = await retrying(() => db.doc(`accounts/${id}`).get());
    const old = snap.exists ? snap.data() : { profile: {} };
    await retrying(() => db.doc(`accounts/${id}`).set({
      ...old, platform: "tiktok", connector_id: a.connector_id, status: a.status || "active",
      handle: old.handle || a.username || a.display_name || a.name || a.label || "",
      label: a.name || a.label || "", updated: nowIso() }));
  }
  return list.length;
}

function accountCard(a, refresh) {
  const pr = a.profile || {};
  const meta = PLATFORMS[a.platform];
  const pic = pr.picture ? h("img", { src: P().blobUrl(pr.picture), alt: "" }) : h("span", {}, accountName(a).replace("@", "").slice(0, 1).toUpperCase());
  const actions = h("div", { class: "row row-wrap account-actions" },
    pr.bio ? button("Copy bio", { icon: "copy", size: "sm", onClick: async () => toast((await copyText(pr.bio)) ? "Bio copied." : "Copy failed.", { kind: "success", timeout: 1500 }) }) : null,
    pr.picture ? button("Save picture", { icon: "download", size: "sm", onClick: () => globalThis.reelforgeSaveFile(P().blobUrl(pr.picture), `${(a.handle || "profile").replace(/\W+/g, "")}-picture.jpg`) }) : null,
    h("a", { class: "btn btn-ghost btn-sm", href: meta.settings(a), target: "_blank", rel: "noopener", html: `${icon("link", { size: 16 })}<span class="btn-label">Open profile settings</span>` }),
    a.platform === "tiktok" && a.status === "error" ? button("Reconnect", { icon: "refresh", size: "sm", onClick: () => connectTiktok(a.connector_id, refresh) }) : null,
    button("Remove", { icon: "trash", variant: "ghost", size: "sm", class: "danger-hover", onClick: async () => {
      if (!(await confirmDialog({ title: `Remove ${accountName(a)}?`, message: "It's removed from the panel only. Nothing changes on the platform.", confirmLabel: "Remove", danger: true }))) return;
      await P().retrying(() => P().db.doc(`accounts/${a.id}`).delete());
      refresh();
    } }));
  return h("li", { class: "account-card" },
    h("div", { class: "account-pic", "aria-hidden": "true" }, pic),
    h("div", { class: "account-main" },
      h("div", { class: "account-name" }, h("strong", {}, pr.name || accountName(a)), " ",
        h("span", { class: "muted small" }, `${meta.label} · ${accountName(a)}`),
        a.status && a.status !== "manual" ? h("span", { class: ["badge", a.status === "active" && "badge-accent"] }, a.status) : null),
      pr.bio ? h("p", { class: "account-bio" }, pr.bio) : h("p", { class: "muted small" }, "No bio saved yet."),
      pr.link ? h("p", { class: "small" }, pr.link) : null,
      actions));
}

async function connectTiktok(connectorId, refresh) {
  try {
    const res = connectorId
      ? await higgsfield("tiktok_reconnect", { connector_id: connectorId })
      : await higgsfield("tiktok_connect", (await listAccounts()).some((a) => a.platform === "tiktok") ? { name: `tiktok-${Date.now().toString(36)}` } : {});
    const url = res.payload && (res.payload.authorize_url || res.payload.url);
    if (!url) throw new Error("Higgsfield didn't return a TikTok sign-in link.");
    await dialog({
      title: "Approve on TikTok",
      body: h("div", { class: "stack" },
        h("p", { class: "dialog-text" }, "Open TikTok, approve access for Higgsfield, then come back and press “Check accounts”. The link works for about 10 minutes."),
        h("a", { class: "btn btn-primary", href: url, target: "_blank", rel: "noopener" }, "Open TikTok")),
      actions: [{ label: "Check accounts", value: true, variant: "secondary", submit: true }],
    });
    const n = await syncTiktok();
    toast(n ? `${n} TikTok account${n === 1 ? "" : "s"} connected.` : "No TikTok account yet. Finish the approval on TikTok, then check again.", { kind: n ? "success" : "error" });
    refresh();
  } catch (err) {
    toastError("Could not connect TikTok", err);
  }
}

async function addManualAccount(platform, refresh) {
  const input = h("input", { class: "input", name: "handle", placeholder: "@yourname", autocomplete: "off", required: true });
  const handle = await dialog({
    title: `Add a ${PLATFORMS[platform].label} account`,
    body: h("div", { class: "stack" }, h("label", { class: "field-label" }, "Username", input),
      h("p", { class: "muted small" }, "Posting needs a social-media connector in Claude (for example Metricool) linked to this account.")),
    actions: [{ label: "Cancel", value: null, variant: "ghost" }, { label: "Add", value: () => input.value.trim().replace(/^@/, ""), variant: "primary", submit: true }],
    initialFocus: "input",
  });
  if (!handle) return;
  const { db, retrying, nowIso, hex } = P();
  await retrying(() => db.doc(`accounts/${platform}-${hex(4)}`).set({ platform, handle, status: "manual", profile: {}, created: nowIso() }));
  refresh();
}

function profileEditor(accounts, refresh) {
  const chosen = new Set(accounts.map((a) => a.id));
  const values = { name: null, bio: null, link: null, picture: null };
  const strictest = () => Math.min(...accounts.filter((a) => chosen.has(a.id)).map((a) => PLATFORMS[a.platform].bioMax), 1000);
  const bioHelp = h("p", { class: "field-help" });
  const paintHelp = () => (bioHelp.textContent = `Up to ${strictest()} characters for the accounts selected (TikTok 80, Instagram 150, YouTube 1000). Empty fields are left as they are.`);
  const picks = h("div", { class: "chips", role: "group", "aria-label": "Accounts to change" }, accounts.map((a) => {
    const id = `pf-${a.id}`;
    const input = h("input", { type: "checkbox", id, class: "visually-hidden", checked: true });
    input.addEventListener("change", () => {
      if (input.checked) chosen.add(a.id);
      else chosen.delete(a.id);
      paintHelp();
    });
    return [input, h("label", { class: "chip", for: id }, h("span", { class: "chip-check", html: icon("check", { size: 14 }) }),
      `${PLATFORMS[a.platform].label} ${accountName(a)}`)];
  }));
  const picName = h("span", { class: "muted small" }, "No new picture");
  const pic = dropzone({ accept: "image/*", compact: true, iconName: "image", title: "New profile picture", hint: "Square JPG or PNG works everywhere",
    onFiles: async ([file]) => {
      try {
        const type = ["image/png", "image/jpeg", "image/webp"].includes(file.type) ? file.type : "image/jpeg";
        const res = await P().uploadAsset(file, type);
        values.picture = res.id;
        picName.textContent = `${file.name} ready`;
      } catch (err) {
        toastError("Could not add the picture", err);
      }
    } });
  const applyBtn = button("Save to selected accounts", { icon: "check", variant: "primary" });
  applyBtn.addEventListener("click", async () => {
    const patch = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== null && v !== ""));
    if (!chosen.size || !Object.keys(patch).length) return toast("Pick accounts and fill in at least one field.", { kind: "error" });
    const tooLong = patch.bio && accounts.filter((a) => chosen.has(a.id) && patch.bio.length > PLATFORMS[a.platform].bioMax);
    if (tooLong && tooLong.length && !(await confirmDialog({ title: "Bio too long for some accounts", message: `${tooLong.map((a) => `${PLATFORMS[a.platform].label} ${accountName(a)}`).join(", ")} allow fewer characters. Save anyway?`, confirmLabel: "Save" }))) return;
    applyBtn.classList.add("is-loading");
    try {
      for (const a of accounts.filter((x) => chosen.has(x.id))) {
        await P().retrying(() => P().db.doc(`accounts/${a.id}`).update({ profile: { ...(a.profile || {}), ...patch }, updated: P().nowIso() }));
      }
      toast(`Saved to ${chosen.size} account${chosen.size === 1 ? "" : "s"}. Copy or save it into each app below.`, { kind: "success" });
      refresh();
    } catch (err) {
      toastError("Could not save the profiles", err);
    } finally {
      applyBtn.classList.remove("is-loading");
    }
  });
  paintHelp();
  return card("Edit profiles", "Change one account or all of them at once: tick the accounts, fill in what should change.",
    h("div", { class: "stack-lg" },
      picks,
      h("div", { class: "form-grid" },
        textField({ label: "Display name", placeholder: "Leave empty to keep", onInput: (v) => (values.name = v.trim() || null) }),
        textField({ label: "Link in bio", placeholder: "https://…", onInput: (v) => (values.link = v.trim() || null) })),
      h("div", { class: "field" }, textField({ label: "Bio", multiline: true, rows: 3, placeholder: "Leave empty to keep", onInput: (v) => (values.bio = v.trim() || null) }), bioHelp),
      h("div", { class: "stack" }, pic, picName),
      h("div", { class: "row" }, applyBtn),
      callout("info", "TikTok, Instagram and YouTube don't let apps change your profile picture or bio, so the panel keeps them here. After saving, use “Copy bio” and “Save picture” on each account, then “Open profile settings” to paste them in.")));
}

function renderAccounts(container) {
  const body = h("div", { class: "stack-lg" });
  container.append(head("Accounts", "Your TikTok, Instagram and YouTube accounts, and their profiles."), body);
  async function paint() {
    let accounts = [];
    try {
      accounts = await listAccounts();
    } catch (err) {
      toastError("Could not load your accounts", err);
    }
    const lists = ORDER.map((platform) => {
      const mine = accounts.filter((a) => a.platform === platform);
      const actions = platform === "tiktok"
        ? [button("Connect TikTok", { icon: "plus", variant: "primary", size: "sm", onClick: () => connectTiktok(null, paint) }),
           button("Check accounts", { icon: "refresh", variant: "ghost", size: "sm", onClick: async () => {
             try {
               const n = await syncTiktok();
               toast(`${n} TikTok account${n === 1 ? "" : "s"} connected.`, { kind: "success", timeout: 2000 });
               paint();
             } catch (err) {
               toastError("Could not check TikTok accounts", err);
             }
           } })]
        : [button(`Add ${PLATFORMS[platform].label} account`, { icon: "plus", size: "sm", onClick: () => addManualAccount(platform, paint) })];
      return h("section", { class: "card" },
        h("div", { class: "card-head card-head-row" },
          h("div", {}, h("h2", { class: "card-title" }, PLATFORMS[platform].label),
            h("p", { class: "card-sub" }, platform === "tiktok" ? "Connected through your Higgsfield account." : "Posting needs a social-media connector in Claude, such as Metricool.")),
          h("div", { class: "row" }, actions)),
        mine.length ? h("ul", { class: "account-list" }, mine.map((a) => accountCard(a, paint))) : h("p", { class: "muted" }, "No accounts yet."));
    });
    body.replaceChildren(accounts.length ? profileEditor(accounts, paint) : callout("info", "Connect or add an account to manage its profile here."), ...lists);
  }
  paint();
}

// --------------------------------------------------------------------------- registration

export const extras = {
  nav: [{ id: "publish", label: "Publish", icon: "upload" }, { id: "accounts", label: "Accounts", icon: "link" }],
  render(id, sub, container) {
    if (id === "accounts") return renderAccounts(container);
    if (id === "publish" && sub) {
      let cleanup = null;
      renderComposer(container, sub).then((fn) => (cleanup = fn)).catch((err) => {
        clear(container);
        container.append(callout("error", `Could not open this post: ${err.message || err}`));
      });
      return () => cleanup && cleanup();
    }
    return renderLibrary(container);
  },
};
