/**
 * More control panel pieces:
 *
 *   Ideas          scripts written by Google Gemini in the account's voice
 *                  (an "ideas" task: Claude calls Gemini with the key kept in
 *                  its environment), each one a click away from a new reel
 *                    ideas/<task id>   {created, topic, model, ideas: [{title, script, why}]}
 *                    settings/ideas    {last_task}
 *   Render several reels at once (one message to Claude for the whole batch)
 *                    settings/batch    {tasks: {project id: task id}, created}
 *   coverCard      the frame Instagram and YouTube show before the video plays
 *                    posts/<key>.cover {ms, asset}
 *   viralityCard   Higgsfield's Virality Predictor on the video (a "virality" task)
 *                    posts/<key>.last_virality = task id; result {score, verdict, strengths, risks, tips}
 *   checkTiktok    reads a TikTok post's processing state from Higgsfield
 */

import { api } from "./api.js";
import { callout } from "./components.js";
import { button, confirmDialog, copyText, h, relativeTime, toast, toastError } from "./dom.js";
import { icon } from "./icons.js";

const P = () => api._panel;
const sentToast = (problem, ask, done) => toast(problem ? `Saved. Claude wasn't reached: send “${ask}” in your Claude chat.` : done,
  { kind: problem ? "error" : "success" });

function head(title, lead, ...actions) {
  return h("header", { class: "home-head" },
    h("div", {}, h("h1", { class: "page-title" }, title), lead ? h("p", { class: "page-lead" }, lead) : null),
    actions.length ? h("div", { class: "row" }, actions) : null);
}

/** A one-line status for a task (queued / running / error / done). */
function taskLine(t, doneText) {
  if (!t) return "";
  if (["queued", "running"].includes(t.status)) return t.message || "Waiting for Claude…";
  if (t.status === "error") return `It didn't work: ${t.error || t.message || "unknown error"}`;
  if (t.status === "cancelled") return "Cancelled.";
  return doneText || t.message || "Done.";
}

// --------------------------------------------------------------------------- Ideas

const TONES = [["", "Like my best reels"], ["gentle and comforting", "Gentle"], ["blunt and confronting", "Blunt"],
               ["hopeful and uplifting", "Hopeful"], ["dark and haunting", "Haunting"]];

async function makeReel(idea) {
  const created = await api.createProject({ name: idea.title });
  await api.saveProject(created.id, { ...created.project, name: idea.title, script: idea.script });
  location.hash = `#/p/${encodeURIComponent(created.id)}/script`;
}

/** The script with its *marked* words in bold (how reelforge highlights them). */
const marked = (text) => String(text).split(/(\*\*?[^*\s][^*]*?\*\*?)/).filter(Boolean)
  .map((part) => (/^\*.*\*$/.test(part) ? h("strong", {}, part.replace(/^\*+|\*+$/g, "")) : part));

function ideaCard(idea, onRemove) {
  const script = h("p", { class: "idea-script" }, marked(idea.script));
  const more = button("Show all", { size: "sm", variant: "ghost" });
  more.addEventListener("click", () => {
    const open = script.classList.toggle("is-open");
    more.querySelector(".btn-label").textContent = open ? "Show less" : "Show all";
  });
  const make = button("Make a reel", { icon: "plus", size: "sm", variant: "primary" });
  make.addEventListener("click", async () => {
    make.classList.add("is-loading");
    try {
      await makeReel(idea);
    } catch (err) {
      toastError("Could not make the reel", err);
      make.classList.remove("is-loading");
    }
  });
  return h("li", { class: "idea-card" },
    h("div", { class: "idea-head" }, h("h3", { class: "idea-title" }, idea.title),
      onRemove ? button("Remove this idea", { icon: "trash", size: "sm", variant: "ghost", iconOnly: true, class: "danger-hover", onClick: onRemove }) : null),
    script,
    idea.why ? h("p", { class: "muted small idea-why" }, h("span", { html: icon("sparkle", { size: 13 }) }), ` ${idea.why}`) : null,
    h("div", { class: "row row-wrap" }, make, more,
      button("Copy", { icon: "copy", size: "sm", variant: "ghost", onClick: async () => { await copyText(idea.script); toast("Script copied.", { kind: "success", timeout: 1500 }); } })));
}

