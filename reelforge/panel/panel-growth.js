/**
 * Control panel pieces for growing the account:
 *
 *   Calendar   posts scheduled and published through Metricool, month by
 *              month, with the time of a waiting post changed or the post
 *              held back as a Metricool draft
 *   Stats      how each reel did on Instagram and YouTube (views, likes,
 *              comments, saves, shares, reach) and the best performers
 *   bestTimePicker / bestTimes   when the audience is most active, for the
 *              Publish page's "at a time I choose"
 *   writePost  a caption, hashtags and a YouTube title written by Claude
 *              from the reel's script
 *
 * Metricool is read and changed with the viewer's own Metricool connector
 * (the mcp capability); the writer asks Claude through the sample
 * capability, on the viewer's own Claude usage. Nothing here is stored in
 * the panel's database.
 */

import { api } from "./api.js";
import { callout } from "./components.js";
import { button, dialog, formatSeconds, h, toast, toastError } from "./dom.js";
import { segmented } from "./fields.js";
import { icon } from "./icons.js";

export const METRICOOL = "Metricool Social Media Management";
const CONNECTORS_URL = "https://claude.ai/customize/connectors";
const P = () => api._panel;
const pad = (n) => String(n).padStart(2, "0");
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const NETWORK_LABEL = { instagram: "Instagram", youtube: "YouTube", tiktok: "TikTok" };

// --------------------------------------------------------------------------- time zones

export const viewerTz = () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

function wall(date, tz) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
    timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(date).map((p) => [p.type, p.value]));
  return { y: +parts.year, mo: +parts.month, d: +parts.day, h: +parts.hour % 24, mi: +parts.minute, s: +parts.second };
}

function offsetMinutes(date, tz) {
  const w = wall(date, tz);
  return Math.round((Date.UTC(w.y, w.mo - 1, w.d, w.h, w.mi, w.s) - Math.floor(date.getTime() / 1000) * 1000) / 60000);
}

/** `date` as "YYYY-MM-DDTHH:MM:SS+HH:MM" on the clock of time zone `tz` (what Metricool takes). */
export function isoIn(date, tz) {
  const w = wall(date, tz);
  const off = offsetMinutes(date, tz);
  const a = Math.abs(off);
  return `${w.y}-${pad(w.mo)}-${pad(w.d)}T${pad(w.h)}:${pad(w.mi)}:${pad(w.s)}${off < 0 ? "-" : "+"}${pad(Math.floor(a / 60))}:${pad(a % 60)}`;
}

/** The moment that the clock time "YYYY-MM-DDTHH:mm[:ss]" in time zone `tz` names. */
export function instantOf(text, tz) {
  const [d, t = "00:00"] = String(text).split("T");
  const [y, mo, day] = d.split("-").map(Number);
  const [hh, mm, ss = 0] = t.split(":").map(Number);
  const guess = Date.UTC(y, mo - 1, day, hh, mm, ss);
  let at = guess - offsetMinutes(new Date(guess), tz) * 60000;
  at = guess - offsetMinutes(new Date(at), tz) * 60000; // once more, for the days the clocks change
  return new Date(at);
}

/** A <input type="datetime-local"> value for `date` on the viewer's clock. */
export const localInput = (date) =>
  `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;

const dayKey = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
const addDays = (date, n) => new Date(date.getFullYear(), date.getMonth(), date.getDate() + n);
const when = (date) => date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

// --------------------------------------------------------------------------- Metricool

/** The payload of a Metricool tool call (rejects with the connector's error codes). */
export async function metricool(tool, input, { text = false } = {}) {
  const mcp = P().mcp;
  if (!mcp) throw Object.assign(new Error("Connectors can't be used in this view."), { code: "no_mcp" });
  const res = await mcp.callTool(METRICOOL, tool, input || {});
  const payload = res && res.payload;
  if (typeof payload === "string" && !text) throw Object.assign(new Error(payload.slice(0, 300)), { code: "tool_error" });
  return payload;
}

let brandPromise = null;
/** The Metricool brand the panel posts through: {id, label, timezone, networks}. */
export function metricoolBrand() {
  brandPromise ||= metricool("getBrandSettings", {}).then((p) => {
    const list = (p && p.data) || [];
    const b = list.find((x) => x.networksData && (x.networksData.instagramData || x.networksData.youtubeData)) || list[0];
    if (!b) throw Object.assign(new Error("Your Metricool account has no brand yet."), { code: "no_brand" });
    return { id: String(b.id), label: b.label || "", timezone: b.timezone || viewerTz(), networks: b.networksData || {} };
  }).catch((err) => {
    brandPromise = null;
    throw err;
  });
  return brandPromise;
}

const MCP_PROBLEMS = {
  server_not_connected: "Metricool isn't connected to your Claude account.",
  needs_reauth: "Metricool needs to be reconnected in your Claude settings.",
  not_in_manifest: "This version of the panel isn't allowed to use Metricool.",
  approval_required: "Allow the panel to use Metricool to see this.",
  blocked_by_policy: "Your organization doesn't let the panel use Metricool.",
  no_mcp: "Connectors can't be used in this view.",
  no_brand: "Your Metricool account has no brand yet.",
};

export function metricoolProblem(err) {
  return MCP_PROBLEMS[err && err.code] || `Metricool didn't answer (${(err && (err.message || err.code)) || "unknown error"}).`;
}

