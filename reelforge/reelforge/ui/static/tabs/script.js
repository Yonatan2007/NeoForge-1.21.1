/** Script tab: the text, markup help, hook highlight and a live caption preview. */

import { isUnsupported } from "../api.js";
import { button, debounce, formatSeconds, h, toast, toastError } from "../dom.js";
import { switchControl } from "../fields.js";
import { applyCase, chunkWords, lengthPlan, parseScript, scriptRanges, splitHook } from "../script-model.js";
import { markUnsupported, on, setValue, state, supports, uploadFor, uploadFile } from "../store.js";

const PLACEHOLDER = "If you lost your memory, who would you trust to tell you who you are?\n\n" +
  "Write a short script: one thought per sentence. Wrap a word in *stars* to colour it.";

const escapeHtml = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

/** Markup for the highlight layer behind the textarea. */
function backdropHtml(raw, ranges) {
  const marks = new Array(raw.length + 1).fill(null).map(() => new Set());
  for (const r of ranges) for (let i = r.start; i < r.end; i++) marks[i].add(r.kind);
  let out = "";
  let cur = "";
  let buf = "";
  const flush = () => {
    if (!buf) return;
    out += cur ? `<mark class="${cur}">${escapeHtml(buf)}</mark>` : escapeHtml(buf);
    buf = "";
  };
  for (let i = 0; i < raw.length; i++) {
    const cls = [...marks[i]].sort().map((k) => `m-${k}`).join(" ");
    if (cls !== cur) {
      flush();
      cur = cls;
    }
    buf += raw[i];
  }
  flush();
  return out + "\n​"; // keep the last (empty) line measurable
}

