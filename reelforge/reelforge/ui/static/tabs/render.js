/**
 * Render tab: readiness summary, the plan (shots, captions, searches,
 * warnings), the render job with live progress and log, and the finished
 * video with downloads.
 */

import { api, isUnsupported } from "../api.js";
import { callout } from "../components.js";
import { basename, button, clear, formatSeconds, formatTime, h, toastError } from "../dom.js";
import { switchControl } from "../fields.js";
import { icon } from "../icons.js";
import { cancelRender, clearJob, isActive, jobs, startRender } from "../jobs.js";
import { parseScript } from "../script-model.js";
import { flush, markUnsupported, on, setValue, state, supports, uploadFor } from "../store.js";

const STAGES = [
  ["voice", "Voice"], ["timing", "Timing"], ["footage", "Footage"],
  ["prepare", "Colour"], ["render", "Rendering"], ["finish", "Finishing"],
];
const DOWNLOADS = [
  ["video", "Video", "MP4", "film"],
  ["srt", "Subtitles", "SRT", "script"],
  ["cover", "Cover image", "JPG", "image"],
  ["credits", "Stock credits", "TXT", "info"],
];

const ASPECT_RATIOS = { "9:16": "9 / 16", "4:5": "4 / 5", "1:1": "1 / 1", "16:9": "16 / 9" };

function readiness() {
  const p = state.project;
  const words = parseScript(p.script).words.length;
  const v = p.voice;
  const claudeVoice = Boolean(state.meta.capabilities && state.meta.capabilities.claude_voice);
  const needsFile = (v.source === "file" || (v.source === "higgsfield" && !claudeVoice)) && !v.file;
  const own = p.footage.items.filter((i) => i.role === "footage").length;
  return [
    { ok: words > 0, label: words ? `Script · ${words} words` : "Script is empty", tab: "script" },
    { ok: !needsFile && !(v.source === "piper" && !v.piper_model),
      label: v.source === "none" ? "No voice (captions at reading pace)" : needsFile ? "Voiceover missing"
        : v.source === "higgsfield" && !v.file ? "Claude makes the voice" : "Voice ready", tab: "voice" },
    { ok: own > 0 || p.footage.stock, label: own ? `${own} of your clips${p.footage.stock ? " + stock" : ""}` : p.footage.stock ? "Stock footage" : "No footage", tab: "footage" },
    { ok: true, optional: !p.music.file, label: p.music.file ? "Music added" : "No music (optional)", tab: "music" },
  ];
}

function outputUrl(key, result) {
  if (state.outputs && state.outputs[key]) return state.outputs[key];
  const rel = result && result[key];
  return rel ? api.fileUrl(state.projectId, rel) : null;
}