export function metricoolCallout(err, retry) {
  const fixable = ["server_not_connected", "needs_reauth"].includes(err && err.code);
  return callout("warn", h("span", {}, metricoolProblem(err), " ",
    fixable ? h("a", { href: CONNECTORS_URL, target: "_blank", rel: "noopener" }, "Open connector settings") : null,
    retry ? [" ", button("Try again", { size: "sm", variant: "ghost", onClick: retry })] : null));
}

// --------------------------------------------------------------------------- best time to post

/**
 * Metricool's activity of the audience on `network` for the coming `days`:
 * {best: [{at, value, strength}] (the strongest hour of up to five days),
 *  grid: {1..7 (Monday = 1): {hour: value}}, max}. Hours are on the viewer's clock.
 */
export async function bestTimes(network, days = 7) {
  const brand = await metricoolBrand();
  const tz = viewerTz();
  const now = new Date();
  const p = await metricool("getBestTimeToPostByNetwork", {
    brandId: brand.id, fromDate: isoIn(now, tz), toDate: isoIn(new Date(now.getTime() + days * 864e5), tz),
    timezone: tz, socialNetwork: network });
  const grid = {};
  let max = 0;
  for (const d of (p && p.data) || []) {
    for (const x of d.bestTimesByHour || []) {
      const v = Number(x.value) || 0;
      (grid[d.dayOfWeek] ||= {})[x.hourOfDay] = v;
      max = Math.max(max, v);
    }
  }
  const perDay = new Map();
  const first = new Date(now);
  first.setMinutes(0, 0, 0);
  first.setHours(first.getHours() + 1); // the next full hour at the earliest
  for (let t = first.getTime(); t < now.getTime() + days * 864e5; t += 36e5) {
    const at = new Date(t);
    const v = grid[((at.getDay() + 6) % 7) + 1]?.[at.getHours()];
    if (v == null) continue;
    const key = dayKey(at);
    if (!perDay.has(key) || perDay.get(key).value < v) perDay.set(key, { at, value: v });
  }
  const best = [...perDay.values()].sort((a, b) => b.value - a.value).slice(0, 5).sort((a, b) => a.at - b.at)
    .map((s) => ({ ...s, strength: max ? s.value / max : 0 }));
  return { best, grid, max };
}

function heatmap(r) {
  const cells = [];
  for (let d = 1; d <= 7; d++) {
    cells.push(h("span", { class: "heat-day" }, DAY_NAMES[d - 1]));
    for (let hr = 0; hr < 24; hr++) {
      const v = (r.grid[d] && r.grid[d][hr]) || 0;
      cells.push(h("span", { class: "heat-cell", style: `--heat:${r.max ? (v / r.max).toFixed(2) : 0}`, title: `${DAY_NAMES[d - 1]} ${pad(hr)}:00` }));
    }
  }
  return h("details", { class: "heat" }, h("summary", {}, "The whole week, hour by hour"),
    h("div", { class: "heat-grid", role: "img", "aria-label": "How active your audience is, by day and hour (darker is busier)" }, cells),
    h("div", { class: "heat-hours", "aria-hidden": "true" }, ["00", "06", "12", "18", "23"].map((x) => h("span", {}, x))));
}