export function renderIdeas(container) {
  const { db, retrying, newTask, nowIso } = P();
  const topic = h("input", { id: "idea-topic", class: "input", type: "text", placeholder: "e.g. letting go of someone who never chose you", autocomplete: "off" });
  const tone = h("select", { id: "idea-tone", class: "input select" }, TONES.map(([v, l]) => h("option", { value: v }, l)));
  const count = h("select", { id: "idea-count", class: "input select" }, [3, 5, 8].map((n) => h("option", { value: n, selected: n === 5 }, `${n} scripts`)));
  const go = button("Get ideas", { icon: "sparkle", variant: "primary" });
  const status = h("p", { class: "card-sub", role: "status", "aria-live": "polite" });
  const list = h("div", { class: "stack-lg" });
  let unsubTask = null;

  container.append(
    head("Ideas", "New scripts from Gemini in your account's voice. Pick one and it becomes a reel, ready for a voice and footage."),
    h("div", { class: "stack-lg" },
      h("section", { class: "card" },
        h("div", { class: "form-grid idea-form" },
          h("div", { class: "field idea-topic" }, h("label", { class: "field-label", for: "idea-topic" }, "Topic (optional)"), topic,
            h("p", { class: "field-help" }, "Leave it empty and Gemini picks the angles.")),
          h("div", { class: "field" }, h("label", { class: "field-label", for: "idea-tone" }, "Tone"), tone),
          h("div", { class: "field" }, h("label", { class: "field-label", for: "idea-count" }, "How many"), count)),
        h("div", { class: "row row-wrap idea-go" }, go, status)),
      list));

  const watch = (id) => {
    if (unsubTask) unsubTask();
    unsubTask = db.doc(`tasks/${id}`).onSnapshot((s) => {
      const t = s.exists ? s.data() : null;
      const busy = t && ["queued", "running"].includes(t.status);
      go.disabled = Boolean(busy);
      status.textContent = taskLine(t, t && t.result ? `Gemini wrote ${(t.result.ideas || []).length} scripts ${relativeTime(t.created)}.` : "");
      if (t && t.status === "done") paint();
    }, () => {});
  };

  go.addEventListener("click", async () => {
    go.classList.add("is-loading");
    try {
      const snap = await retrying(() => db.collection("projects").get());
      const scripts = snap.docs.map((d) => (d.data().project || {}).script || "").filter((x) => x.trim());
      const done = await retrying(() => db.collection("ideas").get()).catch(() => ({ docs: [] }));
      const avoid = [...scripts.map((x) => x.split(/(?<=[.?!])\s/)[0]),
        ...done.docs.flatMap((d) => (d.data().ideas || []).map((i) => i.title))].slice(0, 40);
      const { id, problem } = await newTask("ideas", null, {
        topic: topic.value.trim(), tone: tone.value, count: Number(count.value), avoid, examples: scripts.slice(-3) });
      await retrying(() => db.doc("settings/ideas").set({ last_task: id, updated: nowIso() }));
      watch(id);
      sentToast(problem, P().askFor("ideas"), "Sent to Claude: asking Gemini…");
    } catch (err) {
      toastError("Could not ask for ideas", err);
    } finally {
      go.classList.remove("is-loading");
    }
  });

  async function paint() {
    let batches = [];
    try {
      const snap = await retrying(() => db.collection("ideas").get());
      batches = snap.docs.map((d) => ({ id: d.id, ...d.data() })).sort((a, b) => String(b.created).localeCompare(String(a.created)));
    } catch (err) {
      toastError("Could not load your ideas", err);
    }
    list.replaceChildren(...(batches.length ? batches.map((b) => h("section", { class: "stack" },
      h("h2", { class: "section-title" }, b.topic ? `“${b.topic}”` : "Gemini's picks",
        h("span", { class: "muted small idea-meta" }, [b.created ? relativeTime(b.created) : null, b.model].filter(Boolean).join(" · "))),
      h("ul", { class: "idea-grid" }, (b.ideas || []).map((idea, i) => ideaCard(idea, async () => {
        const ideas = (b.ideas || []).filter((_, j) => j !== i);
        await retrying(() => (ideas.length ? db.doc(`ideas/${b.id}`).update({ ideas }) : db.doc(`ideas/${b.id}`).delete()));
        paint();
      }))))) : [h("p", { class: "muted empty-inline" }, "No ideas yet. Press “Get ideas”.")]));
  }

  retrying(() => db.doc("settings/ideas").get()).then((s) => s.exists && s.data().last_task && watch(s.data().last_task)).catch(() => {});
  paint();
  return () => unsubTask && unsubTask();
}

