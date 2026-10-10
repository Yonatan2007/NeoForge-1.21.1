/**
 * Style tab: preset picker, forms generated from /api/schema for the
 * caption, hook, video and look sections (plus any section no other tab
 * shows), a live preview, and saving the style as the user's defaults.
 */

import { api, isUnsupported } from "../api.js";
import { callout } from "../components.js";
import { button, clear, clone, confirmDialog, h, menuButton, toast, toastError } from "../dom.js";
import { renderFields } from "../fields.js";
import { icon } from "../icons.js";
import { createPreview } from "../preview.js";
import {
  markUnsupported, on, projectBinding, replaceParts, schemaSections, setValue, state, supports,
} from "../store.js";

const STYLE_SECTIONS = ["caption", "hook", "video", "look"];
// Sections edited on their own tabs (or per file on the Footage tab), never on Style.
const OTHER_TABS = new Set(["music", "duration", "footage", "voice", "project", "footage_item"]);
const SECTION_INTRO = {
  caption: "The words that pop up while the voice speaks.",
  hook: "The first sentence, laid along the skyline of the first shot.",
  video: "Format, shot lengths and export quality.",
  look: "The colour grade applied to every clip.",
};
// Footage settings that belong to a look (presets change them); the rest is per reel.
const PER_REEL_FOOTAGE = new Set(["items", "queries"]);

function presetLabel(p) {
  const raw = p.label || p.id || String(p);
  const m = raw.match(/^(.*?)\s*\((.*)\)\s*$/);
  return m ? { title: m[1], help: m[2] } : { title: raw, help: "" };
}

/** Take the look-related parts of a full project dict (a preset or the user's defaults). */
function lookParts(template) {
  const parts = { style: template.style };
  if (template.preset) parts.preset = template.preset;
  for (const [key, value] of Object.entries(template.footage || {})) {
    if (!PER_REEL_FOOTAGE.has(key)) parts[`footage.${key}`] = value;
  }
  return parts;
}

function snapshot() {
  const p = state.project;
  return lookParts({ preset: p.preset, style: clone(p.style), footage: clone(p.footage) });
}

