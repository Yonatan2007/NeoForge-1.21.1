/**
 * reelforge web UI: routing, the app shell (top bar + step navigation), the
 * project gallery and the tab modules for one project.
 *
 * Routes (hash based, so the server only serves "/"):
 *   #/                     project gallery
 *   #/p/<id>/<tab>         a project, tab = script | voice | music | footage | style | length | render
 */

import { api, isUnsupported } from "./api.js";
import {
  button, clear, confirmDialog, dialog, formatSeconds, h, menuButton, relativeTime, toast, toastError, uid,
} from "./dom.js";
import { icon, LOGO } from "./icons.js";
import { isActive, jobs, resumeJob } from "./jobs.js";
import { parseScript } from "./script-model.js";
import * as store from "./store.js";
import { on, state, supports } from "./store.js";
import * as footageTab from "./tabs/footage.js";
import * as lengthTab from "./tabs/length.js";
import * as musicTab from "./tabs/music.js";
import * as renderTab from "./tabs/render.js";
import * as scriptTab from "./tabs/script.js";
import * as styleTab from "./tabs/style.js";
import * as voiceTab from "./tabs/voice.js";

const TABS = [
  { id: "script", label: "Script", icon: "script", module: scriptTab,
    title: "Script", lead: "Write what the voice will say. The first sentence becomes the opening line on the skyline." },
  { id: "voice", label: "Voice", icon: "voice", module: voiceTab,
    title: "Voice", lead: "Choose who reads the script." },
  { id: "music", label: "Music", icon: "music", module: musicTab,
    title: "Music", lead: "Optional. Pick the part of the song you like and where it plays in the video." },
  { id: "footage", label: "Footage", icon: "footage", module: footageTab,
    title: "Footage", lead: "Your own photos and clips go first, in this order. Stock footage fills the rest." },
  { id: "style", label: "Style", icon: "style", module: styleTab,
    title: "Style", lead: "How the captions, the opening line and the colour grade look." },
  { id: "length", label: "Length", icon: "length", module: lengthTab,
    title: "Length", lead: "How long the finished reel should be." },
  { id: "render", label: "Render", icon: "render", module: renderTab,
    title: "Render", lead: "Check the plan, then make the video." },
];

const root = document.getElementById("app");
let cleanupView = null;
let cleanupShell = [];

// --------------------------------------------------------------------------- routing