/** Wrap the selection (or the word at the caret) in `mark`. */
function wrapSelection(ta, mark) {
  let { selectionStart: a, selectionEnd: b, value } = ta;
  if (a === b) {
    while (a > 0 && /\S/.test(value[a - 1])) a--;
    while (b < value.length && /\S/.test(value[b])) b++;
  }
  if (a === b) return;
  let inner = value.slice(a, b);
  const trail = inner.match(/[.,!?;:…"'”’)]+$/);
  if (trail) {
    inner = inner.slice(0, -trail[0].length);
    b -= trail[0].length;
  }
  if (!inner.trim()) return;
  ta.setRangeText(`${mark}${inner}${mark}`, a, b, "end");
  ta.dispatchEvent(new Event("input", { bubbles: true }));
  ta.focus();
}

function hookPreview(hookWords, cs, hs) {
  if (hs.mode === "center") {
    return h("p", { class: "muted small" }, "The opening sentence is shown as normal captions (Style → Opening line).");
  }
  if (!hookWords.length) return null;
  const n = hookWords.length;
  const words = hookWords.map((w, i) => {
    const big = i === 0 || i === n - 1 || w.emphasis > 0;
    const small = !big && w.norm.length <= 3;
    return h("span", { class: ["hook-word", big && "big", small && "small", i === n - 1 && "last"] }, applyCase(w.text, cs.case));
  });
  return h("div", { class: "hook-card" },
    h("div", { class: "hook-card-label" }, hs.mode === "off" ? "Opening line · spoken only (no text)" : "Opening line · laid along the skyline"),
    h("p", { class: ["hook-line", hs.mode === "off" && "is-off"] }, words));
}

function captionChips(body, cs) {
  const chunks = chunkWords(body, cs);
  const colour = cs.emphasis === "color";
  return {
    count: chunks.length,
    el: h("ol", { class: "caption-chips", "aria-label": "Captions in order" }, chunks.map((chunk) =>
      h("li", { class: "caption-chip" }, chunk.map((w, i) => [
        i ? " " : "",
        h("span", { class: colour && w.emphasis ? `em${w.emphasis}` : null }, applyCase(w.text, cs.case)),
      ])))),
  };
}

export function render(panel) {
  const p = state.project;
  const ta = h("textarea", {
    id: "script-text", class: "script-input", spellcheck: true, placeholder: PLACEHOLDER,
    "aria-describedby": "script-help", "aria-label": "Script",
  }, p.script || "");
  const backdrop = h("div", { class: "script-backdrop", "aria-hidden": "true" });
  const editor = h("div", { class: "script-editor" }, backdrop, ta);
  const stats = h("div", { class: "script-stats", role: "status", "aria-live": "polite" });

  const fileInput = h("input", { type: "file", accept: ".txt,.md,text/plain,text/markdown", class: "visually-hidden", tabindex: "-1", "aria-hidden": "true" });
  const uploadBtn = button("Upload .txt", { icon: "upload", size: "sm", onClick: () => fileInput.click() });
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files[0];
    fileInput.value = "";
    if (file) await loadFile(file);
  });

  const toolbar = h("div", { class: "toolbar" },
    h("div", { class: "toolbar-group", role: "group", "aria-label": "Colour key words" },
      button("Yellow", { size: "sm", variant: "ghost", title: "Colour the selected words yellow (*word*)", class: "swatch-btn swatch-yellow", onClick: () => wrapSelection(ta, "*") }),
      button("Red", { size: "sm", variant: "ghost", title: "Colour the selected words red (**word**)", class: "swatch-btn swatch-red", onClick: () => wrapSelection(ta, "**") })),
    h("div", { class: "toolbar-spacer" }),
    uploadBtn, fileInput);

  const help = h("div", { class: "markup-help", id: "script-help" },
    h("p", {}, "The ", h("mark", { class: "m-hook" }, "highlighted"), " first sentence is the opening line. ",
      "Wrap words in ", h("code", {}, "*stars*"), " for yellow or ", h("code", {}, "**double stars**"),
      " for red key words; the stars are not read aloud."));

  const autoEmphasis = h("div", { class: "field field-bool" },
    switchControl({
      id: "auto-emphasis", checked: p.auto_emphasis !== false, label: "Find key words automatically",
      describedBy: "auto-emphasis-help", onChange: (v) => setValue("auto_emphasis", v),
    }),
    h("p", { class: "field-help", id: "auto-emphasis-help" }, "Stresses words like “never”, “today” or “love” even without stars."));

  const editorCard = h("section", { class: "card card-flush script-card", "aria-label": "Script editor" },
    toolbar, editor, h("div", { class: "card-foot" }, stats));

  const previewBody = h("div", { class: "preview-body" });
  const previewCard = h("section", { class: "card", "aria-labelledby": "reads-title" },
    h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "reads-title" }, "How it will read")),
    previewBody);

  panel.append(h("div", { class: "split split-script" },
    h("div", { class: "stack" }, editorCard, help, autoEmphasis),
    previewCard));

  const autosize = () => {
    ta.style.height = "auto";
    ta.style.height = `${Math.max(ta.scrollHeight, 220)}px`;
  };

  const paint = () => {
    const raw = ta.value;
    const project = state.project;
    const hs = project.style.hook;
    const cs = project.style.caption;
    backdrop.innerHTML = backdropHtml(raw, scriptRanges(raw, hs.mode === "center" ? 0 : hs.sentences));
    autosize();

    const parsed = parseScript(raw);
    const voice = uploadFor(project.voice.file);
    const plan = lengthPlan(project, parsed, voice && voice.info ? voice.info.duration : null);
    const { hook, body } = splitHook(parsed.words, project);
    const chips = captionChips(body, cs);
    stats.replaceChildren(
      h("span", {}, h("strong", {}, String(parsed.words.length)), " words"),
      h("span", {}, h("strong", {}, String(parsed.sentences)), parsed.sentences === 1 ? " sentence" : " sentences"),
      h("span", {}, "≈ ", h("strong", {}, formatSeconds(plan.total)), " video"));

    if (!parsed.words.length) {
      previewBody.replaceChildren(h("div", { class: "empty-inline" },
        h("p", {}, "Your captions appear here as you type."),
        h("p", { class: "muted small" }, "Tip: short sentences with one idea each make the best reels.")));
      return;
    }
    const usesMarkup = parsed.words.some((w) => w.emphasis);
    previewBody.replaceChildren(
      hookPreview(hook, cs, hs) || "",
      body.length ? h("div", { class: "preview-section" },
        h("div", { class: "preview-label" }, `Then ${chips.count} caption${chips.count === 1 ? "" : "s"}`,
          cs.max_words === 1 ? h("span", { class: "muted" }, " · one word at a time") : null),
        chips.el) : "",
      usesMarkup && cs.emphasis !== "color"
        ? h("p", { class: "note" }, "Key-word colours are switched off in this style, so starred words show as plain captions. Turn them on in Style → Captions.")
        : "");
  };

  const save = debounce(() => setValue("script", ta.value), 250);
  ta.addEventListener("input", () => {
    paint();
    save();
  });
  ta.addEventListener("blur", () => {
    save.cancel();
    if (ta.value !== state.project.script) setValue("script", ta.value);
  });

  async function loadFile(file) {
    if (file.size > 2_000_000) {
      toast("That file is too large for a script.", { kind: "error" });
      return;
    }
    try {
      if (supports("upload")) {
        await uploadFile(file, "script");
      } else {
        setValue("script", (await file.text()).trim());
      }
      ta.value = state.project.script;
      paint();
      toast(`Loaded “${file.name}”.`, { kind: "success" });
    } catch (err) {
      if (isUnsupported(err)) {
        markUnsupported("upload");
        setValue("script", (await file.text()).trim());
        ta.value = state.project.script;
        paint();
      } else toastError("Could not load the script", err);
    }
  }

  // Dropping a text file anywhere on the editor loads it.
  editor.addEventListener("dragover", (e) => {
    if ([...e.dataTransfer.items].some((i) => i.kind === "file")) {
      e.preventDefault();
      editor.classList.add("is-drop");
    }
  });
  editor.addEventListener("dragleave", () => editor.classList.remove("is-drop"));
  editor.addEventListener("drop", (e) => {
    const file = [...e.dataTransfer.files][0];
    editor.classList.remove("is-drop");
    if (!file) return;
    e.preventDefault();
    loadFile(file);
  });

  const ro = new ResizeObserver(autosize);
  ro.observe(editor);
  paint();
  const off = on("change", ({ paths }) => {
    if (paths.some((x) => x.startsWith("style") || x.startsWith("voice") || x.startsWith("duration"))) paint();
  });
  return () => {
    save.cancel();
    if (ta.value !== state.project.script) setValue("script", ta.value);
    ro.disconnect();
    off();
  };
}
