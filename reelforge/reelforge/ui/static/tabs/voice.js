/** Voice tab: who reads the script (upload, Higgsfield via Claude, Piper or none). */

import { api } from "../api.js";
import { callout, dropzone, fieldFor, uploadAll } from "../components.js";
import { basename, button, clear, copyText, formatSeconds, formatTime, h, toast, toastError } from "../dom.js";
import { findSection, renderFields } from "../fields.js";
import { icon } from "../icons.js";
import { lengthPlan, parseScript } from "../script-model.js";
import {
  flush, on, optional, projectBinding, removeUpload, schemaSections, setValue, state, supports, uploadFor,
} from "../store.js";

const SOURCES = {
  file: { label: "Upload a recording", help: "Your own voiceover file, or a link to one.", icon: "upload" },
  higgsfield: { label: "Higgsfield via Claude", help: "Claude makes the voice with your connected Higgsfield account.", icon: "sparkle" },
  "higgsfield-api": { label: "Higgsfield API", help: "Generates the voice directly with an API key.", icon: "wand", capability: "higgsfield_api" },
  piper: { label: "Piper (offline)", help: "Free draft voice that runs on this computer.", icon: "voice", capability: "piper" },
  none: { label: "No voice", help: "Music only; captions follow a reading pace.", icon: "music" },
};

// Settings shown with each source; everything else in the voice section goes under "More".
const SOURCE_FIELDS = {
  file: [],
  higgsfield: ["voice.higgsfield_preset", "voice.voice_id"],
  "higgsfield-api": ["voice.higgsfield_preset", "voice.voice_id"],
  piper: ["voice.piper_model"],
  none: ["voice.words_per_second"],
};
const HANDLED = ["voice.source", "voice.file", ...new Set(Object.values(SOURCE_FIELDS).flat())];

function sourceList() {
  const fromMeta = (state.meta.voice_sources || []).map((s) => (typeof s === "string" ? { id: s } : { id: s.id ?? s.value, label: s.label }));
  const ids = fromMeta.length ? fromMeta : Object.keys(SOURCES).map((id) => ({ id }));
  return ids.map(({ id, label }) => ({ id, ...(SOURCES[id] || { help: "", icon: "voice" }), label: label || (SOURCES[id] || {}).label || id }));
}

const capable = (src) => !src.capability || !state.meta.capabilities || state.meta.capabilities[src.capability] !== false;

function sourcePicker(onPick) {
  const current = state.project.voice.source;
  const group = h("div", { class: "choice-grid", role: "radiogroup", "aria-label": "Voice source" });
  for (const src of sourceList()) {
    const id = `voice-src-${src.id}`;
    const input = h("input", { type: "radio", name: "voice-source", id, class: "visually-hidden", checked: src.id === current });
    input.addEventListener("change", () => input.checked && onPick(src.id));
    group.append(input, h("label", { class: "choice choice-card", for: id },
      h("span", { class: "choice-icon", html: icon(src.icon, { size: 20 }) }),
      h("span", { class: "choice-title" }, src.label, capable(src) ? null : h("span", { class: "badge" }, "Not set up")),
      h("span", { class: "choice-help" }, src.help)));
  }
  return group;
}