/** Finished video: player, duration and downloads. */
function resultCard(job) {
  const result = job && job.status === "done" ? job.result : null;
  const video = outputUrl("video", result);
  if (!video) return null;
  const cover = outputUrl("cover", result);
  const stamp = job && job.status === "done" ? `${video.includes("?") ? "&" : "?"}r=${job.job_id}` : "";
  const player = h("video", { class: "result-video", controls: true, playsinline: true, preload: "metadata", src: video + stamp, poster: cover || null });
  const frame = h("div", { class: "result-frame", style: { aspectRatio: ASPECT_RATIOS[state.project.style.video.aspect] || "9 / 16" } }, player);
  player.addEventListener("loadedmetadata", () => {
    if (player.videoWidth) frame.style.aspectRatio = `${player.videoWidth} / ${player.videoHeight}`;
  });
  const links = h("ul", { class: "downloads" });
  for (const [key, label, ext, iconName] of DOWNLOADS) {
    const url = outputUrl(key, result);
    if (!url) continue;
    const base = (state.project.name || "reel").replace(/[^\w-]+/g, "-");
    const fromUrl = basename(url.split("?")[0]);
    const suffix = { srt: "captions", cover: "cover", credits: "credits" }[key] || key;
    const name = key === "video" ? `${base}.mp4` : /\.[a-z0-9]+$/i.test(fromUrl) ? fromUrl : `${base}-${suffix}.${ext.toLowerCase()}`;
    links.append(h("li", {}, h("a", { class: ["download", key === "video" && "download-main"], href: url, download: name },
      h("span", { class: "download-icon", html: icon(iconName, { size: 18 }) }),
      h("span", { class: "download-text" }, h("span", { class: "download-label" }, label), h("span", { class: "download-ext" }, ext)),
      h("span", { class: "download-arrow", html: icon("download", { size: 18 }) }))));
  }
  // Inside the Claude app plain download links do nothing: save through its download prompt.
  links.addEventListener("click", (e) => {
    const a = e.target.closest("a.download");
    if (!a || typeof globalThis.reelforgeSaveFile !== "function") return;
    e.preventDefault();
    globalThis.reelforgeSaveFile(a.href, a.getAttribute("download"));
  });
  const warnings = (result && result.warnings) || [];
  // the control panel has a Publish page: go straight to this reel's post
  const ex = globalThis.reelforgeExtras;
  const post = ex && (ex.nav || []).some((n) => n.id === "publish")
    ? h("a", { class: "btn btn-secondary", href: `#/x/publish/${encodeURIComponent(`reel:${state.projectId}`)}`,
               html: `${icon("upload", { size: 18 })}<span class="btn-label">Post it to TikTok, Instagram and YouTube</span>` })
    : null;
  return h("section", { class: "card result-card", "aria-labelledby": "result-title" },
    h("div", { class: "result-grid" },
      frame,
      h("div", { class: "stack" },
        h("div", { class: "card-head" },
          h("h2", { class: "card-title", id: "result-title" }, "Your video"),
          h("p", { class: "card-sub" }, result && result.duration ? `${formatSeconds(result.duration)} · ready to post` : "Latest render")),
        links,
        post,
        warnings.length ? h("div", { class: "stack" }, warnings.map((w) => callout("warn", w))) : null)));
}