// --------------------------------------------------------------------------- Render several

export function renderBatch(container) {
  const { db, retrying, newTasks, nowIso } = P();
  const list = h("ul", { class: "batch-list", "aria-busy": "true" });
  const go = button("Render", { icon: "render", variant: "primary" });
  const pickAll = button("Select all", { size: "sm", variant: "ghost" });
  const chosen = new Set();
  const unsubs = [];
  let rows = [];
  let batch = { tasks: {} };

  container.append(
    head("Render several", "Pick the reels to make and Claude renders them one after another. Each keeps its own settings."),
    h("div", { class: "stack-lg" },
      h("section", { class: "card card-flush" },
        h("div", { class: "batch-bar" }, pickAll, go), list)));

  const repaintButton = () => {
    go.querySelector(".btn-label").textContent = chosen.size ? `Render ${chosen.size} reel${chosen.size === 1 ? "" : "s"}` : "Render";
    go.disabled = !chosen.size;
  };
  pickAll.addEventListener("click", () => {
    const all = rows.filter((r) => r.ready).every((r) => chosen.has(r.id));
    rows.filter((r) => r.ready).forEach((r) => (all ? chosen.delete(r.id) : chosen.add(r.id)));
    paintRows();
  });
  go.addEventListener("click", async () => {
    const ids = [...chosen];
    if (!ids.length) return;
    if (ids.length > 1 && !(await confirmDialog({ title: `Render ${ids.length} reels?`, message: "Each one makes its own Higgsfield voiceover, so this uses your Higgsfield credits for every reel whose script or voice changed.", confirmLabel: "Render them" }))) return;
    go.classList.add("is-loading");
    try {
      const { ids: tasks, problem, ask } = await newTasks("render", ids.map((projectId) => ({ projectId, extra: { batch: true } })));
      batch = { tasks: { ...batch.tasks, ...Object.fromEntries(ids.map((pid, i) => [pid, tasks[i]])) }, created: nowIso() };
      await retrying(() => db.doc("settings/batch").set(batch));
      chosen.clear();
      paintRows();
      sentToast(problem, ask, `Sent to Claude: ${ids.length} reel${ids.length === 1 ? "" : "s"} to render.`);
    } catch (err) {
      toastError("Could not start the renders", err);
    } finally {
      go.classList.remove("is-loading");
    }
  });

  function paintRows() {
    unsubs.splice(0).forEach((fn) => fn());
    list.removeAttribute("aria-busy");
    if (!rows.length) {
      list.replaceChildren(h("li", { class: "empty-inline muted" }, "No reels yet."));
      repaintButton();
      return;
    }
    list.replaceChildren(...rows.map((r) => {
      const state = h("span", { class: "batch-state" });
      const fill = h("div", { class: "progress-fill", style: { width: "0%" } });
      const bar = h("div", { class: "progress progress-thin", hidden: true }, fill);
      const box = h("input", { type: "checkbox", id: `batch-${r.id}`, class: "account-tick", checked: chosen.has(r.id), disabled: !r.ready });
      box.addEventListener("change", () => {
        if (box.checked) chosen.add(r.id);
        else chosen.delete(r.id);
        repaintButton();
      });
      const taskId = batch.tasks[r.id];
      state.replaceChildren(h("span", { class: ["state-pill", r.rendered ? "is-ok" : "is-idle"] }, r.rendered ? "Rendered" : "Not rendered"));
      if (taskId) {
        unsubs.push(db.doc(`tasks/${taskId}`).onSnapshot((s) => {
          const t = s.exists ? s.data() : null;
          if (!t) return;
          const busy = ["queued", "running"].includes(t.status);
          const [label, tone] = busy ? [t.status === "queued" ? "Waiting" : "Rendering", "busy"]
            : t.status === "done" ? ["Rendered just now", "ok"] : t.status === "error" ? ["Failed", "bad"] : ["Cancelled", "idle"];
          state.replaceChildren(h("span", { class: ["state-pill", `is-${tone}`], title: t.error || t.message || null }, label));
          bar.hidden = t.status !== "running";
          fill.style.width = `${Math.round((t.progress || 0) * 100)}%`;
          box.disabled = busy || !r.ready;
        }, () => {}));
      }
      return h("li", { class: "batch-row" },
        box,
        h("label", { class: "batch-main", for: `batch-${r.id}` },
          r.thumb ? h("img", { class: "mini-thumb", src: r.thumb, alt: "" }) : h("span", { class: "mini-thumb is-empty", html: icon("film", { size: 18 }) }),
          h("span", { class: "batch-text" }, h("strong", {}, r.name),
            h("span", { class: "muted small" }, r.ready ? r.first : "No script yet"), bar)),
        state,
        h("a", { class: "btn btn-ghost btn-sm", href: `#/p/${encodeURIComponent(r.id)}/render` }, "Open"));
    }));
    repaintButton();
  }

  (async () => {
    try {
      const [snap, b] = await Promise.all([retrying(() => db.collection("projects").get()),
        retrying(() => db.doc("settings/batch").get()).catch(() => null)]);
      if (b && b.exists) batch = { tasks: {}, ...b.data() };
      rows = snap.docs.map((d) => {
        const x = d.data();
        const script = ((x.project || {}).script || "").trim();
        return { id: d.id, name: x.name || "Untitled reel", updated: x.updated || x.created || "", ready: Boolean(script),
                 first: script.split(/(?<=[.?!])\s/)[0].slice(0, 110), rendered: Boolean(x.outputs && x.outputs.video),
                 thumb: x.outputs && x.outputs.cover ? P().blobUrl(x.outputs.cover) : null };
      }).sort((a, b2) => String(b2.updated).localeCompare(String(a.updated)));
    } catch (err) {
      toastError("Could not load your reels", err);
    }
    paintRows();
  })();
  return () => unsubs.forEach((fn) => fn());
}