/** "Find the best times" for the Publish page; `onPick(datetimeLocalValue)` sets the time. */
export function bestTimePicker(network, onPick) {
  const label = NETWORK_LABEL[network] || network;
  const go = button("Find the best times", { icon: "clock", size: "sm", variant: "ghost" });
  const out = h("div", { class: "best-out", "aria-live": "polite" });
  const load = async () => {
    go.classList.add("is-loading");
    try {
      const r = await bestTimes(network);
      go.hidden = true;
      if (!r.best.length) {
        out.replaceChildren(h("p", { class: "muted small" }, `Metricool has no activity data for your ${label} audience yet.`));
        return;
      }
      const chips = h("div", { class: "best-chips", role: "group", "aria-label": `Best times on ${label}` });
      for (const s of r.best) {
        const pct = Math.round(s.strength * 100);
        const b = h("button", { type: "button", class: "best-chip", "aria-pressed": "false",
                                "aria-label": `${s.at.toLocaleString(undefined, { weekday: "long", hour: "numeric", minute: "2-digit" })}, ${pct}% of the week's busiest hour` },
          h("span", { class: "best-day" }, s.at.toLocaleDateString(undefined, { weekday: "short", day: "numeric" })),
          h("strong", {}, when(s.at)),
          h("span", { class: "best-bar", "aria-hidden": "true" }, h("span", { style: `width:${pct}%` })));
        b.addEventListener("click", () => {
          onPick(localInput(s.at));
          for (const x of chips.children) x.setAttribute("aria-pressed", String(x === b));
        });
        chips.append(b);
      }
      out.replaceChildren(h("p", { class: "muted small" }, "Tap one to post then:"), chips, heatmap(r));
    } catch (err) {
      out.replaceChildren(metricoolCallout(err, load));
    } finally {
      go.classList.remove("is-loading");
    }
  };
  go.addEventListener("click", load);
  return h("div", { class: "best-times" },
    h("div", { class: "best-head" }, h("span", { class: "muted small" }, `When your ${label} audience is most active.`), go), out);
}

// --------------------------------------------------------------------------- caption writer

const SAMPLE_PROBLEMS = {
  not_granted: "You didn't allow the panel to ask Claude, so it can't write for you in this view.",
  rate_limited: "Claude is busy right now. Try again in a minute.",
  sampling_disabled: "Asking Claude from pages is turned off for your account.",
  capability_disabled: "Asking Claude isn't available in this view.",
  capability_removed: "Asking Claude isn't available in this view.",
  not_declared: "This version of the panel can't ask Claude.",
  session_expired: "Your Claude session ran out. Reload the page and try again.",
  invalid_json: "Claude's answer came back garbled. Try again.",
  empty_completion: "Claude's answer came back empty. Try again.",
  refused: "Claude didn't write this one. Try adding a few words about the video.",
  unavailable: "Claude can't be asked from this view.",
};
export const sampleProblem = (err) => SAMPLE_PROBLEMS[err && err.code] || `Claude couldn't write it (${(err && (err.message || err.code)) || "unknown error"}).`;
export const canWrite = () => Boolean(P() && P().sample);

/**
 * A post written by Claude: {caption, hashtags, title}.
 * ctx = {title, script, about, handles}; `fresh` skips the five-minute answer cache.
 */