/** Live progress of a queued/running/failed job. */
function jobCard(job, logOpen) {
  if (!job || job.status === "done") return null;
  const activeIdx = STAGES.findIndex(([id]) => id === job.stage);
  const pct = Math.round((job.progress || 0) * 100);
  const running = isActive(job);
  const log = h("pre", { class: "log", tabindex: "0", "aria-label": "Render log" }, (job.log || []).join("\n") || "No output yet.");
  const details = h("details", { class: "log-wrap", open: logOpen ?? job.status === "error" }, h("summary", {}, "Show log"), log);
  const card = h("section", { class: ["card", "job-card", `job-${job.status}`], "aria-labelledby": "job-title" },
    h("div", { class: "card-head card-head-row" },
      h("div", {},
        h("h2", { class: "card-title", id: "job-title" },
          job.status === "queued" ? "Waiting to start…" : running ? "Rendering…" : job.status === "cancelled" ? "Render cancelled" : "Render failed"),
        h("p", { class: "card-sub", role: "status", "aria-live": "polite" }, running ? `${job.message || ""}` : "")),
      running
        ? button("Cancel", { icon: "x", variant: "ghost", size: "sm", onClick: () => cancelRender(state.projectId).catch((err) => toastError("Could not cancel", err)) })
        : button("Dismiss", { variant: "ghost", size: "sm", onClick: () => clearJob(state.projectId) })),
    running ? h("div", { class: "stack" },
      h("div", { class: "progress-row" },
        h("div", { class: "progress", role: "progressbar", "aria-label": "Render progress", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": pct },
          h("div", { class: "progress-fill", style: { width: `${pct}%` } })),
        h("span", { class: "progress-pct" }, `${pct} %`)),
      h("ol", { class: "stages", "aria-label": "Render stages" }, STAGES.map(([id, label], i) =>
        h("li", { class: ["stage", i < activeIdx && "is-done", i === activeIdx && "is-active"], "aria-current": i === activeIdx ? "step" : null },
          h("span", { class: "stage-dot", html: i < activeIdx ? icon("check", { size: 12 }) : "" }), label))),
      h("p", { class: "muted small elapsed" }, `Elapsed ${formatTime((Date.now() - (job.startedAt || Date.now())) / 1000, false)}`)) : null,
    job.status === "error" ? callout("error", job.error || job.message || "Something went wrong.") : null,
    details);
  requestAnimationFrame(() => {
    log.scrollTop = log.scrollHeight;
  });
  return card;
}

function sourceCell(source) {
  if (!source) return h("span", { class: "muted" }, "–");
  const [kind, ...rest] = String(source).split(":");
  const value = rest.join(":");
  if (kind === "user") {
    const up = uploadFor(value);
    return h("span", { class: "src src-user" },
      up && up.thumb_url ? h("img", { src: up.thumb_url, alt: "" }) : h("span", { html: icon("image", { size: 14 }) }),
      h("span", { class: "src-text" }, basename(value)));
  }
  return h("span", { class: "src src-stock" }, h("span", { html: icon("search", { size: 14 }) }), h("span", { class: "src-text" }, value || "stock"));
}

function planCard(plan) {
  if (!plan) return null;
  const shots = plan.shots || [];
  const total = plan.estimated_seconds || (shots.length ? shots[shots.length - 1].end : 0);
  const stat = (value, label) => h("div", { class: "stat" }, h("span", { class: "stat-value" }, value), h("span", { class: "stat-label" }, label));
  const strip = shots.length ? h("div", { class: "shot-strip", "aria-hidden": "true" }, shots.map((s, i) =>
    h("span", { class: ["shot-seg", String(s.source || "").startsWith("user:") ? "is-user" : "is-stock"], style: { flexGrow: String(Math.max(s.end - s.start, 0.1)) }, title: `Shot ${i + 1}: ${s.source || ""}` }, String(i + 1)))) : null;
  const table = shots.length ? h("div", { class: "table-wrap" }, h("table", { class: "shots" },
    h("caption", { class: "visually-hidden" }, "Planned shots"),
    h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "#"), h("th", { scope: "col" }, "Time"), h("th", { scope: "col" }, "Words"), h("th", { scope: "col" }, "Footage"))),
    h("tbody", {}, shots.map((s, i) => h("tr", {},
      h("td", { class: "num" }, String(i + 1)),
      h("td", { class: "time" }, `${formatTime(s.start)}–${formatTime(s.end)}`),
      h("td", { class: "words" }, s.text || h("span", { class: "muted" }, "(music)")),
      h("td", {}, sourceCell(s.source))))))) : null;
  const captions = plan.captions || [];
  return h("section", { class: "card plan-card", "aria-labelledby": "plan-title" },
    h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "plan-title" }, "Plan")),
    h("div", { class: "stats" },
      stat(formatSeconds(total), plan.target_seconds ? `video (target ${formatSeconds(plan.target_seconds)})` : "video"),
      stat(String(shots.length), shots.length === 1 ? "shot" : "shots"),
      stat(String(captions.length), "captions"),
      stat(String((plan.queries || []).length), "stock searches")),
    (plan.warnings || []).length ? h("div", { class: "stack" }, plan.warnings.map((w) => callout("warn", w))) : null,
    plan.hook_preview ? h("figure", { class: "hook-preview" }, h("img", { src: plan.hook_preview, alt: "Preview of the opening frame with the hook text" }), h("figcaption", { class: "muted small" }, "Opening frame")) : null,
    (plan.hook || []).length ? h("div", { class: "preview-section" }, h("div", { class: "preview-label" }, "Opening line"),
      h("p", { class: "hook-plain" }, plan.hook.join(" "))) : null,
    strip, table,
    captions.length ? h("details", { class: "advanced" },
      h("summary", {}, h("span", { html: icon("chevronRight", { size: 16 }) }), "Captions", h("span", { class: "count" }, String(captions.length))),
      h("ol", { class: "caption-chips" }, captions.map((c) => h("li", { class: "caption-chip" }, c)))) : null,
    (plan.queries || []).length ? h("div", { class: "preview-section" }, h("div", { class: "preview-label" }, "Stock searches"),
      h("div", { class: "chips" }, [...new Set(plan.queries)].map((q) => h("span", { class: "chip chip-static", html: `${icon("search", { size: 13 })}${q.replace(/</g, "&lt;")}` })))) : null);
}