export function render(panel) {
  const preview = createPreview();
  const forms = h("div", { class: "stack-lg" });
  panel.append(h("div", { class: "split split-style" },
    forms,
    h("aside", { class: "preview-col", "aria-label": "Live preview" }, preview.el)));

  const rerender = () => {
    forms.replaceChildren();
    draw();
    preview.update(state.project);
  };

  async function applyTemplate(load, label) {
    const before = snapshot();
    try {
      const template = await load();
      if (!template) return;
      replaceParts(lookParts(template));
      rerender();
      toast(`${label} applied.`, {
        kind: "success",
        action: { label: "Undo", onClick: () => {
          replaceParts(before);
          rerender();
        } },
      });
    } catch (err) {
      if (isUnsupported(err)) {
        markUnsupported(load === loadDefaults ? "defaults" : "presets");
        rerender();
      }
      toastError(`Could not apply ${label.toLowerCase()}`, err);
    }
  }
  const loadDefaults = () => api.getDefaults();

  function presetCard() {
    const presets = state.meta.presets || [];
    const card = h("section", { class: "card", "aria-labelledby": "preset-title" });
    const head = h("div", { class: "card-head card-head-row" },
      h("div", {}, h("h2", { class: "card-title", id: "preset-title" }, "Look"),
        h("p", { class: "card-sub" }, "Start from a preset, then fine-tune anything below.")));
    if (supports("defaults")) {
      head.append(h("div", { class: "row" },
        button("Save as my defaults", {
          icon: "save", size: "sm", title: "New reels will start with this style and these settings",
          onClick: async () => {
            try {
              await api.saveDefaults(clone(state.project));
              toast("Saved. New reels start with these settings.", { kind: "success" });
            } catch (err) {
              if (isUnsupported(err)) {
                markUnsupported("defaults");
                rerender();
              }
              toastError("Could not save your defaults", err);
            }
          },
        }),
        menuButton({
          label: "More default options", size: "sm",
          items: () => [
            { label: "Reset this reel to my defaults", icon: "undo", onSelect: () => applyTemplate(loadDefaults, "Your defaults") },
            { label: "Forget my saved defaults", icon: "trash", danger: true, onSelect: forgetDefaults },
          ],
        })));
    }
    card.append(head);
    if (presets.length && supports("presets")) {
      const group = h("div", { class: "choice-grid choice-grid-3", role: "radiogroup", "aria-label": "Preset" });
      presets.forEach((p) => {
        const id = p.id ?? p;
        const { title, help } = presetLabel(p);
        const inputId = `preset-${id}`;
        const input = h("input", { type: "radio", name: "preset", id: inputId, class: "visually-hidden", checked: state.project.preset === id });
        input.addEventListener("change", () => input.checked && applyTemplate(() => api.getPreset(id), `The “${title}” preset`));
        group.append(input, h("label", { class: "choice choice-card preset-card", for: inputId },
          h("span", { class: `preset-swatch preset-${id}`, "aria-hidden": "true" }, h("span", {}, "Aa")),
          h("span", { class: "choice-title" }, title),
          help ? h("span", { class: "choice-help" }, help) : null));
      });
      card.append(group);
    }
    return card;
  }

  async function forgetDefaults() {
    const ok = await confirmDialog({
      title: "Forget your saved defaults?",
      message: "New reels will start from the built-in reference look again. This reel is not changed.",
      confirmLabel: "Forget defaults",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.resetDefaults();
      toast("Saved defaults removed.");
    } catch (err) {
      toastError("Could not reset your defaults", err);
    }
  }

  function draw() {
    forms.append(presetCard());
    const sections = schemaSections();
    const ordered = [
      ...STYLE_SECTIONS.map((id) => sections.find((s) => s.id === id)).filter(Boolean),
      ...sections.filter((s) => !STYLE_SECTIONS.includes(s.id) && !OTHER_TABS.has(s.id)),
    ];
    for (const section of ordered) {
      const form = renderFields(section.fields, projectBinding);
      if (!form) continue;
      const titleId = `sec-${section.id}`;
      forms.append(h("section", { class: "card", "aria-labelledby": titleId, dataset: { section: section.id } },
        h("div", { class: "card-head" },
          h("h2", { class: "card-title", id: titleId }, section.label),
          section.help || SECTION_INTRO[section.id] ? h("p", { class: "card-sub" }, section.help || SECTION_INTRO[section.id]) : null),
        form));
    }
    if (state.schemaInferred) {
      forms.append(h("p", { class: "muted small" }, h("span", { html: icon("info", { size: 14 }) }), " Setting descriptions are unavailable from this server; showing raw setting names."));
    }
  }

  /** Black bars only pad shapes wider than 9:16: say so when they are on for a 9:16 video. */
  function letterboxNote() {
    const card = forms.querySelector('[data-section="video"]');
    if (!card) return;
    const vs = state.project.style.video;
    const old = card.querySelector(".letterbox-note");
    if (!(vs.letterbox && vs.aspect === "9:16")) {
      if (old) old.remove();
      return;
    }
    if (old) return;
    const note = callout("warn", h("span", {},
      "Black bars only show with the 4:5 or 1:1 format: a 9:16 video already fills the screen, so nothing changes. ",
      button("Make it 4:5 with black bars", { size: "sm", variant: "secondary", onClick: () => {
        setValue("style.video.aspect", "4:5");
        clear(forms);
        draw();
        letterboxNote();
        preview.update(state.project);
        toast("Format set to 4:5 with black bars. Render again to see it.", { kind: "success" });
      } })));
    note.classList.add("letterbox-note");
    card.querySelector(".card-head").after(note);
  }

  draw();
  letterboxNote();
  preview.update(state.project);
  const off = on("change", ({ paths }) => {
    if (paths.some((p) => p === "style.video.aspect" || p === "style.video.letterbox")) letterboxNote();
    const focus = paths.some((p) => p.startsWith("style.hook")) ? "hook" : paths.some((p) => p.startsWith("style.caption")) ? "caption" : null;
    if (paths.some((p) => p.startsWith("style") || p === "script")) preview.update(state.project, focus);
  });
  return () => {
    off();
    preview.destroy();
  };
}