export async function writePost(ctx, { signal, fresh = false } = {}) {
  const sample = P().sample;
  if (!sample) throw Object.assign(new Error("unavailable"), { code: "unavailable" });
  const prompt = [
    "You write the post text for a short vertical video (Instagram Reel, YouTube Short and TikTok) on an account of",
    "late-night philosophical thoughts. Write in the same language as the script.",
    "",
    `Video title: ${ctx.title || "(none)"}`,
    ctx.handles && ctx.handles.length ? `Accounts: ${ctx.handles.join(", ")}` : "",
    ctx.about ? `What the video is about: ${ctx.about}` : "",
    ctx.script ? `Voiceover script:\n"""\n${String(ctx.script).slice(0, 6000)}\n"""` : "",
    "",
    "Write:",
    "- caption: 2 to 4 short lines that make people stop and think; the first line must work on its own as a hook;",
    "  end with a question or a reason to save the post; no hashtags; at most 400 characters.",
    "- hashtags: 5 to 8 hashtags that fit the video, without the # sign, mixing broad and niche ones.",
    "- youtube_title: a hook title for the Short, at most 90 characters, no hashtags, no quotes around it.",
    "",
    'Answer with JSON only: {"caption": "...", "hashtags": ["..."], "youtube_title": "..."}',
  ].filter((line) => line !== "").join("\n");
  const options = { modelTier: "default" };
  if (signal) options.signal = signal;
  if (fresh) options.cache = false;
  const out = await sample.json(prompt, options);
  const caption = String((out && out.caption) || "").trim().slice(0, 2200);
  if (!caption) throw Object.assign(new Error("no caption"), { code: "empty_completion" });
  const raw = Array.isArray(out.hashtags) ? out.hashtags : String(out.hashtags || "").split(/[\s,]+/);
  const hashtags = [...new Set(raw.map((t) => String(t).replace(/^#+/, "").replace(/\s+/g, "").trim()).filter(Boolean))].slice(0, 30);
  let title = String(out.youtube_title || out.title || "").trim().replace(/^["“]|["”]$/g, "");
  if (title.length > 100) title = `${title.slice(0, 99).trimEnd()}…`;
  return { caption, hashtags, title };
}

// --------------------------------------------------------------------------- shared page bits

function head(title, lead, ...actions) {
  return h("header", { class: "home-head" },
    h("div", {}, h("h1", { class: "page-title" }, title), lead ? h("p", { class: "page-lead" }, lead) : null),
    actions.length ? h("div", { class: "row" }, actions) : null);
}

const pill = (label, tone, title) => h("span", { class: ["state-pill", `is-${tone}`], title: title || null }, label);
const dot = (p) => h("span", { class: `pdot pdot-${p}`, "aria-hidden": "true" });

function thumbFor(video, fallback) {
  if (video && video.thumb) return h("img", { class: "mini-thumb", src: video.thumb, alt: "" });
  if (fallback) {
    const img = h("img", { class: "mini-thumb", src: fallback, alt: "", referrerpolicy: "no-referrer" });
    img.addEventListener("error", () => img.replaceWith(h("span", { class: "mini-thumb is-empty", html: icon("film", { size: 18 }) })));
    return img;
  }
  return h("span", { class: "mini-thumb is-empty", html: icon("film", { size: 18 }) });
}

// --------------------------------------------------------------------------- Calendar

const STATUS = {
  PUBLISHED: ["Posted", "ok"], PENDING: ["Scheduled", "busy"], ERROR: ["Failed", "bad"], FAILED: ["Failed", "bad"],
};

function postView(raw) {
  const tz = (raw.publicationDate && raw.publicationDate.timezone) || viewerTz();
  const at = instantOf((raw.publicationDate && raw.publicationDate.dateTime) || "", tz);
  const providers = (raw.providers || []).map((p) => ({ network: p.network, status: String(p.status || "PENDING").toUpperCase(),
                                                        url: p.publicUrl || null, detail: p.detailedStatus || "" }));
  const waiting = providers.length && providers.every((p) => p.status === "PENDING");
  return { raw, at, tz, providers, draft: Boolean(raw.draft), waiting, text: raw.text || "",
           title: (raw.youtubeData && raw.youtubeData.title) || "" };
}

async function changeTime(post, brand, { draft = false, at = null } = {}) {
  const info = { ...post.raw, draft };
  if (at) info.publicationDate = { dateTime: isoIn(at, post.tz).slice(0, 19), timezone: post.tz };
  return metricool("updateScheduledPost", { id: String(post.raw.id), uuid: String(post.raw.uuid), blogId: brand.id,
                                            info: JSON.stringify(info) }, { text: true });
}

async function askTime(post, title) {
  const start = post.at > new Date() ? post.at : new Date(Date.now() + 15 * 60000);
  const input = h("input", { id: "cal-time", class: "input", type: "datetime-local", value: localInput(start), min: localInput(new Date()) });
  const value = await dialog({
    title,
    body: h("div", { class: "stack" },
      h("label", { class: "field-label", for: "cal-time" }, "Date and time"), input,
      h("p", { class: "field-help" }, `On your clock (${viewerTz()}). Metricool posts it then.`)),
    actions: [{ label: "Cancel", value: null }, { label: "Save", variant: "primary", submit: true, value: () => input.value }],
    initialFocus: "#cal-time",
  });
  if (!value) return null;
  const at = new Date(value);
  if (!(at > new Date(Date.now() + 60000))) {
    toast("Pick a time in the future.", { kind: "error" });
    return null;
  }
  return at;
}

function calendarPost(post, brand, findVideo, refresh) {
  const video = findVideo ? findVideo(post.text) || findVideo(post.title) : null;
  const actions = [];
  const run = async (btn, fn, done) => {
    btn.classList.add("is-loading");
    try {
      await fn();
      toast(done, { kind: "success", timeout: 2500 });
      refresh();
    } catch (err) {
      toastError("Metricool didn't take the change", { message: metricoolProblem(err) });
    } finally {
      btn.classList.remove("is-loading");
    }
  };
  if (post.waiting && !post.draft && post.at > new Date()) {
    const move = button("Change time", { icon: "clock", size: "sm", variant: "secondary" });
    move.addEventListener("click", async () => {
      const at = await askTime(post, "Change when it posts");
      if (at) run(move, () => changeTime(post, brand, { at }), `Moved to ${at.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}.`);
    });
    const hold = button("Don't post", { icon: "pause", size: "sm", variant: "ghost" });
    hold.addEventListener("click", async () => {
      const ok = await dialog({ title: "Hold this post back?", body: h("p", { class: "dialog-text" }, "It stays in Metricool as a draft and isn't posted. You can schedule it again from here."),
        actions: [{ label: "Keep it scheduled", value: null }, { label: "Hold back", variant: "primary", value: true }] });
      if (ok) run(hold, () => changeTime(post, brand, { draft: true }), "Held back: it's a draft in Metricool now.");
    });
    actions.push(move, hold);
  } else if (post.draft) {
    const again = button("Schedule again", { icon: "calendar", size: "sm", variant: "secondary" });
    again.addEventListener("click", async () => {
      const at = await askTime(post, "When should it post?");
      if (at) run(again, () => changeTime(post, brand, { at, draft: false }), "Scheduled again.");
    });
    actions.push(again);
  }
  const links = post.providers.filter((p) => p.url).map((p) =>
    h("a", { class: "btn btn-ghost btn-sm", href: p.url, target: "_blank", rel: "noopener" }, `Open on ${NETWORK_LABEL[p.network] || p.network}`));
  return h("li", { class: "cal-post" },
    thumbFor(video),
    h("div", { class: "cal-post-body" },
      h("div", { class: "cal-post-top" },
        h("strong", { class: "timecode" }, when(post.at)),
        post.draft ? pill("Draft · won't post", "idle") : null,
        ...post.providers.map((p) => {
          const [label, tone] = STATUS[p.status] || [p.detail || p.status, "warn"];
          return h("span", { class: "cal-net" }, dot(p.network), NETWORK_LABEL[p.network] || p.network, " ", pill(label, tone, p.detail));
        })),
      video ? h("a", { class: "cal-video", href: `#/x/publish/${encodeURIComponent(video.key)}` }, video.title) : null,
      h("p", { class: "cal-text" }, post.title && post.title !== post.text.split("\n")[0] ? post.title : post.text.split("\n")[0]),
      actions.length || links.length ? h("div", { class: "row row-wrap" }, ...actions, ...links) : null));
}

export function renderCalendar(container, { findVideo } = {}) {
  let month = new Date();
  month = new Date(month.getFullYear(), month.getMonth(), 1);
  let videoLookup = null;
  const title = h("h2", { class: "cal-title", "aria-live": "polite" });
  const prev = button("Previous month", { icon: "chevronLeft", size: "sm", variant: "ghost", iconOnly: true });
  const next = button("Next month", { icon: "chevronRight", size: "sm", variant: "ghost", iconOnly: true });
  const today = button("Today", { size: "sm", variant: "ghost" });
  const refresh = button("Refresh", { icon: "refresh", size: "sm", variant: "ghost", iconOnly: true });
  const grid = h("ol", { class: "cal-grid", "aria-label": "Days of the month" });
  const agenda = h("div", { class: "stack-lg cal-agenda" });
  const note = h("div", {});
  prev.addEventListener("click", () => { month = new Date(month.getFullYear(), month.getMonth() - 1, 1); paint(); });
  next.addEventListener("click", () => { month = new Date(month.getFullYear(), month.getMonth() + 1, 1); paint(); });
  today.addEventListener("click", () => { const d = new Date(); month = new Date(d.getFullYear(), d.getMonth(), 1); paint(); });
  refresh.addEventListener("click", () => paint());

  container.append(
    head("Calendar", "What Metricool posts to Instagram and YouTube, and when. Move a waiting post, or hold it back. TikTok posts go out straight from Publish.",
      h("a", { class: "btn btn-secondary", href: "#/x/publish", html: `${icon("upload", { size: 18 })}<span class="btn-label">New post</span>` })),
    h("div", { class: "stack-lg" },
      h("section", { class: "card cal-card" },
        h("div", { class: "cal-bar" }, title, h("div", { class: "row" }, today, prev, next, refresh)),
        h("div", { class: "cal-week", "aria-hidden": "true" }, DAY_NAMES.map((d) => h("span", {}, d))),
        grid, note),
      agenda));

  let seq = 0;
  async function paint() {
    const mine = ++seq;
    title.textContent = month.toLocaleDateString(undefined, { month: "long", year: "numeric" });
    grid.setAttribute("aria-busy", "true");
    const first = month;
    const start = addDays(first, -((first.getDay() + 6) % 7));
    const last = new Date(month.getFullYear(), month.getMonth() + 1, 0);
    const end = addDays(last, 6 - ((last.getDay() + 6) % 7));
    let posts = [];
    let brand = null;
    try {
      brand = await metricoolBrand();
      if (findVideo && !videoLookup) videoLookup = await findVideo().catch(() => null);
      const p = await metricool("getScheduledPosts", { brandId: brand.id, fromDate: isoIn(start, brand.timezone),
        toDate: isoIn(new Date(addDays(end, 1).getTime() - 1000), brand.timezone), timezone: brand.timezone });
      posts = ((p && p.data) || []).map(postView).filter((x) => !Number.isNaN(x.at.getTime())).sort((a, b) => a.at - b.at);
      note.replaceChildren();
    } catch (err) {
      if (mine !== seq) return;
      note.replaceChildren(metricoolCallout(err, paint));
    }
    if (mine !== seq) return;
    grid.removeAttribute("aria-busy");
    const byDay = new Map();
    for (const post of posts) {
      const k = dayKey(post.at);
      if (!byDay.has(k)) byDay.set(k, []);
      byDay.get(k).push(post);
    }
    const todayKey = dayKey(new Date());
    const cells = [];
    for (let d = start; d <= end; d = addDays(d, 1)) {
      const k = dayKey(d);
      const list = byDay.get(k) || [];
      const label = d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
      const inner = [h("span", { class: "cal-num" }, String(d.getDate())),
        h("span", { class: "cal-dots", "aria-hidden": "true" }, list.slice(0, 4).map((x) => h("span", { class: ["cal-dot", x.draft ? "is-draft" : x.waiting ? "is-waiting" : "is-done"] })))];
      cells.push(h("li", { class: ["cal-day", d.getMonth() !== month.getMonth() && "is-other", k === todayKey && "is-today", list.length && "has-posts"] },
        list.length
          ? h("a", { class: "cal-day-link", href: `#cal-${k}`, "aria-label": `${label}: ${list.length} post${list.length === 1 ? "" : "s"}`,
                     onclick: (e) => { e.preventDefault(); const t = document.getElementById(`cal-${k}`); if (t) { t.scrollIntoView({ behavior: "smooth", block: "start" }); t.focus({ preventScroll: true }); } } }, inner)
          : h("span", { class: "cal-day-link", "aria-label": label }, inner)));
    }
    grid.replaceChildren(...cells);
    const inMonth = posts.filter((x) => x.at.getMonth() === month.getMonth());
    const groups = [...new Set(inMonth.map((x) => dayKey(x.at)))];
    agenda.replaceChildren(...(groups.length ? groups.map((k) => {
      const list = byDay.get(k);
      return h("section", { class: "cal-group", id: `cal-${k}`, tabindex: "-1" },
        h("h2", { class: "section-title" }, list[0].at.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }),
          k === todayKey ? h("span", { class: "count-pill" }, "Today") : null),
        h("ul", { class: "cal-list" }, list.map((post) => calendarPost(post, brand, videoLookup, paint))));
    }) : brand ? [h("p", { class: "muted empty-inline" }, "Nothing posted or scheduled through Metricool this month.")] : []));
  }
  paint();
}

// --------------------------------------------------------------------------- Stats

const METRICS = {
  instagram: [["date", "IGRE02"], ["text", "IGRE03"], ["url", "IGRE06"], ["image", "IGRE05"], ["views", "IGRE23"],
              ["likes", "IGRE10"], ["comments", "IGRE07"], ["saves", "IGRE12"], ["shares", "IGRE21"], ["reach", "IGRE11"]],
  youtube: [["date", "YTVP02"], ["text", "YTVP17"], ["url", "YTVP05"], ["image", "YTVP03"], ["views", "YTVP06"],
            ["likes", "YTVP09"], ["comments", "YTVP11"], ["shares", "YTVP12"], ["avg", "YTVP08"]],
};
const NUMBERS = ["views", "likes", "comments", "saves", "shares", "reach", "avg"];
const SORTS = [["newest", "Newest"], ["views", "Most views"], ["likes", "Most likes"], ["shares", "Most shares"], ["rate", "Best engagement"]];
const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const num = (v) => (v == null ? "—" : compact.format(v));
const plural = (n, word) => `${num(n)} ${word}${n === 1 ? "" : "s"}`;

function parseStamp(s) {
  const m = String(s || "").match(/^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$/);
  return m ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6])) : (s ? new Date(s) : null);
}