// --------------------------------------------------------------------------- Cover

/** Pick the cover frame. `src` resolves to the video URL; onChange(cover|null) after a save. */
export function coverCard(post, src, save, onChange) {
  const video = h("video", { class: "cover-video", muted: true, playsinline: true, preload: "auto" }); // same-origin: the canvas can read it
  const range = h("input", { id: "cover-time", type: "range", class: "range", min: 0, max: 1, step: 0.04, value: 0, disabled: true });
  const time = h("span", { class: "field-value" }, "0.0 s");
  const chosen = h("div", { class: "cover-chosen" });
  const use = button("Use this frame", { icon: "check", size: "sm", variant: "primary" });
  const auto = button("Let the platforms choose", { size: "sm", variant: "ghost" });
  src.then((url) => url && (video.src = url));
  video.addEventListener("loadedmetadata", () => {
    const length = Number.isFinite(video.duration) ? video.duration : 60; // some recordings don't say how long they are
    range.max = String(Math.max(0.04, length - 0.05));
    range.disabled = false;
    const start = post.cover ? post.cover.ms / 1000 : Math.min(1.5, length / 2);
    range.value = String(start);
    video.currentTime = start;
    time.textContent = `${start.toFixed(1)} s`;
  });
  range.addEventListener("input", () => {
    video.currentTime = Number(range.value);
    time.textContent = `${Number(range.value).toFixed(1)} s`;
  });
  const paintChosen = () => {
    const c = post.cover;
    chosen.replaceChildren(c && c.asset
      ? h("figure", { class: "cover-pick" }, h("img", { src: P().blobUrl(c.asset), alt: "The chosen cover" }),
          h("figcaption", { class: "muted small" }, `Cover at ${(c.ms / 1000).toFixed(1)} s`))
      : h("p", { class: "muted small" }, "No cover chosen: each platform picks a frame itself."));
    auto.hidden = !c;
  };
  use.addEventListener("click", async () => {
    if (!video.videoWidth) return toast("The video is still loading.", { kind: "error" });
    use.classList.add("is-loading");
    try {
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0);
      const blob = await new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("the frame couldn't be read"))), "image/jpeg", 0.9));
      const up = await P().uploadAsset(blob, "image/jpeg");
      post.cover = { ms: Math.round(Number(range.value) * 1000), asset: up.id };
      save();
      paintChosen();
      onChange(post.cover);
      toast("Cover saved.", { kind: "success", timeout: 1800 });
    } catch (err) {
      toastError("Could not save the cover", err);
    } finally {
      use.classList.remove("is-loading");
    }
  });
  auto.addEventListener("click", () => {
    post.cover = null;
    save();
    paintChosen();
    onChange(null);
  });
  paintChosen();
  return h("section", { class: "card cover-card" },
    h("div", { class: "card-head" }, h("h2", { class: "card-title" }, "Cover"),
      h("p", { class: "card-sub" }, "The frame people see before the video plays. Instagram and YouTube use it; TikTok asks for its cover in its own publish form.")),
    h("div", { class: "cover-grid" },
      h("div", { class: "cover-frame" }, video),
      h("div", { class: "stack" },
        h("div", { class: "field" }, h("div", { class: "field-head" }, h("label", { class: "field-label", for: "cover-time" }, "Frame"), time), range),
        h("div", { class: "row row-wrap" }, use, auto), chosen)));
}

