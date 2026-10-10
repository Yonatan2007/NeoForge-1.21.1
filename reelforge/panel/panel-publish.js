/**
 * Control panel pages for posting: Publish (your videos, and one post per
 * video with a tab per platform, a phone preview of how it will look, a
 * caption writer, the best times to post and checks before it goes out) and
 * Accounts (how each platform is connected, the accounts, and their profiles
 * edited one at a time or many at once). Calendar and Stats live in
 * panel-growth.js, the stock clip review in panel-review.js.
 *
 *   videos/<id>       your own uploaded videos {title, store, thumb, info, size, created}
 *   posts/<key>       post settings per video (key "reel:<project id>" or "video:<id>")
 *   accounts/<id>     {platform, handle, connector_id?, status, profile: {name, bio, link, picture}}
 *   settings/claude   which Claude sessions handle requests, and what they can reach
 *   settings/profile  {last_task}: the latest profile change request
 *   tasks/<id>        kind "publish" or "profile": Claude does it and writes results back
 *
 * TikTok accounts are connected and read through the Higgsfield connector
 * (which also lists TikTok's trending licensed sounds). Instagram and YouTube
 * posting goes through a social-media connector added in Claude (Metricool).
 * Profile pictures and bios have no API on any of the three: a Claude session
 * on the user's computer changes them in the user's own browser.
 */

import { api } from "./api.js";
import { callout, dropzone } from "./components.js";
import {
  button, clear, confirmDialog, copyText, debounce, dialog, formatBytes, formatSeconds, h, relativeTime, toast, toastError,
} from "./dom.js";
import { paintRange, switchControl } from "./fields.js";
import { icon } from "./icons.js";
import { bestTimePicker, canWrite, renderCalendar, renderStats, sampleProblem, viewerTz, writePost } from "./panel-growth.js";
import { checkTiktok, coverCard, markTiktokPosted, renderBatch, renderIdeas, viralityCard } from "./panel-more.js";
import { footageReview } from "./panel-review.js";

const strip = (handle) => String(handle || "").replace(/^@/, "");
const PLATFORMS = {
  tiktok: { label: "TikTok", surface: "TikTok", captionMax: 2200, bioMax: 80,
            settings: (a) => (a && a.handle ? `https://www.tiktok.com/@${strip(a.handle)}` : "https://www.tiktok.com/") },
  instagram: { label: "Instagram", surface: "Reels", captionMax: 2200, hashtagMax: 30, bioMax: 150,
               settings: () => "https://www.instagram.com/accounts/edit/" },
  youtube: { label: "YouTube", surface: "Shorts", titleMax: 100, captionMax: 5000, bioMax: 1000,
             settings: () => "https://studio.youtube.com/" },
};
const ORDER = ["tiktok", "instagram", "youtube"];
const TIKTOK_PRIVACY = [
  { value: "PUBLIC_TO_EVERYONE", label: "Everyone" }, { value: "FOLLOWER_OF_CREATOR", label: "Followers" },
  { value: "MUTUAL_FOLLOW_FRIENDS", label: "Friends" }, { value: "SELF_ONLY", label: "Only me" },
];
const YT_PRIVACY = [{ value: "public", label: "Public" }, { value: "unlisted", label: "Unlisted" }, { value: "private", label: "Private" }];
const GENRES = ["ALL", "POP", "HIP_HOP/RAP", "LO-FI", "EDM", "COUNTRY", "K-POP", "CHILL_BEATS", "EPIC"];
const CONNECTORS_URL = "https://claude.ai/customize/connectors";

const P = () => api._panel;
const higgsfield = (tool, input) => P().mcp.callTool("higgsfield", tool, input || {});

// Line icons for the phone preview (drawn for this page; the platforms' own marks are not used).
const UI = {
  heart: '<path d="M12 20.5s-7.5-4.6-7.5-10.4A4.3 4.3 0 0 1 12 7.4a4.3 4.3 0 0 1 7.5 2.7c0 5.8-7.5 10.4-7.5 10.4Z"/>',
  comment: '<path d="M20.5 11.5a8.5 8.5 0 0 1-12.4 7.6L3.5 20.5l1.4-4.4A8.5 8.5 0 1 1 20.5 11.5Z"/>',
  share: '<path d="M13.5 4.5 21 12l-7.5 7.5v-4.3C8.3 15.2 5 16.8 3 20.5c.6-6 4-10.2 10.5-10.8Z"/>',
  bookmark: '<path d="M6.5 3.5h11v17l-5.5-4-5.5 4Z"/>',
  send: '<path d="M21.5 3 3 10.2l7.3 3 3 7.3Z"/><path d="m10.3 13.2 4.5-4.5"/>',
  like: '<path d="M7.5 10.5v10h-3v-10Z"/><path d="M7.5 10.5 11 3.6a1.8 1.8 0 0 1 3.3 1.3l-.9 4.6h5.3a2 2 0 0 1 2 2.4l-1.4 6.5a2 2 0 0 1-2 1.6H7.5"/>',
  note: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
  dots: '<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>',
  mute: '<path d="M11 5 6 9H3v6h3l5 4Z"/><path d="m22 9-6 6M16 9l6 6"/>',
  remix: '<path d="M4 7h11a4 4 0 0 1 0 8H9"/><path d="m7 4-3 3 3 3"/><path d="M20 17H9"/>',
};
const ui = (name, size = 26, style = "") => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"${style ? ` style="${style}"` : ""}>${UI[name]}</svg>`;

function newPost(video) {
  const sound = { sound: null, sound_volume: 0, original_volume: 100 };
  const when = { when: "now", at: null, tz: null }; // Metricool can post later; TikTok posts right away
  return {
    video,
    common: { caption: "", hashtags: [] },
    tiktok: { enabled: true, accounts: [], caption: null, hashtags: null, privacy: "PUBLIC_TO_EVERYONE",
              allow_comment: true, allow_duet: true, allow_stitch: true, is_aigc: true, mode: "DIRECT_POST", ...sound },
    instagram: { enabled: true, accounts: [], caption: null, hashtags: null, share_to_feed: true, ...when, ...sound },
    youtube: { enabled: true, accounts: [], title: "", caption: null, hashtags: null, privacy: "public",
               made_for_kids: false, ...when, ...sound },
  };
}