/** The current voice file: player, duration and actions; or a drop zone. */
function voiceFileBlock(rerender) {
  const v = state.project.voice;
  const wrap = h("div", { class: "stack" });
  if (v.file) {
    const isUrl = /^https?:\/\//.test(v.file);
    const info = uploadFor(v.file);
    const src = isUrl ? v.file : (info && info.url) || api.fileUrl(state.projectId, v.file);
    const duration = h("span", { class: "muted" }, info && info.info && info.info.duration ? formatTime(info.info.duration) : "");
    const audio = h("audio", { controls: true, preload: "metadata", src, class: "audio" });
    audio.addEventListener("loadedmetadata", () => {
      if (!duration.textContent && isFinite(audio.duration)) duration.textContent = formatTime(audio.duration);
    });
    wrap.append(h("div", { class: "file-card" },
      h("span", { class: "file-icon", html: icon(isUrl ? "link" : "voice", { size: 20 }) }),
      h("div", { class: "file-main" },
        h("div", { class: "file-name" }, isUrl ? v.file : basename(v.file)),
        h("div", { class: "file-sub" }, isUrl ? "Link" : "Uploaded recording", " · ", duration)),
      button("Remove voiceover", {
        icon: "trash", variant: "ghost", iconOnly: true, onClick: async () => {
          try {
            if (isUrl) setValue("voice.file", null);
            else await removeUpload(v.file);
            rerender();
          } catch (err) {
            toastError("Could not remove the file", err);
          }
        },
      })), audio);
  }
  const progress = h("ul", { class: "upload-list" });
  if (supports("upload")) {
    wrap.append(dropzone({
      accept: "audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac",
      title: v.file ? "Replace with another file" : "Drop your voiceover here, or browse",
      hint: "MP3, WAV, M4A · silence at the start and end is trimmed automatically",
      iconName: "upload",
      compact: Boolean(v.file),
      onFiles: async (files) => {
        const done = await uploadAll(files, "voice", progress);
        if (done.length) {
          toast("Voiceover added.", { kind: "success" });
          rerender();
        }
      },
    }), progress);
  }
  const urlId = "voice-url";
  const url = h("input", { id: urlId, type: "url", class: "input", placeholder: "https://…/voice.mp3", autocomplete: "off" });
  const addUrl = () => {
    const value = url.value.trim();
    if (!/^https?:\/\/\S+$/.test(value)) {
      url.setCustomValidity("Paste a link that starts with https://");
      url.reportValidity();
      return;
    }
    url.setCustomValidity("");
    setValue("voice.file", value);
    rerender();
  };
  url.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addUrl();
    }
  });
  wrap.append(h("div", { class: "field" },
    h("label", { class: "field-label", for: urlId }, "Or paste a link to the audio"),
    h("div", { class: "input-row" }, url, button("Use link", { onClick: addUrl }))));
  return wrap;
}

/** Step 1 for Higgsfield via Claude: the request JSON to hand to Claude. */
function higgsfieldBlock() {
  const code = h("pre", { class: "code", tabindex: "0", "aria-label": "Higgsfield request for Claude" }, "Loading…");
  const copyBtn = button("Copy request", { icon: "copy", variant: "primary", size: "sm" });
  let request = null;
  const load = async () => {
    try {
      await flush();
      request = await optional("higgsfieldRequest", () => api.higgsfieldRequest(state.projectId));
      if (request === undefined) request = state.plan && state.plan.higgsfield_request;
      code.textContent = request ? JSON.stringify(request, null, 2) : "";
      block.hidden = !request;
      scriptOnly.hidden = Boolean(request);
    } catch (err) {
      code.textContent = `Could not build the request: ${err.message}`;
    }
  };
  copyBtn.addEventListener("click", async () => {
    if (!request) return;
    const ok = await copyText(JSON.stringify(request, null, 2));
    toast(ok ? "Request copied. Paste it to Claude." : "Copy failed: select the text and copy it.", { kind: ok ? "success" : "error" });
  });
  const block = h("div", { class: "stack" },
    h("p", {}, "Copy this and send it to Claude. Claude generates the voice with your Higgsfield account and gives you an audio link or file. Add it below."),
    h("div", { class: "code-wrap" }, code, h("div", { class: "code-actions" }, copyBtn,
      button("Refresh", { icon: "refresh", variant: "ghost", size: "sm", onClick: load }))));
  const scriptOnly = h("div", { class: "stack", hidden: true },
    h("p", {}, "Ask Claude to read your script with Higgsfield, then add the audio below."),
    button("Copy script", { icon: "copy", size: "sm", onClick: async () => {
      const ok = await copyText(parseScript(state.project.script).text);
      toast(ok ? "Script copied." : "Copy failed.", { kind: ok ? "success" : "error" });
    } }));
  load();
  return { el: h("div", {}, block, scriptOnly), reload: load };
}