// --------------------------------------------------------------------------- Virality

function viralityResult(t) {
  const r = (t && t.result) || {};
  const score = typeof r.score === "number" ? Math.round(r.score) : null;
  const tone = score == null ? "idle" : score >= 70 ? "ok" : score >= 45 ? "warn" : "bad";
  const listOf = (title, items, kind) => (items && items.length
    ? h("div", { class: "stack" }, h("p", { class: "subsection-title" }, title),
        h("ul", { class: "checklist" }, items.map((x) => h("li", { class: ["check", `is-${kind}`] },
          h("span", { class: "check-icon", html: icon(kind === "ok" ? "check" : kind === "warn" ? "alert" : "info", { size: 14 }) }), h("span", {}, x)))))
    : null);
  return h("div", { class: "stack-lg" },
    h("div", { class: "viral-top" },
      score != null ? h("div", { class: ["viral-score", `is-${tone}`] }, h("strong", {}, String(score)), h("span", {}, "/ 100")) : null,
      h("div", { class: "stack" }, r.verdict ? h("p", { class: "viral-verdict" }, r.verdict) : null,
        h("p", { class: "muted small" }, `Higgsfield Virality Predictor · ${t.updated ? relativeTime(t.updated) : relativeTime(t.created)}`))),
    listOf("What works", r.strengths, "ok"), listOf("Where people may drop off", r.risks, "warn"), listOf("Try", r.tips, "info"));
}

