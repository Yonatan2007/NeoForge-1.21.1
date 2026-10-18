/** Length tab: automatic or a fixed target duration, and how it is reached. */

import { callout } from "../components.js";
import { formatSeconds, h } from "../dom.js";
import { findField, findSection, renderFields } from "../fields.js";
import { lengthPlan, parseScript } from "../script-model.js";
import { on, projectBinding, schemaSections, setValue, state, uploadFor } from "../store.js";

const QUICK = [15, 30, 45, 60, 90];

/** The target length the server accepts (from its schema; 5-180 s otherwise). */
function targetLimits() {
  const f = findField(schemaSections(), "duration.target");
  return { min: (f && f.min) ?? 5, max: (f && f.max) ?? 180 };
}

function currentPlan() {
  const parsed = parseScript(state.project.script);
  const voice = uploadFor(state.project.voice.file);
  return { parsed, plan: lengthPlan(state.project, parsed, voice && voice.info ? voice.info.duration : null) };
}

/** Bar showing opening / voice / ending of the planned video. */
function fitBar() {
  const bar = h("div", { class: "fitbar", role: "img" });
  const legend = h("div", { class: "fit-legend" });
  const warn = h("div", {});
  const paint = () => {
    const { parsed, plan } = currentPlan();
    const vs = state.project.style.video;
    if (!parsed.words.length) {
      bar.replaceChildren();
      legend.replaceChildren(h("span", { class: "muted" }, "Write a script to see how the length works out."));
      warn.replaceChildren();
      return;
    }
    const total = plan.total;
    const outro = Math.max(0, total - plan.intro - plan.spoken);
    const seg = (cls, secs, label) => h("span", { class: `fit-seg ${cls}`, style: { flexGrow: String(Math.max(secs, 0.001)) }, title: `${label}: ${secs.toFixed(1)} s` });
    bar.replaceChildren(seg("fit-intro", plan.intro, "Opening"), seg("fit-voice", plan.spoken, "Voice"), seg("fit-outro", outro, "Ending"));
    bar.setAttribute("aria-label", `Video ${total.toFixed(1)} seconds: opening ${plan.intro.toFixed(1)}, voice ${plan.spoken.toFixed(1)}, ending ${outro.toFixed(1)}`);
    legend.replaceChildren(
      h("span", { class: "legend legend-intro" }, `Opening ${plan.intro.toFixed(1)} s`),
      h("span", { class: "legend legend-voice" }, `Voice ${plan.spoken.toFixed(1)} s${plan.tempo > 1.001 ? ` (${plan.tempo.toFixed(2)}× speed)` : ""}`),
      h("span", { class: "legend legend-outro" }, `Ending ${outro.toFixed(1)} s`),
      h("strong", { class: "fit-total" }, `= ${formatSeconds(total)}`));
    const notes = [];
    if (plan.overBy > 0.05) {
      notes.push(callout("warn", `The voice needs ${formatSeconds(total)} even at the fastest allowed speed, ${plan.overBy.toFixed(1)} s more than the target. Shorten the script or allow a faster tempo below.`));
    } else if (plan.target && outro - (vs.tail ?? 0.8) > 6) {
      notes.push(callout("info", `The last ${Math.round(outro)} s are picture and music only. Add to the script or choose a shorter length if that’s not what you want.`));
    }
    warn.replaceChildren(...notes);
  };
  paint();
  return { el: h("div", { class: "stack" }, bar, legend, warn), paint };
}

export function render(panel) {
  const ds = state.project.duration;
  let lastFixed = ds.target || 30;
  const { min: MIN_TARGET, max: MAX_TARGET } = targetLimits();

  const secs = h("input", { id: "target-secs", type: "number", class: "input input-num", min: MIN_TARGET, max: MAX_TARGET, step: 1, value: ds.target || lastFixed, inputmode: "numeric" });
  const quick = h("div", { class: "chips", role: "group", "aria-label": "Common lengths" });
  const fixedBox = h("div", { class: "stack fixed-box" },
    h("div", { class: "row row-wrap" },
      h("div", { class: "field field-compact" }, h("label", { class: "field-label", for: "target-secs" }, "Seconds"),
        h("div", { class: "input-group" }, secs, h("span", { class: "input-suffix" }, "s"))),
      quick));
  const setTarget = (v) => {
    const n = Math.min(MAX_TARGET, Math.max(MIN_TARGET, Math.round(Number(v))));
    if (!isFinite(n)) return;
    lastFixed = n;
    secs.value = n;
    setValue("duration.target", n);
    paintQuick();
  };
  const paintQuick = () => {
    quick.replaceChildren(...QUICK.map((q) => h("button", {
      type: "button", class: ["chip", "chip-btn", state.project.duration.target === q && "is-on"], "aria-pressed": String(state.project.duration.target === q),
      onclick: () => setTarget(q),
    }, `${q} s`)));
  };
  secs.addEventListener("change", () => setTarget(secs.value));

  const mode = (auto) => {
    fixedBox.hidden = auto;
    setValue("duration.target", auto ? null : lastFixed);
    paintQuick();
  };
  const choice = (id, value, title, help) => [
    h("input", { type: "radio", name: "length-mode", id, class: "visually-hidden", checked: value === !ds.target, onchange: () => mode(value) }),
    h("label", { class: "choice choice-card", for: id }, h("span", { class: "choice-title" }, title), h("span", { class: "choice-help" }, help)),
  ];
  fixedBox.hidden = !ds.target;
  paintQuick();

  const fit = fitBar();
  const section = findSection(schemaSections(), "duration");
  const tuning = section && renderFields(section.fields, projectBinding, { exclude: ["duration.target"], advancedLabel: "More" });

  panel.append(h("div", { class: "stack-lg" },
    h("section", { class: "card", "aria-labelledby": "len-title" },
      h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "len-title" }, "How long should the reel be?")),
      h("div", { class: "choice-grid", role: "radiogroup", "aria-labelledby": "len-title" },
        choice("len-auto", true, "Automatic", "As long as the voiceover needs, plus a short ending."),
        choice("len-fixed", false, "Fixed length", "Hit an exact duration, e.g. for a 30-second slot.")),
      fixedBox),
    h("section", { class: "card", "aria-labelledby": "fit-title" },
      h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "fit-title" }, "How it works out"),
        h("p", { class: "card-sub" }, "Estimated from the script and the voiceover.")),
      fit.el),
    h("section", { class: "card", "aria-labelledby": "rules-title" },
      h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "rules-title" }, "How a fixed length is reached")),
      h("p", {}, "Speech is never cut. reelforge fits the video to the target in this order:"),
      h("ol", { class: "rules" },
        h("li", {}, h("strong", {}, "Voice speed. "), "If the voice is too long, it is sped up slightly (never slowed down), up to the fastest tempo below."),
        h("li", {}, h("strong", {}, "Longer opening. "), "If the voice is short, music and picture play a little before the first word, up to the longest opening below."),
        h("li", {}, h("strong", {}, "Longer ending. "), "Whatever time is left becomes picture and music after the last word.")),
      h("p", { class: "muted small" }, "If the voice still doesn’t fit at the fastest tempo, the video runs longer than the target and the plan shows a warning."),
      tuning ? h("div", { class: "subsection" }, h("h3", { class: "subsection-title" }, "Fine-tuning"), tuning) : null)));

  const off = on("change", ({ paths }) => {
    if (paths.some((p) => p.startsWith("duration") || p.startsWith("voice") || p === "script" || p.startsWith("style.video"))) fit.paint();
  });
  return () => off();
}