const tagsOf = (text) => [...new Set(String(text || "").split(/[\s,]+/).map((t) => t.replace(/^#+/, "").trim()).filter(Boolean))];
const tagText = (tags) => (tags || []).map((t) => `#${t}`).join(" ");
const accountName = (a) => (a ? (a.handle ? `@${strip(a.handle)}` : a.label || PLATFORMS[a.platform].label) : "");
const platformDot = (p) => h("span", { class: `pdot pdot-${p}`, "aria-hidden": "true" });

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
  if (panel.kindOf(file) !== "video") throw new Error(`“${file.name}” isn't a video.`);
  const { info, thumb } = await panel.probe(file, "video");
  const store = await panel.storeFile(file, panel.mimeOf(file), onProgress, () => {});
  const thumbRes = thumb ? await panel.uploadAsset(thumb, "image/jpeg").catch(() => null) : null;
  const id = `v-${Date.now().toString(36)}-${panel.hex(3)}`;
  await panel.retrying(() => panel.db.doc(`videos/${id}`).set({
    title: panel.safeName(file.name).replace(/\.[^.]+$/, ""), store, thumb: thumbRes ? thumbRes.id : null,
    info, size: file.size, created: panel.nowIso() }));
  return id;
}

/** {label, tone} for a post's latest publish task. */
function publishState(task) {
  if (!task) return { label: "Not posted", tone: "idle" };
  if (["queued", "running"].includes(task.status)) return { label: "Publishing", tone: "busy" };
  if (task.status === "error") return { label: "Failed", tone: "bad" };
  const results = Object.values(task.result || {});
  if (results.length && results.every((r) => r.status === "done")) return { label: "Posted", tone: "ok" };
  if (results.length && results.every((r) => ["done", "scheduled"].includes(r.status))) return { label: "Scheduled", tone: "ok" };
  if (results.some((r) => r.status === "waiting")) return { label: "Needs you", tone: "warn" };
  return { label: "Check results", tone: "warn" };
}

// --------------------------------------------------------------------------- small UI pieces

function head(title, lead, ...actions) {
  return h("header", { class: "home-head" },
    h("div", {}, h("h1", { class: "page-title" }, title), lead ? h("p", { class: "page-lead" }, lead) : null),
    actions.length ? h("div", { class: "row" }, actions) : null);
}

let fieldSeq = 0;
function textField({ label, value, placeholder, onInput, multiline = false, max, rows = 3, help }) {
  const id = `pf-field-${++fieldSeq}`;
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
    input, help ? (help instanceof Node ? help : h("p", { class: "field-help" }, help)) : null);
}

function slider({ label, value, onChange, help }) {
  const id = `pf-range-${++fieldSeq}`;
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
  return h("div", { class: "field field-bool" }, switchControl({ id: `pf-sw-${++fieldSeq}`, checked, label, onChange }));
}

function selectField(label, options, value, onChange) {
  const id = `pf-sel-${++fieldSeq}`;
  const sel = h("select", { id, class: "input select" },
    options.map((o) => h("option", { value: o.value, selected: o.value === value }, o.label)));
  sel.addEventListener("change", () => onChange(sel.value));
  return h("div", { class: "field" }, h("label", { class: "field-label", for: id }, label), sel);
}

const WHEN = [{ value: "now", label: "As soon as possible" }, { value: "best", label: "At the best time (Metricool picks)" },
              { value: "at", label: "At a time I choose" }];
const localNow = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
const whenText = (s) => (s.when === "best" ? "best time" : s.when === "at" && s.at
  ? new Date(s.at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : null);

function whenField(s, save) {
  const at = h("input", { class: "input", type: "datetime-local", value: s.at || "", min: localNow(), "aria-label": "Date and time to post" });
  const field = selectField("When", WHEN, s.when, (v) => {
    s.when = v;
    at.hidden = v !== "at";
    save();
  });
  at.hidden = s.when !== "at";
  at.addEventListener("change", () => {
    s.at = at.value || null;
    s.tz = viewerTz();
    save();
  });
  field.append(at);
  /** Post at `value` (a datetime-local value), e.g. a best time picked from Metricool. */
  field.setAt = (value) => {
    s.when = "at";
    s.at = value;
    s.tz = viewerTz();
    field.querySelector("select").value = "at";
    at.hidden = false;
    at.value = value;
    save();
  };
  return field;
}

const pill = ({ label, tone }) => h("span", { class: ["state-pill", `is-${tone}`] }, label);

// --------------------------------------------------------------------------- Publish: video library

function renderLibrary(container) {
  const grid = h("ul", { class: "project-grid", "aria-busy": "true" });
  const progress = h("div", { class: "stack" });
  const drop = dropzone({
    accept: "video/*", multiple: true, iconName: "upload",
    title: "Upload your own videos", hint: "MP4, MOV or WebM from your phone or computer. They sit next to the reels you made, ready to post.",
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
    head("Publish", "Pick a video, write the post once, adjust it per platform, and send it to TikTok, Instagram and YouTube together.",
      h("a", { class: "btn btn-secondary", href: "#/x/accounts", html: `${icon("link", { size: 18 })}<span class="btn-label">Accounts</span>` })),
    h("div", { class: "stack-lg" }, drop, progress,
      h("h2", { class: "section-title" }, "Your videos"), grid));

  async function paint() {
    let videos = [];
    let posts = new Map();
    try {
      const [v, snap] = await Promise.all([listVideos(), P().retrying(() => P().db.collection("posts").get())]);
      videos = v;
      posts = new Map(snap.docs.map((d) => [d.id, d.data()]));
    } catch (err) {
      toastError("Could not load your videos", err);
    }
    grid.removeAttribute("aria-busy");
    if (!videos.length) {
      grid.replaceChildren(h("li", { class: "empty-inline muted" }, "No videos yet. Render a reel, or upload a video above."));
      return;
    }
    const tasks = new Map();
    await Promise.all([...posts.values()].filter((p) => p.last_task).map(async (p) => {
      const s = await P().retrying(() => P().db.doc(`tasks/${p.last_task}`).get()).catch(() => null);
      if (s && s.exists) tasks.set(p.last_task, s.data());
    }));
    grid.replaceChildren(...videos.map((v) => {
      const post = posts.get(v.key);
      const state = publishState(post && post.last_task ? tasks.get(post.last_task) : null);
      return h("li", { class: "project-card" },
        h("a", { class: "project-link", href: `#/x/publish/${encodeURIComponent(v.key)}` },
          h("div", { class: "project-thumb" },
            v.thumb ? h("img", { src: v.thumb, alt: "", loading: "lazy" }) : h("div", { class: "thumb-empty", html: icon("film", { size: 28 }) }),
            h("span", { class: ["badge", "badge-dark"] }, v.kind === "reel" ? "Made here" : "Uploaded"),
            h("span", { class: "card-state" }, pill(state))),
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
            } })) : null);
    }));
  }
  paint();
}

// --------------------------------------------------------------------------- Publish: phone preview

const fmtTags = (tags) => (tags && tags.length ? tags.map((t) => `#${t}`).join(" ") : "");

function soundLine(platform, s, handle) {
  const named = s.sound && (s.sound.title || s.sound.name);
  const text = named ? `${s.sound.title || s.sound.name}${s.sound.artist ? ` · ${s.sound.artist}` : ""}` : `${handle || "you"} · original sound`;
  const muted = named && s.sound_volume === 0;
  return h("div", { class: "pui-sound" },
    h("span", { html: ui("note", 14) }),
    h("span", { class: "pui-marquee" }, h("span", { class: "pui-track" }, h("span", {}, text), h("span", { "aria-hidden": "true" }, text))),
    muted ? h("span", { class: "pui-muted", html: `${ui("mute", 12)}<span>muted</span>` }) : null);
}

function overlayFor(platform, post, accounts) {
  const s = post[platform];
  const caption = (s.caption ?? post.common.caption) || "";
  const tags = fmtTags(s.hashtags ?? post.common.hashtags);
  const account = accounts.find((a) => s.accounts.includes(a.id));
  const handle = account ? accountName(account) : "@yourname";
  const initial = strip(handle).slice(0, 1).toUpperCase() || "Y";
  const act = (name, label, style) => h("span", { class: "pui-act" }, h("span", { html: ui(name, 26, style) }), label ? h("span", {}, label) : null);
  const off = !s.enabled ? h("div", { class: "pui-off" }, `${PLATFORMS[platform].label} is off for this post`) : null;
  if (platform === "tiktok") {
    return [
      h("div", { class: "pui-top" }, h("span", { class: "muted-on-video" }, "Following"), h("strong", {}, "For You")),
      h("div", { class: "pui-rail" },
        h("span", { class: "pui-avatar" }, initial, h("span", { class: "pui-plus" }, "+")),
        act("heart", "24.1K"), act("comment", "318"), act("bookmark", "2.4K"), act("share", "190"),
        h("span", { class: "pui-disc", "aria-hidden": "true" })),
      h("div", { class: "pui-bottom" },
        h("strong", { class: "pui-handle" }, handle),
        h("p", { class: "pui-caption" }, caption || h("span", { class: "pui-placeholder" }, "Your caption"), tags ? h("b", {}, ` ${tags}`) : null),
        soundLine(platform, s, handle)),
      off];
  }
  if (platform === "instagram") {
    return [
      h("div", { class: "pui-top" }, h("strong", {}, "Reels")),
      h("div", { class: "pui-rail" }, act("heart", "24.1K"), act("comment", "318"), act("send", "190"), act("dots"),
        h("span", { class: "pui-square", "aria-hidden": "true" })),
      h("div", { class: "pui-bottom" },
        h("div", { class: "pui-who" }, h("span", { class: "pui-avatar sm" }, initial), h("strong", {}, strip(handle)), h("span", { class: "pui-follow" }, "Follow")),
        h("p", { class: "pui-caption one" }, caption || h("span", { class: "pui-placeholder" }, "Your caption"), tags ? h("b", {}, ` ${tags}`) : null),
        soundLine(platform, s, strip(handle))),
      off];
  }
  return [
    h("div", { class: "pui-top" }, h("strong", {}, "Shorts")),
    h("div", { class: "pui-rail" }, act("like", "24K"), act("like", "Dislike", "transform:rotate(180deg)"), act("comment", "318"),
      act("share", "Share"), act("remix", "Remix")),
    h("div", { class: "pui-bottom" },
      h("div", { class: "pui-who" }, h("span", { class: "pui-avatar sm" }, initial), h("strong", {}, handle), h("span", { class: "pui-follow is-light" }, "Subscribe")),
      h("p", { class: "pui-caption one" }, s.title || caption || h("span", { class: "pui-placeholder" }, "Your title")),
      soundLine(platform, s, strip(handle))),
    off];
}

function phonePreview(video) {
  const player = h("video", { class: "phone-video", muted: true, loop: true, playsinline: true, preload: "metadata", poster: video.thumb || null });
  videoUrl(video).then((url) => url && (player.src = url));
  const overlay = h("div", { class: "phone-ui" });
  const playBtn = h("button", { type: "button", class: "phone-play", "aria-label": "Play the preview", html: icon("play", { size: 22 }) });
  const screen = h("div", { class: "phone-screen" }, player, overlay, playBtn);
  playBtn.addEventListener("click", () => (player.paused ? player.play().catch(() => {}) : player.pause()));
  player.addEventListener("play", () => playBtn.classList.add("is-playing"));
  player.addEventListener("pause", () => playBtn.classList.remove("is-playing"));
  return {
    el: h("div", { class: "phone" }, screen),
    player,
    render(platform, post, accounts) {
      overlay.dataset.platform = platform;
      overlay.replaceChildren(...overlayFor(platform, post, accounts).filter(Boolean));
    },
  };
}

// --------------------------------------------------------------------------- Publish: one post

async function trendingSounds(account, filters) {
  const res = await higgsfield("tiktok_music_trending", { connector_id: account.connector_id, limit: 30, ...filters });
  const p = res.payload;
  const items = Array.isArray(p) ? p : (p && (p.tracks || p.music || p.items || p.sounds || p.data || p.results)) || [];
  // Higgsfield answers {tracks: [{song_clip_id, name, artist, duration_sec, preview_url, rank}]}
  return items.map((t) => {
    const title = t.title || t.name || t.song_name || t.music_name || "Untitled sound";
    const artist = t.artist || t.author || t.artist_name || t.owner || "";
    return {
      id: String(t.song_clip_id || t.music_sound_id || t.sound_id || t.music_id || t.id || `${title}|${artist}`),
      title, artist, rank: t.rank || null,
      link: t.preview_url || t.listen_url || t.play_url || t.share_url || t.link || t.url || null,
      duration: t.duration_sec || t.duration || t.duration_seconds || null,
    };
  });
}

function soundSection(platform, settings, accounts, save) {
  const box = h("div", { class: "stack" });
  const chosen = h("div", {});
  const paintChosen = () => {
    const s = settings.sound;
    chosen.replaceChildren(s && (s.title || s.name)
      ? h("div", { class: "sound-chosen" }, h("span", { class: "sound-icon", html: icon("music", { size: 16 }) }),
          h("span", { class: "sound-text" }, h("strong", {}, s.title || s.name), s.artist ? ` · ${s.artist}` : ""),
          s.link ? h("a", { class: "btn btn-ghost btn-sm", href: s.link, target: "_blank", rel: "noopener" }, "Listen") : null,
          button("Remove", { variant: "ghost", size: "sm", onClick: () => { settings.sound = null; save(); paintChosen(); } }))
      : h("p", { class: "muted small" }, "No sound chosen: the video keeps only its own audio."));
  };
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
    const country = h("input", { class: "input", value: "US", maxlength: 2, "aria-label": "Country code", style: { width: "70px" } });
    const find = button("Find trending sounds", { icon: "search", size: "sm", variant: "secondary" });
    find.addEventListener("click", async () => {
      const account = tiktokAccounts[0];
      if (!account) return toast("Connect a TikTok account first (Accounts page).", { kind: "error" });
      find.classList.add("is-loading");
      try {
        const filters = { genre: genre.value, date_range: range.value, country_code: country.value.toUpperCase() || "US" };
        const tracks = await trendingSounds(account, filters);
        results.replaceChildren(...(tracks.length ? tracks.map((t) => h("li", { class: "sound-row" },
          h("span", { class: "sound-icon", html: icon("music", { size: 16 }) }),
          h("span", { class: "sound-text" }, h("strong", {}, t.title), t.artist ? ` · ${t.artist}` : "",
            t.duration ? h("span", { class: "muted small timecode" }, ` ${formatSeconds(t.duration)}`) : null),
          t.link ? h("a", { class: "btn btn-ghost btn-sm", href: t.link, target: "_blank", rel: "noopener" }, "Listen") : null,
          button("Use", { size: "sm", variant: "secondary", onClick: () => {
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
      callout("info", "TikTok only takes music in its own publish form, which opens in your Claude chat when you publish. Claude shows you this sound and these volumes there, so setting them is one tap."));
  } else if (platform === "instagram") {
    const name = textField({ label: "Instagram sound", value: settings.sound && settings.sound.name, placeholder: "Song title and artist, e.g. Night Drive Lumen",
      help: "Write it the way Instagram's music search shows it. Claude picks the exact match from Instagram's music library.",
      onInput: (v) => { settings.sound = v.trim() ? { name: v.trim() } : null; save(); } });
    box.append(name, sliders,
      callout("info", "Claude adds this sound through Metricool at these volumes. Instagram allows that only for a Business account linked to a Facebook Page; for any other account the reel goes out with its own audio and you add the sound in the app."));
  } else {
    const name = textField({ label: "Sound to add", value: settings.sound && settings.sound.name, placeholder: "The trending sound's name or link",
      onInput: (v) => { settings.sound = v.trim() ? { name: v.trim() } : null; save(); } });
    box.append(name, sliders,
      callout("warn", `${PLATFORMS[platform].label} doesn't let any app add its in-app sounds: add this sound in the ${PLATFORMS[platform].label} app, at these volumes, when you post (or right after, while editing the post).`));
  }
  return box;
}

function platformPanel(platform, post, accounts, save, changed) {
  const s = post[platform];
  const meta = PLATFORMS[platform];
  const mine = accounts.filter((a) => a.platform === platform);
  const body = h("div", { class: "stack-lg" });
  const sw = switchControl({ id: `on-${platform}`, checked: s.enabled, label: `Post to ${meta.label}`, onChange: (v) => {
    s.enabled = v;
    body.classList.toggle("is-off", !v);
    body.inert = !v;
    save();
    changed();
  } });
  const acc = mine.length
    ? h("div", { class: "chips", role: "group", "aria-label": `${meta.label} accounts` }, mine.map((a) => {
        const id = `acc-${platform}-${a.id}`;
        const input = h("input", { type: "checkbox", id, class: "visually-hidden", checked: s.accounts.includes(a.id) });
        input.addEventListener("change", () => {
          s.accounts = input.checked ? [...new Set([...s.accounts, a.id])] : s.accounts.filter((x) => x !== a.id);
          save();
          changed();
        });
        return [input, h("label", { class: "chip", for: id }, h("span", { class: "chip-check", html: icon("check", { size: 14 }) }), accountName(a),
          a.status && a.status !== "active" && a.status !== "manual" ? h("span", { class: "chip-note" }, a.status) : null)];
      }))
    : callout("warn", h("span", {}, `No ${meta.label} account yet. `, h("a", { href: "#/x/accounts" }, "Add one on the Accounts page.")));

  const fields = [];
  if (platform === "youtube") {
    fields.push(textField({ label: "Title", value: s.title, max: meta.titleMax, placeholder: "Shown under the Short",
      onInput: (v) => { s.title = v; save(); changed(); } }));
  }
  fields.push(textField({ label: platform === "youtube" ? "Description" : "Caption", value: s.caption, multiline: true, max: meta.captionMax,
    placeholder: post.common.caption ? `Same as above: ${post.common.caption.slice(0, 80)}` : "Leave empty to use the post text above",
    onInput: (v) => { s.caption = v.trim() ? v : null; save(); changed(); } }));
  fields.push(textField({ label: platform === "youtube" ? "Tags" : "Hashtags", value: s.hashtags ? tagText(s.hashtags) : "",
    placeholder: post.common.hashtags.length ? `Same as above: ${tagText(post.common.hashtags)}` : "#motivation #quotes",
    help: meta.hashtagMax ? `Up to ${meta.hashtagMax} hashtags on ${meta.label}.` : null,
    onInput: (v) => { s.hashtags = v.trim() ? tagsOf(v) : null; save(); changed(); } }));

  const options = h("div", { class: "form-grid" });
  if (platform === "tiktok") {
    options.append(selectField("Who can watch", TIKTOK_PRIVACY, s.privacy, (v) => { s.privacy = v; save(); }),
      selectField("Post", [{ value: "DIRECT_POST", label: "Post now" }, { value: "UPLOAD_TO_DRAFT", label: "Save as a TikTok draft" }], s.mode, (v) => { s.mode = v; save(); }),
      toggle("Allow comments", s.allow_comment, (v) => { s.allow_comment = v; save(); }),
      toggle("Allow duets", s.allow_duet, (v) => { s.allow_duet = v; save(); }),
      toggle("Allow stitches", s.allow_stitch, (v) => { s.allow_stitch = v; save(); }),
      toggle("Label as AI-generated", s.is_aigc, (v) => { s.is_aigc = v; save(); }));
  }
  let best = null;
  if (platform !== "tiktok") {
    const when = whenField(s, () => { save(); changed(); });
    best = h("div", { class: "stack" }, h("p", { class: "subsection-title" }, "Best time to post"),
      bestTimePicker(platform, (value) => {
        when.setAt(value);
        toast(`${meta.label} posts ${new Date(value).toLocaleString(undefined, { weekday: "long", hour: "numeric", minute: "2-digit" })}.`, { kind: "success", timeout: 2500 });
      }));
    if (platform === "instagram") {
      options.append(when, toggle("Also show in the feed", s.share_to_feed, (v) => { s.share_to_feed = v; save(); }));
    } else {
      options.append(when, selectField("Visibility", YT_PRIVACY, s.privacy, (v) => { s.privacy = v; save(); }),
        toggle("Made for kids", s.made_for_kids, (v) => { s.made_for_kids = v; save(); }));
    }
  }

  body.append(
    h("div", { class: "stack" }, h("p", { class: "subsection-title" }, "Post to"), acc),
    h("div", { class: "stack" }, ...fields),
    options,
    best || "",
    h("div", { class: "stack" }, h("p", { class: "subsection-title" }, "Sound"), soundSection(platform, s, accounts, () => { save(); changed(); })));
  body.classList.toggle("is-off", !s.enabled);
  body.inert = !s.enabled;
  return h("div", { class: "ppanel-inner" },
    h("div", { class: "ppanel-head" }, sw,
      h("span", { class: "muted small" }, platform === "tiktok" ? "Through Higgsfield" : "Through Metricool")),
    body);
}

function resultsCard(task, taskId) {
  if (!task) return null;
  const rows = Object.entries(task.result || {}).map(([platform, r]) => {
    const ok = ["done", "scheduled"].includes(r.status);
    let actions = null;
    if (platform === "tiktok" && r.status === "waiting" && taskId) {
      const check = r.publish_id ? button("Check TikTok", { icon: "refresh", size: "sm", variant: "ghost" }) : null;
      if (check) check.addEventListener("click", async () => {
        check.classList.add("is-loading");
        try {
          const next = await checkTiktok(taskId, task);
          toast(next.message, { kind: next.status === "error" ? "error" : "info", timeout: 2500 });
        } catch (err) {
          toastError("Could not read TikTok's status", err);
        } finally {
          check.classList.remove("is-loading");
        }
      });
      const posted = button("I posted it", { icon: "check", size: "sm", variant: "ghost",
        onClick: () => markTiktokPosted(taskId, task).catch((err) => toastError("Could not save", err)) });
      actions = h("span", { class: "row result-actions" }, check, posted);
    }
    return h("li", { class: ["check", ok ? "is-ok" : r.status === "error" ? "is-missing" : "is-optional"] },
    h("span", { class: "check-icon", html: icon(ok ? "check" : r.status === "error" ? "alert" : "info", { size: 14 }) }),
    h("span", {}, h("strong", {}, (PLATFORMS[platform] || { label: platform }).label), ` · ${r.message || r.status}`,
      r.url ? [" · ", h("a", { href: r.url, target: "_blank", rel: "noopener" }, "Open post")] : null),
    actions);
  });
  const st = publishState(task);
  return h("section", { class: ["card", "job-card", `job-${task.status}`] },
    h("div", { class: "card-head card-head-row" },
      h("div", {}, h("h2", { class: "card-title" }, task.status === "queued" && task.notify_error ? "Waiting for Claude"
        : ["queued", "running"].includes(task.status) ? "Publishing…" : "Publish results"),
        h("p", { class: "card-sub", role: "status", "aria-live": "polite" }, task.message || "")),
      pill(st)),
    rows.length ? h("ul", { class: "checklist" }, rows) : null,
    task.error ? callout("error", task.error) : null);
}

/**
 * What to fix or know before publishing: [{level: "error" | "warn" | "ok", text, platform?}].
 * ctx = {project (a reel's settings), duration, dims: [w, h], posted: Set of platforms, renderWarnings}.
 */
function checksFor(post, ctx) {
  const out = [];
  const add = (level, text, platform = null) => out.push({ level, text, platform });
  const on = ORDER.filter((p) => post[p].enabled);
  if (!on.length) add("error", "Every platform is off. Turn one on in its tab.");
  for (const p of on) {
    const s = post[p];
    const meta = PLATFORMS[p];
    if (!s.accounts.length) add("warn", `${meta.label} is on but no account is ticked, so it's skipped.`, p);
    const caption = (s.caption ?? post.common.caption) || "";
    const tags = s.hashtags ?? post.common.hashtags;
    if (p === "youtube") {
      const title = (s.title || "").trim();
      if (!title) add("error", "YouTube needs a title.", p);
      else if (title.length > meta.titleMax) add("error", `The YouTube title is ${title.length} characters; YouTube allows ${meta.titleMax}.`, p);
      if (caption.length > meta.captionMax) add("error", `The YouTube description is ${caption.length} characters; YouTube allows ${meta.captionMax}.`, p);
      const tagChars = tags.join(",").length;
      if (tagChars > 500) add("warn", `The YouTube tags add up to ${tagChars} characters; YouTube keeps 500.`, p);
    } else {
      const full = caption.length + (tags.length ? tagText(tags).length + 2 : 0);
      if (full > meta.captionMax) add("error", `The ${meta.label} caption with its hashtags is ${full} characters; ${meta.label} allows ${meta.captionMax}.`, p);
      const inline = (caption.match(/#[\p{L}\p{N}_]+/gu) || []).length;
      if (meta.hashtagMax && tags.length + inline > meta.hashtagMax) add("error", `${meta.label} allows ${meta.hashtagMax} hashtags; this post has ${tags.length + inline}.`, p);
    }
    if (s.when === "at") {
      if (!s.at) add("error", `Pick the time for ${meta.label}.`, p);
      else if (new Date(s.at) < new Date()) add("error", `The ${meta.label} time has passed. Pick a time in the future.`, p);
    }
    if (ctx.posted && ctx.posted.has(p)) add("warn", `This video was already sent to ${meta.label} from here; publishing again posts it a second time.`, p);
    if (ctx.duration) {
      if (p === "youtube" && ctx.duration > 180) add("warn", `The video is ${formatSeconds(ctx.duration)}: Shorts are up to 3 minutes, so YouTube makes it a normal video.`, p);
      if (p === "instagram" && ctx.duration > 180) add("warn", `The video is ${formatSeconds(ctx.duration)}: Instagram suggests reels of up to 3 minutes to new viewers.`, p);
      if (p === "tiktok" && ctx.duration > 600) add("error", `The video is ${formatSeconds(ctx.duration)}: TikTok takes up to 10 minutes from apps.`, p);
    }
  }
  const bare = on.filter((p) => p !== "youtube" && !((post[p].caption ?? post.common.caption) || "").trim());
  if (bare.length) add("warn", `There's no caption for ${bare.map((p) => PLATFORMS[p].label).join(" and ")} yet. A line of text gets people commenting.`);
  const vs = ctx.project && ctx.project.style && ctx.project.style.video;
  if (vs && vs.draft) add("warn", "Draft quality is on for this reel (half the resolution). Turn it off on the Render tab and render again before posting.");
  if (vs && vs.aspect && vs.aspect !== "9:16" && !vs.letterbox) {
    add("warn", `The reel is ${vs.aspect}, so it won't fill the phone screen. Turn on “Black bars to 9:16” on the Style tab and render again to post it full-screen.`);
  } else if (!vs && ctx.dims && ctx.dims[0] && Math.abs(ctx.dims[0] / ctx.dims[1] - 9 / 16) > 0.03) {
    add("warn", `This video is ${ctx.dims[0]}×${ctx.dims[1]}, not 9:16, so it won't fill the phone screen.`);
  }
  for (const w of ctx.renderWarnings || []) add("warn", `From the render: ${w}`);
  if (!out.length) add("ok", "Everything looks ready.");
  return out;
}

async function renderComposer(container, key) {
  const { db, retrying, newTask, nowIso } = P();
  container.append(h("p", { class: "back-row" }, h("a", { class: "btn btn-ghost btn-sm", href: "#/x/publish", html: `${icon("chevronLeft", { size: 16 })}<span class="btn-label">All videos</span>` })));
  const [videos, accounts, snap] = await Promise.all([listVideos(), listAccounts(), retrying(() => db.doc(`posts/${key}`).get())]);
  const video = videos.find((v) => v.key === key);
  if (!video) {
    container.append(callout("error", "This video is no longer in the panel."));
    return null;
  }
  const [kind, id] = key.split(":");
  const base = newPost({ kind, id });
  const post = snap.exists ? { ...base, ...snap.data() } : base;
  for (const p of ORDER) post[p] = { ...base[p], ...(post[p] || {}) };
  if (!snap.exists) {
    // start with every account ticked
    for (const p of ORDER) post[p].accounts = accounts.filter((a) => a.platform === p).map((a) => a.id);
    post.youtube.title = video.title;
  }
  const save = debounce(() => retrying(() => db.doc(`posts/${key}`).set({ ...post, updated: nowIso() }))
    .catch((err) => toastError("Could not save the post settings", err)), 600);

  // what the checks look at besides the post: the reel's settings, the file, earlier posts, the render's notes
  const ctx = { project: null, duration: (video.info && video.info.duration) || null, dims: null, posted: new Set(), renderWarnings: [] };
  if (kind === "reel") {
    const ps = await retrying(() => db.doc(`projects/${id}`).get()).catch(() => null);
    const d = ps && ps.exists ? ps.data() : null;
    ctx.project = (d && d.project) || null;
    if (d && d.outputs && d.outputs.timings) {
      fetch(P().blobUrl(d.outputs.timings)).then((r) => (r.ok ? r.json() : null)).then((t) => {
        if (t && Array.isArray(t.warnings) && t.warnings.length) {
          ctx.renderWarnings = t.warnings;
          repaintChecks();
        }
      }).catch(() => {});
    }
  }

  let active = ORDER.find((p) => post[p].enabled) || "tiktok";
  const phone = phonePreview(video);
  phone.player.addEventListener("loadedmetadata", () => {
    ctx.duration = ctx.duration || phone.player.duration || null;
    ctx.dims = [phone.player.videoWidth, phone.player.videoHeight];
    repaintChecks();
  });
  const repaintPreview = () => phone.render(active, post, accounts);
  const schedulePreview = (() => {
    let pending = false;
    return () => {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        repaintPreview();
        repaintTabs();
        repaintBar();
        repaintChecks();
      });
    };
  })();

  const captionField = textField({ label: "Caption", value: post.common.caption, multiline: true, rows: 4, max: 2200, placeholder: "What the post says",
    onInput: (v) => { post.common.caption = v; save(); schedulePreview(); } });
  const tagsField = textField({ label: "Hashtags", value: tagText(post.common.hashtags), placeholder: "#motivation #mindset #quotes",
    help: "Separate with spaces or commas; the # is optional.", onInput: (v) => { post.common.hashtags = tagsOf(v); save(); schedulePreview(); } });
  const setField = (field, value) => {
    const input = field.querySelector("input, textarea");
    input.value = value;
    input.dispatchEvent(new Event("input"));
  };

  // Claude writes the caption, hashtags and YouTube title from the script (the viewer's own Claude usage)
  const writer = h("div", { class: "writer", "aria-live": "polite" });
  const writeBtn = canWrite() ? button("Write it for me", { icon: "sparkle", size: "sm", variant: "secondary" }) : null;
  let writing = null;
  const useSuggestion = (r) => {
    setField(captionField, r.caption);
    setField(tagsField, tagText(r.hashtags));
    if (r.title) {
      post.youtube.title = r.title;
      panels.youtube.replaceChildren(platformPanel("youtube", post, accounts, save, schedulePreview));
      save();
    }
    writer.replaceChildren();
    toast(r.title ? "Caption, hashtags and the YouTube title are filled in." : "Caption and hashtags are filled in.", { kind: "success", timeout: 2500 });
  };
  const write = async (fresh) => {
    if (writing) writing.abort();
    const ctl = (writing = new AbortController());
    writeBtn.disabled = true;
    writer.replaceChildren(h("div", { class: "writer-box is-busy" }, h("span", { class: "spinner spinner-sm", "aria-hidden": "true" }),
      h("span", {}, "Claude is writing… this can take up to a minute."),
      button("Stop", { size: "sm", variant: "ghost", onClick: () => ctl.abort() })));
    try {
      const r = await writePost({ title: video.title, script: ctx.project ? ctx.project.script : "",
                                  about: ctx.project ? "" : post.common.caption, handles: accounts.map(accountName).filter(Boolean) },
                                { signal: ctl.signal, fresh });
      if (ctl !== writing) return;
      writer.replaceChildren(h("div", { class: "writer-box" },
        h("p", { class: "subsection-title" }, "Claude's suggestion"),
        h("p", { class: "writer-caption" }, r.caption),
        r.hashtags.length ? h("p", { class: "writer-tags" }, tagText(r.hashtags)) : null,
        r.title ? h("p", { class: "writer-title" }, h("span", { class: "muted" }, "YouTube title: "), r.title) : null,
        h("div", { class: "row row-wrap" },
          button("Use it", { icon: "check", size: "sm", variant: "primary", onClick: () => useSuggestion(r) }),
          button("Write another", { icon: "refresh", size: "sm", variant: "secondary", onClick: () => write(true) }),
          button("Dismiss", { size: "sm", variant: "ghost", onClick: () => writer.replaceChildren() }))));
    } catch (err) {
      if (ctl !== writing) return;
      writer.replaceChildren(err && err.code === "cancelled" ? "" : callout("warn", sampleProblem(err)));
    } finally {
      if (ctl === writing) {
        writing = null;
        writeBtn.disabled = false;
      }
    }
  };
  if (writeBtn) writeBtn.addEventListener("click", () => write(false));

  const common = h("section", { class: "card" },
    h("div", { class: "card-head card-head-row" },
      h("div", {}, h("h2", { class: "card-title" }, "Post text"),
        h("p", { class: "card-sub" }, "Written once, used on every platform unless a platform tab says otherwise.")),
      writeBtn),
    writer,
    h("div", { class: "stack" }, captionField, tagsField));

  // platform tabs: one panel each, the preview follows the open tab
  const tablist = h("div", { class: "ptabs", role: "tablist", "aria-label": "Platforms" });
  const panels = {};
  const tabs = {};
  for (const p of ORDER) {
    tabs[p] = h("button", { type: "button", role: "tab", id: `ptab-${p}`, class: "ptab", "aria-controls": `ppanel-${p}` });
    tabs[p].addEventListener("click", () => select(p));
    tabs[p].addEventListener("keydown", (e) => {
      const i = ORDER.indexOf(p);
      const next = e.key === "ArrowRight" ? ORDER[(i + 1) % ORDER.length] : e.key === "ArrowLeft" ? ORDER[(i + ORDER.length - 1) % ORDER.length] : null;
      if (next) {
        e.preventDefault();
        select(next);
        tabs[next].focus();
      }
    });
    tablist.append(tabs[p]);
    panels[p] = h("section", { role: "tabpanel", id: `ppanel-${p}`, class: "ppanel", "aria-labelledby": `ptab-${p}` },
      platformPanel(p, post, accounts, save, schedulePreview));
  }
  const repaintTabs = () => {
    for (const p of ORDER) {
      const s = post[p];
      const n = s.accounts.length;
      tabs[p].setAttribute("aria-selected", String(p === active));
      tabs[p].tabIndex = p === active ? 0 : -1;
      tabs[p].replaceChildren(platformDot(p), h("span", { class: "ptab-label" }, PLATFORMS[p].label),
        h("span", { class: ["ptab-state", s.enabled && n ? "is-on" : "is-off"] }, s.enabled ? (n ? `${n}` : "no account") : "off"));
      panels[p].hidden = p !== active;
    }
  };
  const select = (p) => {
    active = p;
    repaintTabs();
    repaintPreview();
    repaintBar();
  };
  const platformSwitch = h("div", { class: "preview-tabs", role: "group", "aria-label": "Preview as" });
  const previewTabs = ORDER.map((p) => {
    const b = h("button", { type: "button", class: "preview-tab" }, platformDot(p), PLATFORMS[p].surface);
    b.addEventListener("click", () => select(p));
    platformSwitch.append(b);
    return [p, b];
  });

  if (post.cover && post.cover.asset) phone.player.poster = P().blobUrl(post.cover.asset);
  const cover = coverCard(post, videoUrl(video), save, (c) => {
    phone.player.poster = c && c.asset ? P().blobUrl(c.asset) : video.thumb || "";
  });
  const virality = viralityCard(post, key, video, save);

  const checksBox = h("section", { class: "card checks-card", "aria-labelledby": "checks-title" });
  const repaintChecks = () => {
    const list = checksFor(post, ctx);
    const bad = list.filter((c) => c.level === "error").length;
    const warn = list.filter((c) => c.level === "warn").length;
    checksBox.replaceChildren(
      h("div", { class: "card-head card-head-row" },
        h("div", {}, h("h2", { class: "card-title", id: "checks-title" }, "Before it goes out"),
          h("p", { class: "card-sub" }, bad ? `${bad} thing${bad === 1 ? "" : "s"} to fix before publishing.` : warn ? "Worth a look, but nothing stops it." : "All set.")),
        pill(bad ? { label: "Fix first", tone: "bad" } : warn ? { label: "Have a look", tone: "warn" } : { label: "Ready", tone: "ok" })),
      h("ul", { class: "checklist" }, list.map((c) => h("li", { class: ["check", `is-${c.level}`] },
        h("span", { class: "check-icon", html: icon(c.level === "ok" ? "check" : c.level === "error" ? "alert" : "info", { size: 14 }) }),
        h("span", {}, c.platform ? [platformDot(c.platform), " "] : null, c.text)))));
  };

  const bar = h("div", { class: "publish-bar" });
  const results = h("div", {});
  const publishBtn = button("Publish", { icon: "upload", variant: "primary", size: "lg" });
  const summary = h("div", { class: "publish-summary" });
  const targets = () => ORDER.filter((p) => post[p].enabled && post[p].accounts.length);
  const repaintBar = () => {
    const t = targets();
    const n = t.reduce((sum, p) => sum + post[p].accounts.length, 0);
    publishBtn.querySelector(".btn-label").textContent = t.length ? `Publish to ${n} account${n === 1 ? "" : "s"}` : "Publish";
    publishBtn.disabled = !t.length;
    summary.replaceChildren(...(t.length
      ? t.map((p) => h("span", { class: "publish-chip" }, platformDot(p), PLATFORMS[p].label, h("span", { class: "timecode" }, `×${post[p].accounts.length}`),
          whenText(post[p]) ? h("span", { class: "publish-when" }, whenText(post[p])) : null))
      : [h("span", { class: "muted small" }, "Turn on a platform and pick an account.")]));
    for (const [p, b] of previewTabs) b.setAttribute("aria-pressed", String(p === active));
  };
  bar.append(summary, publishBtn);

  let unsub = null;
  let checkedTiktok = false;
  let reveal = false; // scroll to the results once the new task shows up
  const watch = (taskId) => {
    if (unsub) unsub();
    unsub = db.doc(`tasks/${taskId}`).onSnapshot((s) => {
      const t = s.exists ? s.data() : null;
      ctx.posted = new Set(Object.entries((t && t.result) || {})
        .filter(([, r]) => r && ["done", "scheduled", "waiting"].includes(r.status)).map(([p]) => p));
      repaintChecks();
      results.replaceChildren(resultsCard(t, taskId) || "");
      if (t && !checkedTiktok && t.result && t.result.tiktok && t.result.tiktok.status === "waiting" && t.result.tiktok.publish_id) {
        checkedTiktok = true; // once per visit: TikTok may have finished processing since
        checkTiktok(taskId, t).catch(() => {});
      }
      if (reveal && s.exists) {
        reveal = false;
        results.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }, () => {});
  };
  if (post.last_task) watch(post.last_task);

  publishBtn.addEventListener("click", async () => {
    const t = targets();
    const errors = checksFor(post, ctx).filter((c) => c.level === "error" && (!c.platform || t.includes(c.platform)));
    if (errors.length) {
      toast(errors[0].text, { kind: "error" });
      checksBox.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
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
    h("div", { class: "composer" },
      h("aside", { class: "composer-preview", "aria-label": "Preview" },
        h("div", { class: "preview-switch" }, platformSwitch), phone.el,
        h("p", { class: "muted small preview-note" }, "A mock-up of the post: counts and buttons are examples.")),
      h("div", { class: "stack-lg composer-main" },
        common,
        h("section", { class: "card card-flush platform-card" }, tablist, ...ORDER.map((p) => panels[p])),
        cover,
        virality,
        checksBox,
        results)),
    bar);
  repaintTabs();
  repaintPreview();
  repaintBar();
  repaintChecks();
  return () => {
    if (unsub) unsub();
    virality.cleanup();
  };
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
      h("p", { class: "muted small" }, "Posting to it needs Metricool in Claude, linked to this account (see “How posting works”).")),
    actions: [{ label: "Cancel", value: null, variant: "ghost" }, { label: "Add", value: () => input.value.trim().replace(/^@/, ""), variant: "primary", submit: true }],
    initialFocus: "input",
  });
  if (!handle) return;
  const { db, retrying, nowIso, hex } = P();
  await retrying(() => db.doc(`accounts/${platform}-${hex(4)}`).set({ platform, handle, status: "manual", profile: {}, created: nowIso() }));
  refresh();
}

function connectionsCard(accounts, route, refresh) {
  const tiktok = accounts.filter((a) => a.platform === "tiktok" && a.status === "active").length;
  const metricool = (route.connectors || []).some((c) => /metricool/i.test(c));
  const computer = Boolean(route.computer_session_id);
  const row = (title, how, ready, readyText, notText, ...actions) => h("li", { class: "conn-row" },
    h("span", { class: ["conn-state", ready ? "is-ok" : "is-idle"], html: icon(ready ? "check" : "info", { size: 16 }) }),
    h("div", { class: "conn-main" },
      h("div", { class: "conn-title" }, h("strong", {}, title), h("span", { class: ["state-pill", ready ? "is-ok" : "is-idle"] }, ready ? readyText : notText)),
      h("p", { class: "muted small" }, how)),
    h("div", { class: "conn-actions" }, actions));
  return h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", { class: "card-title" }, "How posting works"),
      h("p", { class: "card-sub" }, "Each part runs through a different connection. Set up the ones you need.")),
    h("ul", { class: "conn-list" },
      row("TikTok posts", "Through your Higgsfield connection. TikTok asks you to confirm each post in your Claude chat.",
        tiktok > 0, `${tiktok} account${tiktok === 1 ? "" : "s"}`, "Not connected",
        button("Connect TikTok", { icon: "plus", size: "sm", variant: tiktok ? "secondary" : "primary", onClick: () => connectTiktok(null, refresh) })),
      row("Instagram and YouTube posts", "Through Metricool: add the Metricool connector in Claude, link your Instagram (Business or Creator) and YouTube accounts inside Metricool, then start a new Claude session for this project and say “take over my reelforge panel”.",
        metricool, "Ready", "Needs Metricool",
        h("a", { class: "btn btn-secondary btn-sm", href: CONNECTORS_URL, target: "_blank", rel: "noopener", html: `${icon("link", { size: 16 })}<span class="btn-label">Claude connectors</span>` })),
      row("Profile pictures and bios", "No app can change these through TikTok, Instagram or YouTube, so Claude does it in your own browser: open Claude on your computer (desktop app, with Claude in Chrome) in this project and say “connect my reelforge panel for profile changes”.",
        computer, "Ready", "Needs Claude on your computer")));
}

function profileResults(task) {
  if (!task) return null;
  const rows = Object.entries(task.result || {}).map(([accountId, r]) => h("li", { class: ["check", r.status === "done" ? "is-ok" : r.status === "error" ? "is-missing" : "is-optional"] },
    h("span", { class: "check-icon", html: icon(r.status === "done" ? "check" : r.status === "error" ? "alert" : "info", { size: 14 }) }),
    h("span", {}, h("strong", {}, r.account || accountId), ` · ${r.message || r.status}`)));
  return h("section", { class: ["card", "job-card"] },
    h("div", { class: "card-head" }, h("h2", { class: "card-title" }, task.status === "queued" && task.notify_error ? "Waiting for Claude on your computer"
      : ["queued", "running"].includes(task.status) ? "Changing your profiles…" : "Profile changes"),
      h("p", { class: "card-sub", role: "status", "aria-live": "polite" }, task.message || "")),
    rows.length ? h("ul", { class: "checklist" }, rows) : null,
    task.error ? callout("error", task.error) : null);
}

function profileEditor(accounts, chosen, refresh, onTask) {
  const values = { name: null, bio: null, link: null, picture: null };
  const picked = () => accounts.filter((a) => chosen.has(a.id));
  const strictest = () => Math.min(...picked().map((a) => PLATFORMS[a.platform].bioMax), 1000);
  const bioHelp = h("p", { class: "field-help" });
  const count = h("p", { class: "muted small" });
  const paint = () => {
    bioHelp.textContent = `Up to ${strictest()} characters for the accounts selected (TikTok 80, Instagram 150, YouTube 1000). Empty fields stay as they are.`;
    count.textContent = chosen.size ? `${chosen.size} account${chosen.size === 1 ? "" : "s"} selected. Tick or untick accounts below.` : "Select accounts below to change them.";
  };
  const preview = h("div", { class: "pic-preview", "aria-hidden": "true", html: icon("image", { size: 22 }) });
  const picName = h("span", { class: "muted small" }, "No new picture: the current ones stay.");
  const clearPic = button("Remove", { variant: "ghost", size: "sm", hidden: true, onClick: () => {
    values.picture = null;
    preview.innerHTML = icon("image", { size: 22 });
    picName.textContent = "No new picture: the current ones stay.";
    clearPic.hidden = true;
  } });
  const pic = dropzone({ accept: "image/*", compact: true, iconName: "image", title: "Choose a new profile picture", hint: "Square JPG or PNG, at least 400 × 400",
    onFiles: async ([file]) => {
      try {
        const type = ["image/png", "image/jpeg", "image/webp"].includes(file.type) ? file.type : "image/jpeg";
        const res = await P().uploadAsset(file, type);
        values.picture = res.id;
        preview.replaceChildren(h("img", { src: P().blobUrl(res.id), alt: "" }));
        picName.textContent = `${file.name} will be used.`;
        clearPic.hidden = false;
      } catch (err) {
        toastError("Could not add the picture", err);
      }
    } });
  const patchOf = () => Object.fromEntries(Object.entries(values).filter(([, v]) => v !== null && v !== ""));
  async function saveProfiles() {
    const patch = patchOf();
    if (!chosen.size || !Object.keys(patch).length) {
      toast("Select accounts and fill in at least one field.", { kind: "error" });
      return null;
    }
    const tooLong = patch.bio && picked().filter((a) => patch.bio.length > PLATFORMS[a.platform].bioMax);
    if (tooLong && tooLong.length && !(await confirmDialog({ title: "Bio too long for some accounts", message: `${tooLong.map((a) => `${PLATFORMS[a.platform].label} ${accountName(a)}`).join(", ")} allow fewer characters. Save anyway?`, confirmLabel: "Save" }))) return null;
    for (const a of picked()) {
      await P().retrying(() => P().db.doc(`accounts/${a.id}`).update({ profile: { ...(a.profile || {}), ...patch }, updated: P().nowIso() }));
    }
    return patch;
  }
  const saveBtn = button("Save", { icon: "save", variant: "secondary" });
  const applyBtn = button("Change on my accounts", { icon: "wand", variant: "primary" });
  saveBtn.addEventListener("click", async () => {
    saveBtn.classList.add("is-loading");
    try {
      if (await saveProfiles()) {
        toast(`Saved to ${chosen.size} account${chosen.size === 1 ? "" : "s"}.`, { kind: "success" });
        refresh();
      }
    } catch (err) {
      toastError("Could not save the profiles", err);
    } finally {
      saveBtn.classList.remove("is-loading");
    }
  });
  applyBtn.addEventListener("click", async () => {
    applyBtn.classList.add("is-loading");
    try {
      const patch = await saveProfiles();
      if (!patch) return;
      const targets = picked().map((a) => ({ id: a.id, platform: a.platform, handle: a.handle || "" }));
      const { id, problem } = await P().newTask("profile", null, { accounts: targets, changes: patch });
      await P().retrying(() => P().db.doc("settings/profile").set({ last_task: id, updated: P().nowIso() }));
      onTask(id);
      toast(problem ? `Saved. ${problem} Open Claude on your computer and say “apply my reelforge profile changes”.`
        : "Sent to Claude: it changes them in your browser.", { kind: problem ? "info" : "success", timeout: problem ? 10000 : undefined });
      refresh();
    } catch (err) {
      toastError("Could not send the profile changes", err);
    } finally {
      applyBtn.classList.remove("is-loading");
    }
  });
  paint();
  return {
    paint,
    el: h("section", { class: "card profile-editor" },
      h("div", { class: "card-head" }, h("h2", { class: "card-title" }, "Edit profiles"),
        h("p", { class: "card-sub" }, "Change one account, or many at once.")),
      h("div", { class: "stack-lg" },
        count,
        h("div", { class: "form-grid" },
          textField({ label: "Display name", placeholder: "Leave empty to keep", onInput: (v) => (values.name = v.trim() || null) }),
          textField({ label: "Link in bio", placeholder: "https://…", onInput: (v) => (values.link = v.trim() || null) })),
        textField({ label: "Bio", multiline: true, rows: 3, placeholder: "Leave empty to keep", help: bioHelp, onInput: (v) => (values.bio = v.trim() || null) }),
        h("div", { class: "pic-row" }, preview, h("div", { class: "stack pic-main" }, pic, h("div", { class: "row" }, picName, clearPic))),
        h("div", { class: "row row-wrap" }, applyBtn, saveBtn),
        h("p", { class: "muted small" }, "“Change on my accounts” sends the changes to Claude on your computer, which makes them in your browser where you're signed in. “Save” only keeps them here, for copying."))),
  };
}

function accountCard(a, chosen, onToggle, refresh) {
  const pr = a.profile || {};
  const meta = PLATFORMS[a.platform];
  const pic = pr.picture ? h("img", { src: P().blobUrl(pr.picture), alt: "" }) : h("span", {}, strip(accountName(a)).slice(0, 1).toUpperCase() || "?");
  const id = `pick-${a.id}`;
  const tick = h("input", { type: "checkbox", id, class: "account-tick", checked: chosen.has(a.id), "aria-label": `Select ${accountName(a)}` });
  tick.addEventListener("change", () => onToggle(a.id, tick.checked));
  return h("li", { class: ["account-card", chosen.has(a.id) && "is-picked"] },
    h("div", { class: "account-top" },
      h("div", { class: `account-pic ring-${a.platform}`, "aria-hidden": "true" }, pic),
      h("div", { class: "account-who" },
        h("strong", { class: "account-name" }, pr.name || accountName(a)),
        h("span", { class: "account-handle" }, platformDot(a.platform), `${meta.label} · ${accountName(a)}`)),
      tick),
    pr.bio ? h("p", { class: "account-bio" }, pr.bio) : h("p", { class: "account-bio muted" }, "No bio saved yet."),
    pr.link ? h("p", { class: "account-link small" }, pr.link) : null,
    h("div", { class: "account-actions" },
      a.status && a.status !== "manual" ? h("span", { class: ["state-pill", a.status === "active" ? "is-ok" : "is-warn"] }, a.status) : null,
      h("span", { class: "spacer" }),
      pr.bio ? button("Copy bio", { icon: "copy", size: "sm", variant: "ghost", iconOnly: true, onClick: async () => toast((await copyText(pr.bio)) ? "Bio copied." : "Copy failed.", { kind: "success", timeout: 1500 }) }) : null,
      pr.picture ? button("Save picture", { icon: "download", size: "sm", variant: "ghost", iconOnly: true, onClick: () => globalThis.reelforgeSaveFile(P().blobUrl(pr.picture), `${strip(a.handle || "profile").replace(/\W+/g, "")}-picture.jpg`) }) : null,
      h("a", { class: "btn btn-ghost btn-sm btn-icon", href: meta.settings(a), target: "_blank", rel: "noopener", title: "Open profile settings", "aria-label": "Open profile settings", html: icon("link", { size: 16 }) }),
      a.platform === "tiktok" && a.status === "error" ? button("Reconnect", { icon: "refresh", size: "sm", onClick: () => connectTiktok(a.connector_id, refresh) }) : null,
      button("Remove", { icon: "trash", variant: "ghost", size: "sm", iconOnly: true, class: "danger-hover", onClick: async () => {
        if (!(await confirmDialog({ title: `Remove ${accountName(a)}?`, message: "It's removed from the panel only. Nothing changes on the platform.", confirmLabel: "Remove", danger: true }))) return;
        await P().retrying(() => P().db.doc(`accounts/${a.id}`).delete());
        refresh();
      } })));
}

function renderAccounts(container) {
  const body = h("div", { class: "stack-lg" });
  const results = h("div", {});
  const chosen = new Set();
  const seen = new Set(); // accounts start selected; unticking one sticks
  let unsub = null;
  const watchProfile = (taskId) => {
    if (unsub) unsub();
    unsub = P().db.doc(`tasks/${taskId}`).onSnapshot((s) => results.replaceChildren(profileResults(s.exists ? s.data() : null) || ""), () => {});
  };
  container.append(head("Accounts", "Your TikTok, Instagram and YouTube accounts, how posting reaches them, and their profiles."), body);
  let first = true;
  async function paint() {
    let accounts = [];
    let route = {};
    try {
      [accounts, route] = await Promise.all([listAccounts(), P().routing()]);
      for (const a of accounts) {
        if (!seen.has(a.id)) chosen.add(a.id);
        seen.add(a.id);
      }
      if (first) {
        first = false;
        const snap = await P().retrying(() => P().db.doc("settings/profile").get()).catch(() => null);
        if (snap && snap.exists && snap.data().last_task) watchProfile(snap.data().last_task);
      }
    } catch (err) {
      toastError("Could not load your accounts", err);
    }
    for (const id of [...chosen]) if (!accounts.some((a) => a.id === id)) chosen.delete(id);
    const editor = accounts.length ? profileEditor(accounts, chosen, paint, watchProfile) : null;
    const lists = ORDER.map((platform) => {
      const mine = accounts.filter((a) => a.platform === platform);
      const add = platform === "tiktok"
        ? h("div", { class: "row" },
            button("Connect TikTok", { icon: "plus", size: "sm", variant: "secondary", onClick: () => connectTiktok(null, paint) }),
            button("Check accounts", { icon: "refresh", variant: "ghost", size: "sm", onClick: async () => {
              try {
                const n = await syncTiktok();
                toast(`${n} TikTok account${n === 1 ? "" : "s"} connected.`, { kind: "success", timeout: 2000 });
                paint();
              } catch (err) {
                toastError("Could not check TikTok accounts", err);
              }
            } }))
        : button(`Add ${PLATFORMS[platform].label} account`, { icon: "plus", size: "sm", variant: "secondary", onClick: () => addManualAccount(platform, paint) });
      return h("section", { class: "account-group" },
        h("div", { class: "account-group-head" }, h("h2", { class: "section-title" }, platformDot(platform), PLATFORMS[platform].label,
          h("span", { class: "count-pill" }, String(mine.length))), add),
        mine.length
          ? h("ul", { class: "account-grid" }, mine.map((a) => accountCard(a, chosen, (id, on) => {
              if (on) chosen.add(id);
              else chosen.delete(id);
              if (editor) editor.paint();
              const li = body.querySelector(`#pick-${CSS.escape(id)}`);
              if (li) li.closest(".account-card").classList.toggle("is-picked", on);
            }, paint)))
          : h("p", { class: "muted small account-empty" }, "No accounts yet."));
    });
    body.replaceChildren(connectionsCard(accounts, route, paint),
      editor ? editor.el : callout("info", "Connect or add an account to manage its profile here."), results, ...lists);
  }
  paint();
  return () => unsub && unsub();
}

// --------------------------------------------------------------------------- registration

/** For Calendar and Stats: a function that finds the panel video a post's text or title belongs to. */
async function videoLookup() {
  const norm = (t) => String(t || "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  const [videos, snap] = await Promise.all([listVideos(), P().retrying(() => P().db.collection("posts").get())]);
  const byKey = new Map(videos.map((v) => [v.key, v]));
  const known = [];
  for (const doc of snap.docs) {
    const v = byKey.get(doc.id);
    if (!v) continue;
    const p = doc.data();
    const texts = [p.common && p.common.caption, ...ORDER.map((n) => p[n] && p[n].caption), p.youtube && p.youtube.title]
      .map(norm).filter((t) => t.length >= 12).map((t) => t.slice(0, 48));
    known.push({ video: { ...v, key: doc.id }, texts });
  }
  return (text) => {
    const t = norm(text);
    if (t.length < 12) return null;
    const hit = known.find((k) => k.texts.some((x) => t.startsWith(x) || x.startsWith(t.slice(0, 48))));
    return hit ? hit.video : null;
  };
}

export const extras = {
  nav: [{ id: "ideas", label: "Ideas", icon: "sparkle" }, { id: "publish", label: "Publish", icon: "upload" }, { id: "calendar", label: "Calendar", icon: "calendar" },
        { id: "stats", label: "Stats", icon: "chart" }, { id: "accounts", label: "Accounts", icon: "link" }],
  homeActions: [{ label: "Script ideas", icon: "sparkle", href: "#/x/ideas" }, { label: "Render several", icon: "render", href: "#/x/batch" }],
  footageReview,
  homeStats() {
    const span = h("span", {});
    listAccounts().then((list) => {
      const n = list.length;
      span.replaceChildren(h("strong", {}, String(n)), n === 1 ? " account" : " accounts");
    }).catch(() => {});
    return [span];
  },
  render(id, sub, container) {
    if (id === "accounts") return renderAccounts(container);
    if (id === "calendar") return renderCalendar(container, { findVideo: videoLookup });
    if (id === "ideas") return renderIdeas(container);
    if (id === "batch") return renderBatch(container);
    if (id === "stats") return renderStats(container, { findVideo: videoLookup });
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