export function render(panel) {
  const checks = h("ul", { class: "checklist" });
  const planBtn = button("Preview plan", { icon: "eye" });
  const renderBtn = button("Render video", { icon: "render", variant: "primary", size: "lg" });
  const draft = h("div", { class: "field field-bool" },
    switchControl({ id: "draft", checked: state.project.style.video.draft, label: "Quick draft", describedBy: "draft-help", onChange: (v) => setValue("style.video.draft", v) }),
    h("p", { class: "field-help", id: "draft-help" }, "Half resolution: much faster, for checking timing and footage."));
  const launch = h("section", { class: "card launch-card", "aria-labelledby": "launch-title" },
    h("div", { class: "launch-grid" },
      h("div", {}, h("h2", { class: "card-title", id: "launch-title" }, "Ready check"), checks),
      h("div", { class: "launch-actions" }, renderBtn, h("div", { class: "row" }, supports("plan") ? planBtn : null), draft)));
  const jobSlot = h("div", {});
  const resultSlot = h("div", {});
  const planSlot = h("div", {});
  if (!supports("render")) {
    renderBtn.disabled = true;
    launch.append(callout("info", "This server cannot render videos."));
  }
  panel.append(h("div", { class: "stack-lg" }, launch, jobSlot, resultSlot, planSlot));

  const paintChecks = () => {
    clear(checks);
    for (const c of readiness()) {
      checks.append(h("li", { class: ["check", c.ok ? (c.optional ? "is-optional" : "is-ok") : "is-missing"] },
        h("span", { class: "check-icon", html: icon(c.ok ? (c.optional ? "info" : "check") : "alert", { size: 14 }) }),
        h("a", { href: `#/p/${encodeURIComponent(state.projectId)}/${c.tab}` }, c.label)));
    }
  };

  let resultKey = null;
  const paintJob = () => {
    const job = jobs[state.projectId];
    const running = isActive(job);
    renderBtn.disabled = running || !supports("render");
    renderBtn.querySelector(".btn-label").textContent = running ? "Rendering…" : state.outputs.video ? "Render again" : "Render video";
    const open = jobSlot.querySelector("details.log-wrap");
    jobSlot.replaceChildren(jobCard(job, open ? open.open : undefined) || "");
    // Only rebuild the player when there is a new video (polls must not restart playback).
    const key = JSON.stringify([state.outputs.video || null, job && job.status === "done" ? job.job_id : null]);
    if (key !== resultKey) {
      resultKey = key;
      resultSlot.replaceChildren(resultCard(job) || "");
    }
  };

  const paintPlan = () => planSlot.replaceChildren(planCard(state.plan) || "");

  planBtn.addEventListener("click", async () => {
    planBtn.disabled = true;
    planBtn.classList.add("is-loading");
    try {
      await flush();
      state.plan = await api.plan(state.projectId);
      paintPlan();
      planSlot.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      if (isUnsupported(err)) {
        markUnsupported("plan");
        planBtn.remove();
      }
      toastError("Could not make the plan", err);
    } finally {
      planBtn.disabled = false;
      planBtn.classList.remove("is-loading");
    }
  });

  renderBtn.addEventListener("click", async () => {
    renderBtn.disabled = true;
    try {
      await startRender(state.projectId);
    } catch (err) {
      toastError("Could not start the render", err);
      paintJob();
    }
  });

  paintChecks();
  paintJob();
  paintPlan();
  // Keep the elapsed time ticking between polls.
  const ticker = setInterval(() => {
    const el = jobSlot.querySelector(".elapsed");
    const job = jobs[state.projectId];
    if (el && job && isActive(job)) el.textContent = `Elapsed ${formatTime((Date.now() - (job.startedAt || Date.now())) / 1000, false)}`;
  }, 1000);
  const offs = [
    on("job", ({ projectId }) => projectId === state.projectId && paintJob()),
    on("outputs", paintJob),
    on("change", paintChecks),
  ];
  return () => {
    clearInterval(ticker);
    offs.forEach((f) => f());
  };
}
