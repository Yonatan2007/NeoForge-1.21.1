/**
 * Replies: a viewer's comment on one of your reels, with replies drafted by
 * Claude in the account's voice (the sample capability, on the viewer's own
 * Claude usage) to copy and post in the app. A comment worth a whole video
 * becomes a video reply: saved with status "idea", the next autopilot run
 * writes a reel that answers it (reelforge/autopilot.py, "replies").
 *
 *   replies/<id>         {account, reel_id, reel_title, platform, comment, drafts: [...], video: {hook, angle} | null,
 *                         status: "draft" | "idea", created, updated}: the reel's account answers it
 *   autopilot/<slot id>  a reel that answers one carries reply_to = <id>: shown here as answered
 *
 * No platform lets an app read or post comments for a personal account, so
 * the comment is pasted and the reply posted by hand.
 */

import { api } from "./api.js";
import { callout } from "./components.js";
import { button, confirmDialog, h, relativeTime, toast, toastError } from "./dom.js";
import { segmented } from "./fields.js";
import { icon } from "./icons.js";
import { canWrite, sampleProblem } from "./panel-growth.js";
import { accountName, accountOf } from "./panel-autopilot.js";

const P = () => api._panel;
const NETS = { tiktok: "TikTok", instagram: "Instagram", youtube: "YouTube" };
const slotDate = (it) => new Date(`${it.slot}:00`);
const short = (d) => d.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

function head(title, lead) {
  return h("header", { class: "home-head" },
    h("div", {}, h("h1", { class: "page-title" }, title), lead ? h("p", { class: "page-lead" }, lead) : null));
}

/** Three replies in the account's voice, and a video reply when the comment deserves one. */
export async function draftReplies({ comment, platform, reel }, { signal } = {}) {
  const sample = P().sample;
  if (!sample) throw Object.assign(new Error("unavailable"), { code: "unavailable" });
  const prompt = [
    "You help the person behind an account of late-night philosophical short videos (love, loss, self-worth,",
    "becoming who you are) answer the comments on their videos. Write in the commenter's language, as the account:",
    "calm, warm, honest, a little poetic, never preachy, no emojis unless the commenter used some, never generic",
    "(no \"Thanks!\", no \"Glad you liked it\").",
    "",
    reel ? `The video: "${reel.title || ""}"` : "",
    reel && reel.script ? `Its voiceover:\n"""\n${String(reel.script).slice(0, 3000)}\n"""` : "",
    `A viewer commented on ${NETS[platform] || "the video"}:\n"""\n${String(comment).slice(0, 1500)}\n"""`,
    "",
    "Write:",
    "- replies: 3 different replies, each under 200 characters, that keep the conversation going (one may ask",
    "  something back).",
    "- video: when the comment asks or reveals something worth a whole video, the first line of a short video that",
    "  answers it (under 14 words) as hook, and in one sentence the angle; otherwise null.",
    "",
    'Answer with JSON only: {"replies": ["...", "...", "..."], "video": {"hook": "...", "angle": "..."} or null}',
  ].filter((line) => line !== "").join("\n");
  const options = { modelTier: "default", cache: false };
  if (signal) options.signal = signal;
  const out = await sample.json(prompt, options);
  const replies = (Array.isArray(out && out.replies) ? out.replies : []).map((r) => String(r).trim()).filter(Boolean).slice(0, 3);
  if (!replies.length) throw Object.assign(new Error("no replies"), { code: "empty_completion" });
  const v = out.video && out.video.hook
    ? { hook: String(out.video.hook).trim().slice(0, 160), angle: String(out.video.angle || "").trim().slice(0, 400) } : null;
  return { replies, video: v };
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied. Paste it as your reply in the app.", { kind: "success", timeout: 2200 });
  } catch {
    toast("Copying isn't allowed in this view: press and hold the text to copy it.", { kind: "error" });
  }
}

