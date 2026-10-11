/**
 * Autopilot: Claude writes, voices, renders and schedules reels by itself,
 * a few a day at the audience's busiest hours (reelforge/autopilot.py).
 *
 *   settings/autopilot   the settings on this page (the same keys as autopilot.DEFAULTS)
 *   music/<id>           the music library (the Music page): "random" music deals from it
 *   autopilot/<slot id>  one reel of the queue: {slot, tz, status, title, caption, cover,
 *                        master_url, platforms, metricool: {id, uuid, plannerUrl}, problems}
 *   tasks/<id>           kind "autopilot" {days, per_day}: fill the coming days; Claude runs it
 *
 * Approving or holding back a queued reel happens here, through the viewer's
 * own Metricool connector; nothing else needs Claude between runs.
 */

import { api } from "./api.js";
import { callout } from "./components.js";
import { button, confirmDialog, debounce, h, relativeTime, toast, toastError } from "./dom.js";
import { segmented, switchControl } from "./fields.js";
import { icon } from "./icons.js";
import { metricool, metricoolBrand, metricoolProblem, isoIn } from "./panel-growth.js";
import { TONES } from "./panel-more.js";

const P = () => api._panel;
const DEFAULTS = {
  enabled: true, per_day: 3, days: 7, platforms: ["instagram", "youtube"], approval: "auto",
  window: ["08:00", "23:30"], min_gap: 3, template: null, format: "template", music: "random",
  topic: "", tone: "", ai_label: false,
};
const NETS = { instagram: "Instagram", youtube: "YouTube", tiktok: "TikTok" };
const CREDITS_PER_REEL = 2.4; // one Higgsfield voiceover
const STATUS = {
  planned: ["Being made", "busy"], review: ["Needs your OK", "warn"], scheduled: ["Scheduled", "ok"],
  posted: ["Posted", "ok"], posting: ["Posting…", "busy"], skipped: ["Won't post", "idle"],
  held: ["Held back", "warn"], failed: ["Failed", "bad"], ready: ["Made, not scheduled", "idle"],
};

function head(title, lead, ...actions) {
  return h("header", { class: "home-head" },
    h("div", {}, h("h1", { class: "page-title" }, title), lead ? h("p", { class: "page-lead" }, lead) : null),
    actions.length ? h("div", { class: "row" }, actions) : null);
}

const pill = (status) => {
  const [label, tone] = STATUS[status] || [status, "idle"];
  return h("span", { class: ["state-pill", `is-${tone}`] }, label);
};
const dot = (p) => h("span", { class: `pdot pdot-${p}`, title: NETS[p] || p, "aria-hidden": "true" });
const slotDate = (it) => new Date(`${it.slot}:00`); // wall-clock time in the brand's zone, shown as is
/** A run Claude is on, or was sent lately (one that never reached Claude doesn't block a new one for long). */
const busy = (t) => Boolean(t) && (t.status === "running"
  || (t.status === "queued" && Date.now() - new Date(t.created).getTime() < 15 * 60000));
const dayLabel = (d) => d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
const timeLabel = (d) => d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

/** A queued reel's state, using Metricool's answer when there is one. */
function liveStatus(it, live) {
  if (it.status !== "scheduled" && it.status !== "review") return it.status;
  const providers = (live && live.providers) || [];
  if (providers.length && providers.every((p) => p.status === "PUBLISHED")) return "posted";
  if (providers.some((p) => ["ERROR", "FAILED"].includes(p.status))) return "failed";
  if (live && live.draft) return it.status === "skipped" ? "skipped" : "review";
  if (slotDate(it) < new Date()) return "posting";
  return it.status;
}