export function render(panel) {
  let hf = null;
  const body = h("div", { class: "stack-lg" });
  panel.append(body);

  let offPace = null;
  const draw = () => {
    clear(body);
    if (offPace) offPace();
    offPace = null;
    const v = state.project.voice;
    const src = sourceList().find((s) => s.id === v.source) || { id: v.source, label: v.source };

    body.append(h("section", { class: "card", "aria-labelledby": "voice-src-title" },
      h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "voice-src-title" }, "Who reads the script?")),
      sourcePicker((id) => {
        setValue("voice.source", id);
        draw();
      })));

    if (!capable(src)) {
      body.append(callout("warn", src.id === "piper"
        ? "Piper is not installed on this computer. Install it (pip install piper-tts) and download a voice model, or pick another source."
        : "The Higgsfield API is not configured: add HF_KEY and HIGGSFIELD_TTS_ENDPOINT to the .env file, or use Higgsfield via Claude."));
    }

    const extraFields = (SOURCE_FIELDS[v.source] || []).map((p) => fieldFor(p)).filter(Boolean);
    const claudeVoice = Boolean(state.meta.capabilities && state.meta.capabilities.claude_voice);
    if (v.source === "higgsfield" && claudeVoice) {
      // Claude renders this reel (control panel): it makes the voice itself.
      hf = null;
      body.append(h("section", { class: "card", "aria-labelledby": "hfc" },
        h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "hfc" }, "Claude makes the voice"),
          h("p", { class: "card-sub" }, "With your connected Higgsfield account, when you render.")),
        h("div", { class: "stack" },
          extraFields.length ? h("div", { class: "form-grid" }, extraFields) : null,
          h("p", { class: "muted" }, "About 1 credit for every 50 words. To use your own recording instead, choose “Upload a voiceover” above."))));
    } else if (v.source === "higgsfield") {
      hf = higgsfieldBlock();
      body.append(h("section", { class: "card step-card", "aria-labelledby": "hf1" },
        h("div", { class: "card-head" }, h("span", { class: "step-num" }, "1"), h("h2", { class: "card-title", id: "hf1" }, "Ask Claude for the voice")),
        extraFields.length ? h("div", { class: "form-grid" }, extraFields) : null,
        hf.el));
      body.append(h("section", { class: "card step-card", "aria-labelledby": "hf2" },
        h("div", { class: "card-head" }, h("span", { class: "step-num" }, "2"), h("h2", { class: "card-title", id: "hf2" }, "Add the voiceover")),
        voiceFileBlock(draw)));
    } else if (v.source === "file") {
      body.append(h("section", { class: "card", "aria-labelledby": "vf" },
        h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "vf" }, "Your voiceover")),
        voiceFileBlock(draw)));
    } else if (extraFields.length) {
      const card = h("section", { class: "card", "aria-labelledby": "vs" },
        h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "vs" }, src.label)),
        h("div", { class: "form-grid" }, extraFields));
      if (v.source === "none") {
        const est = h("p", { class: "muted", role: "status" });
        const paint = () => {
          const parsed = parseScript(state.project.script);
          est.textContent = parsed.words.length
            ? `At this pace the ${parsed.words.length} words take about ${formatSeconds(lengthPlan(state.project, parsed).speech)}.`
            : "";
        };
        paint();
        card.append(est);
        offPace = on("change", ({ paths }) => paths.includes("voice.words_per_second") && paint());
      }
      body.append(card);
    }

    const section = findSection(schemaSections(), "voice");
    const more = section && renderFields(section.fields, projectBinding, { exclude: HANDLED, advancedLabel: "More voice settings" });
    if (more) {
      body.append(h("section", { class: "card", "aria-labelledby": "vmore" },
        h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "vmore" }, "Timing"),
          h("p", { class: "card-sub" }, "How word timings are found for the captions.")),
        more));
    }
  };

  draw();
  const offChange = on("change", ({ paths }) => {
    if (hf && paths.some((p) => p === "voice.higgsfield_preset" || p === "voice.voice_id")) hf.reload();
  });
  return () => {
    offChange();
    if (offPace) offPace();
  };
}