/** One reel's numbers from a Metricool analytics row. */
function statRow(network, row) {
  const out = { network };
  METRICS[network].forEach(([name], i) => {
    const v = row[i] ?? null;
    out[name] = NUMBERS.includes(name) ? (v == null || v === "" ? null : Number(v)) : v;
  });
  out.date = parseStamp(out.date);
  const engaged = ["likes", "comments", "saves", "shares"].reduce((s, k) => s + (out[k] || 0), 0);
  const base = network === "instagram" ? (out.reach || out.views) : out.views;
  out.rate = base ? engaged / base : null;
  return out;
}

export async function loadStats(network, days) {
  const brand = await metricoolBrand();
  const now = new Date();
  const p = await metricool("getAnalyticsDataByMetrics", { brandId: brand.id, from: isoIn(new Date(now.getTime() - days * 864e5), brand.timezone),
                                                           to: isoIn(now, brand.timezone), metrics: METRICS[network].map(([, code]) => code) });
  return ((p && p.rows) || []).filter((r) => Array.isArray(r) && r[0]).map((r) => statRow(network, r));
}

export function renderStats(container, { findVideo } = {}) {
  let network = "instagram";
  let days = 30;
  let sort = "newest";
  let rows = [];
  let videoLookup = null;
  const tabs = segmented({ label: "Platform", value: network, options: ["instagram", "youtube"].map((n) => ({ value: n, label: NETWORK_LABEL[n] })),
                           onChange: (v) => { network = v; load(); } });
  const range = h("select", { class: "input select", "aria-label": "Period" },
    [[7, "Last 7 days"], [30, "Last 30 days"], [90, "Last 90 days"]].map(([v, l]) => h("option", { value: v, selected: v === days }, l)));
  range.addEventListener("change", () => { days = Number(range.value); load(); });
  const sorter = h("select", { class: "input select", "aria-label": "Sort reels by" }, SORTS.map(([v, l]) => h("option", { value: v }, l)));
  sorter.addEventListener("change", () => { sort = sorter.value; paintList(); });
  const refresh = button("Refresh", { icon: "refresh", size: "sm", variant: "ghost", iconOnly: true, onClick: () => load() });
  const tiles = h("div", { class: "stat-tiles" });
  const chart = h("div", {});
  const list = h("ol", { class: "stat-list", "aria-label": "Your reels" });
  const note = h("div", {});

  container.append(
    head("Stats", "How your reels are doing on Instagram and YouTube, from Metricool."),
    h("div", { class: "stack-lg" },
      h("div", { class: "stat-bar" }, tabs, h("div", { class: "row row-wrap" }, range, sorter, refresh)),
      note, tiles, chart,
      h("section", { class: "card card-flush" }, list)));

  let seq = 0;
  async function load() {
    const mine = ++seq;
    list.setAttribute("aria-busy", "true");
    try {
      if (findVideo && !videoLookup) videoLookup = await findVideo().catch(() => null);
      rows = await loadStats(network, days);
      if (mine !== seq) return;
      note.replaceChildren(network === "youtube" && rows.length && rows.every((r) => r.views == null)
        ? callout("info", "YouTube's numbers reach Metricool a day or two after posting; they show up here then.") : "");
    } catch (err) {
      if (mine !== seq) return;
      rows = [];
      note.replaceChildren(metricoolCallout(err, load));
    }
    list.removeAttribute("aria-busy");
    paintTiles();
    paintList();
  }

  function paintTiles() {
    const sum = (k) => rows.reduce((s, r) => s + (r[k] || 0), 0);
    const known = rows.filter((r) => r.views != null);
    const best = known.slice().sort((a, b) => b.views - a.views)[0];
    const rated = rows.filter((r) => r.rate != null);
    const rate = rated.length ? rated.reduce((s, r) => s + r.rate, 0) / rated.length : null;
    const tile = (label, value, sub) => h("div", { class: "stat-tile" }, h("span", { class: "stat-label" }, label), h("strong", { class: "stat-value" }, value), sub ? h("span", { class: "muted small" }, sub) : null);
    tiles.replaceChildren(
      tile("Reels", String(rows.length), `in the last ${days} days`),
      tile("Views", known.length ? num(sum("views")) : "—", known.length ? `${num(Math.round(sum("views") / known.length))} per reel` : null),
      tile("Likes", num(sum("likes")), network === "instagram" ? `${plural(sum("saves"), "save")} · ${plural(sum("shares"), "share")}` : plural(sum("comments"), "comment")),
      tile("Engagement", rate == null ? "—" : `${(rate * 100).toFixed(1)} %`, network === "instagram" ? "likes, comments, saves and shares per person reached" : "likes, comments and shares per view"),
      best ? tile("Top reel", num(best.views), (best.text || "").split("\n")[0].slice(0, 60)) : null);
    const chrono = known.slice().sort((a, b) => a.date - b.date);
    const max = Math.max(1, ...chrono.map((r) => r.views));
    chart.replaceChildren(chrono.length > 1
      ? h("section", { class: "card stat-chart" },
          h("div", { class: "card-head" }, h("h2", { class: "card-title" }, "Views per reel"), h("p", { class: "card-sub" }, "Oldest on the left.")),
          h("div", { class: "bars", role: "img", "aria-label": `Views per reel: ${chrono.map((r) => num(r.views)).join(", ")}` },
            chrono.map((r) => h("span", { class: "bar", style: `--h:${(r.views / max).toFixed(3)}`, title: `${r.date ? r.date.toLocaleDateString() : ""}: ${num(r.views)} views` },
              h("span", { class: "bar-value" }, num(r.views))))))
      : "");
  }

  function paintList() {
    const by = {
      newest: (a, b) => (b.date || 0) - (a.date || 0),
      views: (a, b) => (b.views ?? -1) - (a.views ?? -1),
      likes: (a, b) => (b.likes ?? -1) - (a.likes ?? -1),
      shares: (a, b) => (b.shares ?? -1) - (a.shares ?? -1),
      rate: (a, b) => (b.rate ?? -1) - (a.rate ?? -1),
    }[sort];
    if (!rows.length) {
      list.replaceChildren(h("li", { class: "empty-inline muted" }, `No ${NETWORK_LABEL[network]} posts in the last ${days} days yet.`));
      return;
    }
    const metric = (one, many, v) => h("span", { class: "stat-metric" }, h("strong", {}, num(v)), ` ${v === 1 ? one : many}`);
    list.replaceChildren(...rows.slice().sort(by).map((r) => {
      const video = videoLookup ? videoLookup(r.text) : null;
      const metrics = network === "instagram"
        ? [metric("view", "views", r.views), metric("like", "likes", r.likes), metric("comment", "comments", r.comments),
           metric("save", "saves", r.saves), metric("share", "shares", r.shares), metric("reached", "reached", r.reach)]
        : [metric("view", "views", r.views), metric("like", "likes", r.likes), metric("comment", "comments", r.comments),
           metric("share", "shares", r.shares),
           r.avg != null ? h("span", { class: "stat-metric" }, h("strong", {}, formatSeconds(r.avg)), " watched on average") : null];
      return h("li", { class: "stat-row" },
        thumbFor(video, r.image),
        h("div", { class: "stat-main" },
          h("p", { class: "stat-text" }, (r.text || (video && video.title) || "Untitled").split("\n")[0]),
          h("p", { class: "muted small" }, [r.date ? r.date.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : null,
            r.rate != null ? `${(r.rate * 100).toFixed(1)} % engagement` : null].filter(Boolean).join(" · ")),
          h("div", { class: "stat-metrics" }, metrics)),
        r.url ? h("a", { class: "btn btn-ghost btn-sm", href: r.url, target: "_blank", rel: "noopener" }, "Open") : null);
    }));
  }
  load();
}