export function renderAutopilot(container) {
  const { db, retrying, newTask, nowIso, blobUrl, listSongs } = P();
  let settings = { ...DEFAULTS };
  let items = [];
  let projects = [];
  let library = []; // songs in the shuffle
  let live = new Map(); // Metricool posts by uuid
  let brand = null;
  let credits = null;
  let task = null; // the latest autopilot run
  let pastOpen = false;
  let unsubTask = null;
  const status = h("div", { class: "card ap-status" });
  const form = h("div", { class: "card" });
  const queue = h("div", { class: "stack-lg" });
  const note = h("div", {});

  container.append(
    head("Autopilot", "Claude writes, voices, renders and schedules your reels by itself, at the hours your audience is most active.",
      h("a", { class: "btn btn-ghost", href: "#/x/batch", html: `${icon("render", { size: 18 })}<span class="btn-label">Render several</span>` })),
    h("div", { class: "stack-lg" }, status, note, form, queue));

  const save = debounce(() => retrying(() => db.doc("settings/autopilot").set({ ...settings, updated: nowIso() }))
    .catch((err) => toastError("Could not save the autopilot settings", err)), 500);
  const set = (patch, repaint = false) => {
    settings = { ...settings, ...patch };
    save();
    paintStatus();
    if (repaint) paintForm();
  };

  // ---- status card: how far the queue reaches, the next run, credits
  const statusLine = () => {
    if (task && ["queued", "running"].includes(task.status) && task.message) return task.message;
    if (busy(task)) return "Claude is making your reels…";
    if (task && task.status === "error") return `The last run failed: ${task.error || task.message}`;
    if (task && task.status === "done") return `Last run ${relativeTime(task.updated || task.created)}: ${task.message || "done"}.`;
    return `${settings.per_day} a day on ${settings.platforms.map((p) => NETS[p]).join(", ")}, ${settings.approval === "review" ? "after you approve each one" : "posted automatically"}.`;
  };
  function paintStatus() {
    const upcoming = items.filter((it) => slotDate(it) > new Date() && ["scheduled", "review", "planned"].includes(it.status));
    const last = upcoming.length ? slotDate(upcoming[upcoming.length - 1]) : null;
    const daysLeft = last ? Math.max(0, Math.ceil((last - new Date()) / 864e5)) : 0;
    const need = settings.per_day * settings.days;
    const fill = button(`Fill the next ${settings.days} days`, { icon: "wand", variant: "primary" });
    fill.disabled = !settings.enabled || busy(task);
    fill.addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: `Make up to ${need} reels?`,
        message: `Claude writes them with Gemini, voices them with Higgsfield (about ${Math.round(need * CREDITS_PER_REEL)} credits), renders them and schedules them on ${settings.platforms.map((p) => NETS[p]).join(", ")} at the best times${settings.approval === "review" ? " as drafts for you to approve" : ""}. Days that already have their reels are skipped.`,
        confirmLabel: "Make them",
      });
      if (!ok) return;
      fill.classList.add("is-loading");
      try {
        await retrying(() => db.doc("settings/autopilot").set({ ...settings, updated: nowIso() }));
        const { id, problem } = await newTask("autopilot", null, { days: settings.days, per_day: settings.per_day });
        await retrying(() => db.doc("settings/autopilot").update({ last_task: id }));
        settings.last_task = id;
        watchTask(id);
        toast(problem ? `Saved. Claude wasn't reached: send “${P().askFor("autopilot")}” in your Claude chat.` : "Sent to Claude: making your reels.",
          { kind: problem ? "error" : "success" });
      } catch (err) {
        toastError("Could not start the autopilot", err);
      } finally {
        fill.classList.remove("is-loading");
      }
    });
    const toggle = switchControl({ id: "ap-enabled", checked: settings.enabled, label: "Autopilot on",
      onChange: (v) => set({ enabled: v }) });
    const running = busy(task);
    status.replaceChildren(...[
      h("div", { class: "ap-status-top" },
        h("div", { class: "stack" },
          h("p", { class: "ap-big" }, upcoming.length
            ? `${upcoming.length} reel${upcoming.length === 1 ? "" : "s"} lined up, through ${last.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" })}`
            : "Nothing lined up yet"),
          h("p", { class: "muted" }, statusLine())),
        h("div", { class: "field field-bool" }, toggle)),
      upcoming.length && daysLeft <= 2 && !running
        ? callout("warn", `The queue runs out ${daysLeft <= 0 ? "today" : `in ${daysLeft} day${daysLeft === 1 ? "" : "s"}`}. Fill the next days to keep posting.`) : null,
      h("div", { class: "row row-wrap ap-actions" }, fill,
        h("span", { class: "muted small ap-credits" }, credits ? `Higgsfield credits: ${Math.round(credits)} · about ${Math.round(need * CREDITS_PER_REEL)} per run` : "")),
      h("p", { class: "muted small" }, "Claude makes the reels when you press the button (send “run autopilot” in your Claude chat if it isn't reached). Metricool posts them on time; you don't need to keep anything open."),
    ].filter(Boolean));
  }

  const watchTask = (id) => {
    if (unsubTask) unsubTask();
    unsubTask = db.doc(`tasks/${id}`).onSnapshot((s) => {
      const was = task && task.status;
      task = s.exists ? s.data() : null;
      paintStatus();
      if (task && task.status === "done" && was !== "done") loadQueue();
    }, () => {});
  };

  // ---- settings form
  function paintForm() {
    const field = (label, control, help) => h("div", { class: "field" },
      h("span", { class: "field-label" }, label), control, help ? h("p", { class: "field-help" }, help) : null);
    const perDay = segmented({ label: "Reels a day", value: settings.per_day,
      options: [1, 2, 3, 4, 5].map((n) => ({ value: n, label: String(n) })), onChange: (v) => set({ per_day: v }) });
    const days = h("select", { class: "input select", "aria-label": "Fill ahead" },
      [3, 7, 14].map((n) => h("option", { value: n, selected: n === settings.days }, `${n} days`)));
    days.addEventListener("change", () => set({ days: Number(days.value) }));
    const tiktokLinked = Boolean(brand && brand.networks && brand.networks.tiktokData);
    const nets = h("div", { class: "chips", role: "group", "aria-label": "Platforms" }, Object.entries(NETS).map(([p, label]) => {
      const id = `ap-net-${p}`;
      const off = p === "tiktok" && !tiktokLinked;
      const input = h("input", { type: "checkbox", id, class: "visually-hidden", checked: settings.platforms.includes(p) && !off, disabled: off });
      input.addEventListener("change", () => {
        const next = input.checked ? [...new Set([...settings.platforms, p])] : settings.platforms.filter((x) => x !== p);
        if (!next.length) {
          input.checked = true;
          return toast("Keep at least one platform.", { kind: "error" });
        }
        set({ platforms: next });
      });
      return [input, h("label", { class: ["chip", off && "is-off"], for: id, title: off ? "Link TikTok in Metricool first" : null },
        h("span", { class: "chip-check", html: icon("check", { size: 14 }) }), dot(p), label, off ? h("span", { class: "chip-note" }, "link in Metricool") : null)];
    }));
    const approval = segmented({ label: "Posting", value: settings.approval,
      options: [{ value: "auto", label: "Post on its own" }, { value: "review", label: "Ask me first" }],
      onChange: (v) => set({ approval: v }) });
    const from = h("input", { class: "input", type: "time", value: settings.window[0], "aria-label": "Earliest posting time" });
    const to = h("input", { class: "input", type: "time", value: settings.window[1], "aria-label": "Latest posting time" });
    const onWindow = () => from.value && to.value && from.value < to.value && set({ window: [from.value, to.value] });
    from.addEventListener("change", onWindow);
    to.addEventListener("change", onWindow);
    const gap = h("select", { class: "input select", "aria-label": "Hours between reels" },
      [2, 3, 4, 5].map((n) => h("option", { value: n, selected: n === Number(settings.min_gap) }, `${n} h apart`)));
    gap.addEventListener("change", () => set({ min_gap: Number(gap.value) }));
    const reels = projects.filter((p) => (p.project && p.project.script || "").trim());
    const template = h("select", { class: "input select", "aria-label": "Make reels like" },
      h("option", { value: "" }, "My latest reel"),
      reels.map((p) => h("option", { value: p.id, selected: p.id === settings.template }, p.name || p.id)));
    template.addEventListener("change", () => set({ template: template.value || null }));
    const format = h("select", { class: "input select", "aria-label": "Format" },
      [["template", "Same as that reel"], ["9:16", "9:16, full screen"], ["4:5", "4:5 with black bars"]]
        .map(([v, l]) => h("option", { value: v, selected: v === settings.format }, l)));
    format.addEventListener("change", () => set({ format: format.value }));
    const music = h("select", { class: "input select", "aria-label": "Music" },
      [["random", "A random song from my music library"], ["template", "That reel's song"], ["none", "No music"]]
        .map(([v, l]) => h("option", { value: v, selected: v === settings.music }, l)));
    music.addEventListener("change", () => set({ music: music.value }, true));
    const musicHelp = settings.music !== "random" ? null
      : h("p", { class: "field-help" }, library.length
        ? `${library.length} song${library.length === 1 ? "" : "s"} in the shuffle, dealt so none repeats before the others have played. `
        : "Your music library is empty, so reels use that reel's song for now. ",
        h("a", { href: "#/x/music" }, library.length ? "Manage songs" : "Add songs"));
    const topic = h("textarea", { class: "input textarea", rows: 2, placeholder: "Optional, e.g. letting go, self-worth, loneliness at night" }, settings.topic || "");
    topic.addEventListener("change", () => set({ topic: topic.value.trim() }));
    const tone = h("select", { class: "input select", "aria-label": "Tone" },
      TONES.map(([v, l]) => h("option", { value: v, selected: v === settings.tone }, l)));
    tone.addEventListener("change", () => set({ tone: tone.value }));
    const ai = switchControl({ id: "ap-ai", checked: settings.ai_label, label: "Label posts as AI-generated",
      onChange: (v) => set({ ai_label: v }) });

    form.replaceChildren(
      h("div", { class: "card-head" }, h("h2", { class: "card-title" }, "How it works for you"),
        h("p", { class: "card-sub" }, "Saved as you go. Each run uses these.")),
      h("div", { class: "form-grid ap-form" },
        field("Reels a day", perDay, `${settings.per_day * 7} a week.`),
        field("Fill ahead", days, "How many days one run fills."),
        field("Platforms", nets, tiktokLinked ? null : "TikTok joins once it's linked in Metricool: TikTok's own form can't be filled without you."),
        field("Posting", approval, settings.approval === "review" ? "Each reel waits as a Metricool draft until you approve it below or in Calendar." : "Reels go out at their times without you."),
        field("Posting hours", h("div", { class: "row ap-window" }, from, h("span", { class: "muted" }, "to"), to, gap), "Within these hours, the busiest ones for your audience."),
        field("Make reels like", template, "They copy its look, captions, voice and searches; the script, clips and post are new."),
        field("Format", format),
        field("Music", h("div", { class: "stack" }, music, musicHelp)),
        field("Topics", topic, "Leave empty and Gemini picks the angles."),
        field("Tone", tone),
        h("div", { class: "field field-bool" }, ai)));
  }

  // ---- queue
  async function act(it, draft, label) {
    try {
      const b = await metricoolBrand();
      let post = it.metricool && it.metricool.uuid ? live.get(String(it.metricool.uuid)) : null;
      if (!post) {
        await loadLive(true);
        post = it.metricool && it.metricool.uuid ? live.get(String(it.metricool.uuid)) : null;
      }
      if (!post) throw new Error("Metricool no longer has this post.");
      if (!draft && slotDate(it) < new Date(Date.now() + 2 * 60000)) throw new Error("Its time has passed. Pick a new time in Calendar.");
      await metricool("updateScheduledPost", { id: String(post.id), uuid: String(post.uuid), blogId: b.id,
                                               info: JSON.stringify({ ...post, draft }) }, { text: true });
      await retrying(() => db.doc(`autopilot/${it.id}`).update({ status: draft ? "skipped" : "scheduled", updated: nowIso() }));
      toast(label, { kind: "success", timeout: 2500 });
      await loadQueue();
    } catch (err) {
      toastError("Metricool didn't take the change", { message: err && err.code ? metricoolProblem(err) : (err && err.message) || String(err) });
    }
  }

  function row(it) {
    const st = liveStatus(it, it.metricool && live.get(String(it.metricool.uuid)));
    const when = slotDate(it);
    const actions = [];
    if (it.master_url) actions.push(h("a", { class: "btn btn-ghost btn-sm", href: it.master_url, target: "_blank", rel: "noopener", html: `${icon("play", { size: 14 })}<span class="btn-label">Watch</span>` }));
    if (st === "review") actions.push(button("Approve", { icon: "check", size: "sm", variant: "primary", onClick: () => act(it, false, "Approved: it posts at its time.") }));
    if (st === "review" || st === "scheduled") actions.push(button("Don't post", { icon: "pause", size: "sm", variant: "ghost", onClick: () => act(it, true, "Held back: it stays a draft in Metricool.") }));
    if (st === "skipped" && when > new Date()) actions.push(button("Post it after all", { size: "sm", variant: "ghost", onClick: () => act(it, false, "Back on: it posts at its time.") }));
    if (it.metricool && it.metricool.plannerUrl) actions.push(h("a", { class: "btn btn-ghost btn-sm", href: it.metricool.plannerUrl, target: "_blank", rel: "noopener" }, "Metricool"));
    return h("li", { class: ["ap-item", `is-${st}`] },
      it.cover ? h("img", { class: "mini-thumb", src: blobUrl(it.cover), alt: "" }) : h("span", { class: "mini-thumb is-empty", html: icon("film", { size: 18 }) }),
      h("div", { class: "ap-main" },
        h("div", { class: "ap-top" }, h("strong", { class: "timecode" }, timeLabel(when)), pill(st),
          h("span", { class: "ap-nets" }, (it.platforms || []).map(dot))),
        h("p", { class: "ap-title" }, it.title || "Untitled"),
        it.caption ? h("p", { class: "muted small ap-caption" }, String(it.caption).split("\n")[0]) : null,
        (it.problems || []).length ? h("ul", { class: "ap-problems" }, it.problems.map((p) => h("li", {}, p))) : null,
        actions.length ? h("div", { class: "row row-wrap" }, actions) : null));
  }

  function paintQueue() {
    const now = new Date();
    const upcoming = items.filter((it) => slotDate(it) >= new Date(now - 3 * 3600e3));
    const past = items.filter((it) => slotDate(it) < new Date(now - 3 * 3600e3)).slice(-12).reverse();
    const byDay = new Map();
    for (const it of upcoming) {
      const k = slotDate(it).toDateString();
      if (!byDay.has(k)) byDay.set(k, []);
      byDay.get(k).push(it);
    }
    queue.replaceChildren(
      h("section", { class: "stack" },
        h("h2", { class: "section-title" }, "Coming up"),
        upcoming.length ? h("div", { class: "stack-lg" }, [...byDay.values()].map((list) => h("div", { class: "stack" },
          h("p", { class: "subsection-title" }, dayLabel(slotDate(list[0]))),
          h("ul", { class: "ap-list" }, list.map(row)))))
          : h("p", { class: "muted empty-inline" }, "Nothing lined up. Press “Fill the next days” and Claude makes the reels.")),
      ...(past.length ? [h("details", { class: "ap-past", open: pastOpen, ontoggle: (e) => { pastOpen = e.target.open; } },
        h("summary", {}, `Earlier (${past.length})`), h("ul", { class: "ap-list" }, past.map(row)))] : []));
  }

  async function loadLive(force = false) {
    const scheduled = items.filter((it) => it.metricool && it.metricool.uuid);
    if (!scheduled.length || (live.size && !force)) return;
    try {
      const b = await metricoolBrand();
      const first = slotDate(scheduled[0]);
      const last = slotDate(scheduled[scheduled.length - 1]);
      const p = await metricool("getScheduledPosts", { brandId: b.id, timezone: b.timezone,
        fromDate: isoIn(new Date(first.getTime() - 864e5), b.timezone), toDate: isoIn(new Date(last.getTime() + 864e5), b.timezone) });
      live = new Map(((p && p.data) || []).map((x) => [String(x.uuid), x]));
      note.replaceChildren();
    } catch (err) {
      note.replaceChildren(callout("info", `Metricool couldn't be read (${metricoolProblem(err)}), so posting states may be out of date.`));
    }
  }

  async function loadQueue() {
    try {
      const snap = await retrying(() => db.collection("autopilot").get());
      items = snap.docs.map((d) => ({ id: d.id, ...d.data() })).sort((a, b) => String(a.slot).localeCompare(String(b.slot)));
    } catch (err) {
      toastError("Could not load the queue", err);
    }
    await loadLive(true);
    paintStatus();
    paintQueue();
  }

  (async () => {
    try {
      const [s, ps, songs] = await Promise.all([retrying(() => db.doc("settings/autopilot").get()),
        retrying(() => db.collection("projects").get()), listSongs()]);
      if (s.exists) settings = { ...DEFAULTS, ...s.data() };
      if (settings.music === "rotate") settings.music = "random";
      projects = ps.docs.map((d) => ({ id: d.id, ...d.data() }));
      library = songs.filter((song) => song.enabled !== false);
    } catch (err) {
      toastError("Could not load the autopilot", err);
    }
    paintStatus();
    paintForm();
    paintQueue();
    metricoolBrand().then((b) => { brand = b; paintForm(); }).catch(() => {});
    if (P().mcp) P().mcp.callTool("higgsfield", "balance", {}).then((r) => {
      credits = Number((r && r.payload && r.payload.credits) || 0) || null;
      paintStatus();
    }).catch(() => {});
    if (settings.last_task) watchTask(settings.last_task);
    loadQueue();
  })();
  return () => unsubTask && unsubTask();
}