export function renderReplies(container) {
  const { db, retrying, nowIso } = P();
  let reels = []; // posted autopilot reels, newest first
  let saved = []; // replies/<id>, newest first
  let answeredBy = new Map(); // reply id -> the reel that answers it
  let platform = "tiktok";
  let running = null; // AbortController of the draft being written
  const result = h("div", { class: "stack" });
  const list = h("div", { class: "stack" });

  const reelSelect = h("select", { class: "input select", "aria-label": "The reel it's on" });
  const comment = h("textarea", { class: "input textarea", rows: 3, placeholder: "Paste the comment here", "aria-label": "The comment" });
  const nets = segmented({ label: "Where it was left", value: platform,
    options: Object.entries(NETS).map(([value, label]) => ({ value, label })), onChange: (v) => { platform = v; } });
  const write = button("Write replies", { icon: "sparkle", variant: "primary" });
  write.addEventListener("click", () => draft());
  if (!canWrite()) write.disabled = true;

  container.append(
    head("Replies", "Paste a comment someone left on a reel. Claude drafts replies in your voice to copy into the app, and a comment worth a whole video becomes a video reply in the next autopilot run."),
    h("div", { class: "stack-lg" },
      h("section", { class: "card", "aria-labelledby": "rp-new" },
        h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "rp-new" }, "A new comment"),
          h("p", { class: "card-sub" }, "Replies from you, especially early ones, tell the platforms people are talking under your reel.")),
        h("div", { class: "form-grid reply-form" },
          h("div", { class: "field" }, h("span", { class: "field-label" }, "On the reel"), reelSelect),
          h("div", { class: "field" }, h("span", { class: "field-label" }, "Left on"), nets)),
        h("div", { class: "field" }, h("span", { class: "field-label" }, "The comment"), comment),
        h("div", { class: "row" }, write),
        canWrite() ? null : callout("info", "Asking Claude isn't available in this view, so replies can't be drafted here."),
        result),
      h("section", { class: "stack", "aria-labelledby": "rp-saved" },
        h("h2", { class: "section-title", id: "rp-saved" }, "Comments"), list)));

  const reelOf = (id) => reels.find((r) => r.id === id) || null;

  function draftView(doc) {
    const reel = reelOf(doc.reel_id);
    const answer = answeredBy.get(doc.id);
    let videoPart = null;
    if (doc.video) {
      const make = doc.status === "idea" || answer ? null : button("Make it a video reply", { icon: "wand", size: "sm", variant: "primary",
        onClick: () => setStatus(doc, "idea", "Saved: the next autopilot run answers it in a reel.") });
      const undo = doc.status === "idea" && !answer ? button("Don't make it", { size: "sm", variant: "ghost",
        onClick: () => setStatus(doc, "draft", "It stays a comment.") }) : null;
      const state = answer ? `Answered in the reel of ${short(slotDate(answer))}.`
        : doc.status === "idea" ? "In the next autopilot run." : "Worth a video reply:";
      videoPart = h("div", { class: "reply-video" },
        h("p", { class: "small muted" }, state),
        h("p", {}, h("strong", {}, doc.video.hook)),
        doc.video.angle ? h("p", { class: "small" }, doc.video.angle) : null,
        make || undo ? h("div", { class: "row row-wrap" }, make, undo) : null);
    }
    return h("li", { class: ["reply-item", doc.status === "idea" && !answer && "is-idea"] },
      h("p", { class: "small muted" }, [NETS[doc.platform] || doc.platform, reel ? `on “${reel.title}”` : doc.reel_title ? `on “${doc.reel_title}”` : null,
        doc.created ? relativeTime(doc.created) : null].filter(Boolean).join(" · ")),
      h("p", { class: "reply-comment" }, `“${doc.comment}”`),
      h("ul", { class: "reply-drafts" }, (doc.drafts || []).map((text) => h("li", {},
        h("p", {}, text), button("Copy", { icon: "copy", iconOnly: true, size: "sm", variant: "ghost", onClick: () => copy(text) })))),
      videoPart,
      h("div", { class: "row" }, button("Delete", { icon: "trash", size: "sm", variant: "ghost", onClick: () => remove(doc) })));
  }

  function paintList() {
    list.replaceChildren(saved.length
      ? h("ul", { class: "reply-list" }, saved.map(draftView))
      : h("p", { class: "muted empty-inline" }, "No comments yet. Paste one above."));
  }

  function paintReels() {
    reelSelect.replaceChildren(
      h("option", { value: "" }, reels.length ? "Choose the reel" : "No posted reels yet"),
      ...reels.map((r) => h("option", { value: r.id },
        `${short(slotDate(r))} · ${accountOf(r) === "main" ? "" : `${accountName(accountOf(r))}: `}${r.title || "Untitled"}`)));
  }

  async function draft() {
    const text = comment.value.trim();
    if (!text) return toast("Paste the comment first.", { kind: "error" });
    if (running) running.abort();
    running = new AbortController();
    const reel = reelOf(reelSelect.value);
    write.classList.add("is-loading");
    result.replaceChildren(h("p", { class: "muted" }, "Claude is writing…"));
    try {
      const out = await draftReplies({ comment: text, platform, reel }, { signal: running.signal });
      const id = `r-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
      const doc = { account: reel ? accountOf(reel) : "main", reel_id: reel ? reel.id : null, reel_title: reel ? reel.title : null,
                    platform, comment: text,
                    drafts: out.replies, video: out.video, status: "draft", created: nowIso(), updated: nowIso() };
      await retrying(() => db.doc(`replies/${id}`).set(doc));
      saved = [{ id, ...doc }, ...saved];
      comment.value = "";
      result.replaceChildren();
      paintList();
      toast(out.video ? "Replies written. This one could also be a video reply." : "Replies written.", { kind: "success", timeout: 2500 });
    } catch (err) {
      result.replaceChildren(err && err.code === "cancelled" ? "" : callout("warn", sampleProblem(err)));
    } finally {
      write.classList.remove("is-loading");
      running = null;
    }
  }

  async function setStatus(doc, status, message) {
    try {
      await retrying(() => db.doc(`replies/${doc.id}`).update({ status, updated: nowIso() }));
      doc.status = status;
      paintList();
      toast(message, { kind: "success", timeout: 2500 });
    } catch (err) {
      toastError("Could not save that", err);
    }
  }

  async function remove(doc) {
    const ok = await confirmDialog({ title: "Delete this comment?", message: "Its drafts go too. A video reply already made stays.",
                                     confirmLabel: "Delete", danger: true });
    if (!ok) return;
    try {
      await retrying(() => db.doc(`replies/${doc.id}`).delete());
      saved = saved.filter((x) => x.id !== doc.id);
      paintList();
    } catch (err) {
      toastError("Could not delete it", err);
    }
  }

  (async () => {
    try {
      const [ap, rs] = await Promise.all([retrying(() => db.collection("autopilot").get()), retrying(() => db.collection("replies").get())]);
      const all = ap.docs.map((d) => ({ id: d.id, ...d.data() }));
      const now = new Date();
      reels = all.filter((r) => r.slot && slotDate(r) <= now && r.status !== "failed").sort((a, b) => String(b.slot).localeCompare(String(a.slot)));
      answeredBy = new Map(all.filter((r) => r.reply_to).map((r) => [r.reply_to, r]));
      saved = rs.docs.map((d) => ({ id: d.id, ...d.data() })).sort((a, b) => String(b.created).localeCompare(String(a.created)));
    } catch (err) {
      toastError("Could not load your comments", err);
    }
    paintReels();
    paintList();
  })();
  return () => running && running.abort();
}