function parseRoute() {
  const m = location.hash.match(/^#\/p\/([^/]+)(?:\/([a-z]+))?/);
  if (!m) return { home: true };
  const tab = TABS.some((t) => t.id === m[2]) ? m[2] : "script";
  return { projectId: decodeURIComponent(m[1]), tab };
}

export const projectHref = (id, tab = "script") => `#/p/${encodeURIComponent(id)}/${tab}`;

async function route() {
  const r = parseRoute();
  if (cleanupView) cleanupView();
  cleanupView = null;
  if (r.home) {
    if (state.projectId) {
      await store.flush().catch(() => {});
      store.closeProject();
    }
    renderHome();
    return;
  }
  if (r.projectId !== state.projectId) {
    renderLoading();
    try {
      await store.openProject(r.projectId);
    } catch (err) {
      toastError(err.status === 404 ? "" : "Could not open the project", err.status === 404 ? new Error("That project no longer exists.") : err);
      location.hash = "#/";
      return;
    }
    resumeJob(state.projectId);
  }
  renderProject(r.tab);
}

// --------------------------------------------------------------------------- shell pieces

function brand() {
  return h("a", { class: "brand", href: "#/", "aria-label": "reelforge – all reels", html: `${LOGO}<span class="brand-name">reelforge</span>` });
}

function renderLoading() {
  clear(root).append(
    h("header", { class: "topbar" }, h("div", { class: "topbar-left" }, brand())),
    h("main", { id: "main", class: "loading-screen", "aria-busy": "true" }, h("div", { class: "spinner", role: "status", "aria-label": "Loading" })));
}

function saveIndicator() {
  const el = h("div", { class: "save-state", role: "status", "aria-live": "polite" });
  const paint = () => {
    const { status } = state.save;
    el.dataset.status = status;
    clear(el);
    if (status === "error") {
      el.append(h("span", { html: icon("alert", { size: 16 }) }), h("span", {}, "Not saved"),
        h("button", { type: "button", class: "link-btn", onclick: store.retrySave }, "Retry"));
    } else if (status === "saving") {
      el.append(h("span", { class: "spinner spinner-sm", "aria-hidden": "true" }), h("span", {}, "Saving…"));
    } else if (status === "dirty") {
      el.append(h("span", { class: "dot", "aria-hidden": "true" }), h("span", {}, "Editing"));
    } else {
      el.append(h("span", { html: icon("check", { size: 16 }) }), h("span", {}, "Saved"));
    }
  };
  paint();
  cleanupShell.push(on("save", paint));
  return el;
}

function nameInput() {
  const input = h("input", {
    class: "project-name", type: "text", value: state.project.name || "", "aria-label": "Reel name",
    placeholder: "Untitled reel", maxlength: 120, spellcheck: false,
  });
  const fit = () => {
    input.style.width = `${Math.min(28, Math.max(8, (input.value || input.placeholder).length + 2))}ch`;
  };
  input.addEventListener("input", () => {
    fit();
    store.setValue("name", input.value.trim() || "untitled");
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") input.blur();
  });
  // the server names an untitled reel after an uploaded script file
  cleanupShell.push(on("change", ({ paths } = {}) => {
    if (!(paths || []).includes("name") || document.activeElement === input) return;
    input.value = state.project.name || "";
    fit();
  }));
  fit();
  return input;
}

function projectMenu() {
  return menuButton({
    label: "Reel actions",
    items: () => [
      supports("duplicate") && { label: "Duplicate reel", icon: "copy", onSelect: () => duplicateProject(state.projectId) },
      { label: "Delete reel", icon: "trash", danger: true, onSelect: () => deleteProject(state.projectId, state.project.name) },
    ],
  });
}

// --------------------------------------------------------------------------- step navigation

/** One-line status under each step name, plus a state for its marker. */
function stepStatus(tab) {
  const p = state.project;
  const parsed = parseScript(p.script);
  switch (tab) {
    case "script":
      return parsed.words.length ? { text: `${parsed.words.length} words`, ok: true } : { text: "Start here", warn: true };
    case "voice": {
      const v = p.voice;
      if (v.source === "none") return { text: "No voice", ok: true };
      if (v.source === "piper") return { text: "Piper", ok: Boolean(v.piper_model), warn: !v.piper_model };
      if (v.source === "higgsfield-api") return { text: "Higgsfield API", ok: true };
      if (v.file) return { text: v.source === "higgsfield" ? "Higgsfield" : "Recording added", ok: true };
      if (v.source === "higgsfield" && state.meta.capabilities && state.meta.capabilities.claude_voice) {
        return { text: "Claude makes it", ok: true };
      }
      return { text: v.source === "higgsfield" ? "Waiting for voice" : "Add a recording", warn: true };
    }
    case "music":
      return p.music.file ? { text: "Track added", ok: true } : { text: "Optional" };
    case "footage": {
      const own = p.footage.items.filter((i) => i.role === "footage").length;
      if (own) return { text: `${own} of yours${p.footage.stock ? " + stock" : ""}`, ok: true };
      return p.footage.stock ? { text: "Stock footage", ok: true } : { text: "Add footage", warn: true };
    }
    case "style": {
      const preset = (state.meta.presets || []).find((x) => (x.id ?? x) === p.preset);
      const label = preset ? (preset.label || preset.id || preset).split(" (")[0] : p.preset;
      return { text: label || "Custom", ok: true };
    }
    case "length":
      return p.duration.target ? { text: formatSeconds(p.duration.target), ok: true } : { text: "Automatic", ok: true };
    case "render": {
      const job = jobs[state.projectId];
      if (isActive(job)) return { text: `Rendering ${Math.round((job.progress || 0) * 100)} %`, busy: true };
      if (job && job.status === "error") return { text: "Render failed", warn: true };
      return state.outputs.video ? { text: "Video ready", ok: true } : { text: "Not rendered yet" };
    }
    default:
      return { text: "" };
  }
}

function stepNav(active) {
  const list = h("ol", { class: "steps-list" });
  const items = TABS.map((t) => {
    const sub = h("span", { class: "step-sub" });
    const marker = h("span", { class: "step-marker", "aria-hidden": "true" });
    const link = h("a", { class: "step", href: projectHref(state.projectId, t.id), "aria-current": t.id === active ? "page" : null },
      marker,
      h("span", { class: "step-icon", html: icon(t.icon, { size: 18 }) }),
      h("span", { class: "step-text" }, h("span", { class: "step-label" }, t.label), sub));
    list.append(h("li", {}, link));
    return { t, sub, marker, link };
  });
  const paint = () => {
    if (!state.project) return; // the project was just closed (leaving for the gallery)
    for (const { t, sub, marker, link } of items) {
      const st = stepStatus(t.id);
      sub.textContent = st.text;
      link.dataset.state = st.busy ? "busy" : st.warn ? "warn" : st.ok ? "ok" : "idle";
      marker.innerHTML = st.ok && !st.busy ? icon("check", { size: 12 }) : "";
    }
  };
  paint();
  cleanupShell.push(on("change", paint), on("uploads", paint), on("outputs", paint), on("job", paint), on("project", paint));
  const nav = h("nav", { class: "steps", "aria-label": "Reel steps" }, list);
  requestAnimationFrame(() => {
    const cur = nav.querySelector("[aria-current=page]");
    if (cur && nav.scrollWidth > nav.clientWidth) cur.scrollIntoView({ block: "nearest", inline: "center" });
  });
  return nav;
}

// --------------------------------------------------------------------------- project view

function renderProject(tabId) {
  cleanupShell.forEach((fn) => fn());
  cleanupShell = [];
  const tab = TABS.find((t) => t.id === tabId);
  const index = TABS.indexOf(tab);
  document.title = `${state.project.name || "Untitled"} · ${tab.label} · reelforge`;

  const renderBtn = tabId === "render" ? null : h("a", { class: "btn btn-primary btn-sm topbar-render", href: projectHref(state.projectId, "render"), html: `${icon("render", { size: 16 })}<span class="btn-label">Render</span>` });
  const topbar = h("header", { class: "topbar" },
    h("div", { class: "topbar-left" },
      h("a", { class: "btn btn-ghost btn-icon back-btn", href: "#/", "aria-label": "All reels", title: "All reels", html: icon("arrowLeft") }),
      brand(),
      h("span", { class: "crumb-sep", "aria-hidden": "true" }, "/"),
      nameInput()),
    h("div", { class: "topbar-right" }, saveIndicator(), projectMenu(), renderBtn));

  const panel = h("div", { class: "panel" });
  const next = TABS[index + 1];
  const prev = TABS[index - 1];
  const pager = h("nav", { class: "pager", "aria-label": "Step navigation" },
    prev ? h("a", { class: "btn btn-ghost", href: projectHref(state.projectId, prev.id), html: `${icon("chevronLeft")}<span class="btn-label">${prev.label}</span>` }) : h("span"),
    next ? h("a", { class: "btn btn-secondary", href: projectHref(state.projectId, next.id), html: `<span class="btn-label">Next: ${next.label}</span>${icon("chevronRight")}` }) : null);

  const main = h("main", { id: "main", class: "main", tabindex: "-1" },
    h("div", { class: "main-inner" },
      h("header", { class: "page-head" },
        h("p", { class: "eyebrow" }, `Step ${index + 1} of ${TABS.length}`),
        h("h1", { class: "page-title" }, tab.title),
        h("p", { class: "page-lead" }, tab.lead)),
      panel,
      pager));

  clear(root).append(topbar, h("div", { class: "shell" }, stepNav(tabId), main));
  const cleanup = tab.module.render(panel);
  cleanupView = () => {
    if (typeof cleanup === "function") cleanup();
  };
  window.scrollTo(0, 0);
}

// --------------------------------------------------------------------------- gallery

function projectCard(p) {
  const thumb = p.thumb
    ? h("img", { src: p.thumb, alt: "", loading: "lazy" })
    : h("div", { class: "thumb-empty", html: icon("mountain", { size: 28 }) });
  const card = h("li", { class: "project-card" },
    h("a", { class: "project-link", href: projectHref(p.id) },
      h("div", { class: "project-thumb" }, thumb, p.has_video ? h("span", { class: "badge badge-accent", html: `${icon("check", { size: 12 })}Video ready` }) : null),
      h("div", { class: "project-meta" },
        h("h2", { class: "project-title" }, p.name || "Untitled reel"),
        h("p", { class: "project-sub" }, p.updated ? `Edited ${relativeTime(p.updated)}` : ""))),
    h("div", { class: "project-actions" }, menuButton({
      label: `Actions for ${p.name || "Untitled reel"}`,
      items: () => [
        { label: "Open", icon: "folder", onSelect: () => (location.hash = projectHref(p.id)) },
        supports("duplicate") && { label: "Duplicate", icon: "copy", onSelect: () => duplicateProject(p.id) },
        { label: "Delete", icon: "trash", danger: true, onSelect: () => deleteProject(p.id, p.name) },
      ],
    })));
  return card;
}

async function renderHome() {
  cleanupShell.forEach((fn) => fn());
  cleanupShell = [];
  document.title = "reelforge";
  const grid = h("ul", { class: "project-grid", "aria-busy": "true" });
  const newBtn = button("New reel", { icon: "plus", variant: "primary", onClick: newProject });
  const main = h("main", { id: "main", class: "main home", tabindex: "-1" },
    h("div", { class: "main-inner" },
      h("header", { class: "home-head" },
        h("div", {},
          h("h1", { class: "page-title" }, "Your reels"),
          h("p", { class: "page-lead" }, "Script in, ready-to-post vertical video out.")),
        newBtn),
      grid));
  clear(root).append(h("header", { class: "topbar" }, h("div", { class: "topbar-left" }, brand())), main);

  let projects = [];
  try {
    projects = await api.listProjects();
  } catch (err) {
    toastError("Could not load your reels", err);
  }
  grid.removeAttribute("aria-busy");
  if (!projects.length) {
    grid.replaceWith(h("section", { class: "empty-state" },
      h("div", { class: "empty-art", html: icon("film", { size: 36 }) }),
      h("h2", {}, "Make your first reel"),
      h("p", {}, "Paste a short script, add a voice and some footage (or let reelforge find stock clips), and render a vertical video ready for Reels, TikTok and Shorts."),
      button("New reel", { icon: "plus", variant: "primary", onClick: newProject })));
    return;
  }
  grid.append(h("li", { class: "project-card project-new" },
    h("button", { type: "button", class: "project-new-btn", onclick: newProject },
      h("span", { class: "project-new-icon", html: icon("plus", { size: 24 }) }), h("span", {}, "New reel"))));
  for (const p of projects) grid.append(projectCard(p));
}

// --------------------------------------------------------------------------- project actions

async function newProject() {
  const presets = state.meta.presets || [];
  const nameId = uid("new-name");
  const name = h("input", { id: nameId, class: "input", type: "text", placeholder: "e.g. Last conversation", autocomplete: "off", maxlength: 120 });
  const choices = [{ value: "", label: "My defaults", help: "Your saved style and settings" }]
    .concat(presets.map((p) => ({ value: p.id ?? p, label: (p.label || p.id || p).split(" (")[0], help: (p.label || "").match(/\((.*)\)/)?.[1] || "" })));
  const group = uid("preset");
  const list = h("div", { class: "choice-list", role: "radiogroup", "aria-label": "Start from" },
    choices.map((c, i) => {
      const id = `${group}-${i}`;
      return [h("input", { type: "radio", name: group, id, value: c.value, class: "visually-hidden", checked: i === 0 }),
        h("label", { class: "choice", for: id }, h("span", { class: "choice-title" }, c.label), c.help ? h("span", { class: "choice-help" }, c.help) : null)];
    }));
  const body = h("div", { class: "stack" },
    h("div", { class: "field" }, h("label", { class: "field-label", for: nameId }, "Name"), name),
    presets.length ? h("div", { class: "field" }, h("span", { class: "field-label" }, "Start from"), list) : null);
  const result = await dialog({
    title: "New reel",
    body,
    initialFocus: `#${nameId}`,
    actions: [
      { label: "Cancel", value: null, variant: "ghost" },
      { label: "Create reel", variant: "primary", submit: true, value: (form) => ({
        name: name.value.trim() || undefined,
        preset: (form.querySelector(`input[name="${group}"]:checked`) || {}).value || undefined,
      }) },
    ],
  });
  if (!result) return;
  try {
    const res = await api.createProject(result);
    const id = res.id || (res.project && res.project.id);
    if (res.project && id) store.adoptProjectResponse(id, res);
    location.hash = projectHref(id);
  } catch (err) {
    toastError("Could not create the reel", err);
  }
}

async function duplicateProject(id) {
  try {
    await store.flush().catch(() => {});
    const res = await api.duplicateProject(id);
    const newId = res.id || (res.project && res.project.id);
    toast("Reel duplicated.", { kind: "success" });
    if (newId) location.hash = projectHref(newId);
    else route();
  } catch (err) {
    if (isUnsupported(err)) {
      store.markUnsupported("duplicate");
      toast("This server cannot duplicate reels.", { kind: "error" });
    } else toastError("Could not duplicate", err);
  }
}

async function deleteProject(id, name) {
  const ok = await confirmDialog({
    title: "Delete this reel?",
    message: `“${name || "Untitled reel"}” and its uploaded files and videos will be removed. This cannot be undone.`,
    confirmLabel: "Delete reel",
    danger: true,
  });
  if (!ok) return;
  try {
    await api.deleteProject(id);
    toast("Reel deleted.");
    if (state.projectId === id) store.closeProject();
    if (location.hash === "#/" || !location.hash) route();
    else location.hash = "#/";
  } catch (err) {
    toastError("Could not delete", err);
  }
}

// --------------------------------------------------------------------------- boot

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    if (state.projectId) store.flush().then(() => toast("Saved.", { kind: "success", timeout: 1500 }), (err) => toastError("Could not save", err));
  }
});

on("save", ({ status, error }) => {
  if (status === "error" && error) toastError("Could not save your changes", error);
});

window.addEventListener("hashchange", route);

// The skip link must not touch the hash (that is the router's).
document.querySelector(".skip-link")?.addEventListener("click", (e) => {
  e.preventDefault();
  const main = document.getElementById("main");
  if (main) main.focus();
});

(async function boot() {
  renderLoading();
  await store.loadReference();
  route();
})();