export function viralityCard(post, key, video, save) {
  const body = h("div", {});
  const status = h("p", { class: "card-sub", role: "status", "aria-live": "polite" });
  const go = button(post.last_virality ? "Check again" : "Check it", { icon: "eye", size: "sm", variant: "secondary" });
  let unsub = null;
  const watch = (id) => {
    if (unsub) unsub();
    unsub = P().db.doc(`tasks/${id}`).onSnapshot((s) => {
      const t = s.exists ? s.data() : null;
      const busy = t && ["queued", "running"].includes(t.status);
      go.disabled = Boolean(busy);
      status.textContent = t && t.status === "done" ? "How Higgsfield expects people to react to the video." : taskLine(t);
      body.replaceChildren(t && t.status === "done" ? viralityResult(t) : "");
    }, () => {});
  };
  go.addEventListener("click", async () => {
    go.classList.add("is-loading");
    try {
      const [kind, id] = key.split(":");
      const { id: taskId, problem } = await P().newTask("virality", null, { post_id: key, video: { kind, id, title: video.title } });
      post.last_virality = taskId;
      save();
      watch(taskId);
      sentToast(problem, P().askFor("virality"), "Sent to Claude: checking the video…");
    } catch (err) {
      toastError("Could not start the check", err);
    } finally {
      go.classList.remove("is-loading");
    }
  });
  if (post.last_virality) watch(post.last_virality);
  else status.textContent = "Higgsfield's Virality Predictor scores the hook, attention and where people may scroll away. It uses Higgsfield credits.";
  const el = h("section", { class: "card viral-card" },
    h("div", { class: "card-head card-head-row" }, h("div", {}, h("h2", { class: "card-title" }, "Will it go viral?"), status), go),
    body);
  el.cleanup = () => unsub && unsub();
  return el;
}

// --------------------------------------------------------------------------- TikTok status

const TIKTOK_DONE = ["PUBLISH_COMPLETE", "PUBLISHED", "SUCCESS", "COMPLETE"];
const TIKTOK_FAILED = ["FAILED", "PUBLISH_FAILED", "ERROR"];

/** Ask Higgsfield how a TikTok post is doing and write it into the task. Resolves the new result entry. */
export async function checkTiktok(taskId, task) {
  const r = (task.result || {}).tiktok || {};
  if (!r.publish_id || !r.connector_id) throw new Error("This post has no TikTok publish id yet.");
  const res = await P().mcp.callTool("higgsfield", "tiktok_publish_status", { connector_id: r.connector_id, publish_id: r.publish_id });
  const p = res.payload || {};
  const status = String(p.status || p.publish_status || (p.data && p.data.status) || "").toUpperCase();
  const url = p.share_url || p.url || p.post_url || (p.publicaly_available_post_id && p.publicaly_available_post_id[0]
    ? `https://www.tiktok.com/video/${p.publicaly_available_post_id[0]}` : null) || r.url || null;
  const next = TIKTOK_DONE.includes(status) ? { ...r, status: "done", message: "Posted on TikTok", url }
    : TIKTOK_FAILED.includes(status) ? { ...r, status: "error", message: `TikTok: ${p.fail_reason || p.error || status.toLowerCase()}` }
    : { ...r, message: status ? `TikTok is processing it (${status.toLowerCase().replace(/_/g, " ")})` : r.message };
  await P().retrying(() => P().db.doc(`tasks/${taskId}`).update({ result: { ...(task.result || {}), tiktok: next } }));
  return next;
}

/** The user finished TikTok's form themselves: mark it posted. */
export async function markTiktokPosted(taskId, task) {
  const r = (task.result || {}).tiktok || {};
  await P().retrying(() => P().db.doc(`tasks/${taskId}`).update({ result: { ...(task.result || {}), tiktok: { ...r, status: "done", message: "Posted on TikTok (you confirmed it)" } } }));
}
