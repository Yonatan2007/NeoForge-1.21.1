/**
 * Voice picker for the control panel: every voice in the user's Higgsfield
 * account (built-in and their own), read through the Higgsfield connector,
 * with search, a gender filter and a link to each voice's sample.
 * The list is kept in settings/voices so it shows at once next time.
 */

import { api } from "./api.js";
import { button, h, toastError } from "./dom.js";
import { segmented } from "./fields.js";
import { icon } from "./icons.js";
import { on, setValue, state } from "./store.js";

const PAGE = 24;
let cache = null; // {voices, fetched}

async function fetchVoices(mcp) {
  const voices = [];
  let cursor;
  for (let i = 0; i < 10; i++) {
    const res = await mcp.callTool("higgsfield", "list_voices", cursor ? { size: 100, cursor } : { size: 100 });
    const page = res.payload || {};
    for (const v of page.voices || []) {
      voices.push({ id: v.voice_id, type: v.voice_type || "preset", name: v.name || "Voice",
                    gender: v.gender || "", preview: v.preview_url || null });
    }
    if (!page.has_more || !page.next_cursor) break;
    cursor = page.next_cursor;
  }
  return voices;
}

async function loadVoices({ refresh = false } = {}) {
  const panel = api._panel;
  if (cache && !refresh) return cache;
  const doc = panel.db.doc("settings/voices");
  if (!refresh) {
    const snap = await panel.retrying(() => doc.get()).catch(() => null);
    if (snap && snap.exists && (snap.data().voices || []).length) {
      cache = snap.data();
      return cache;
    }
  }
  if (!panel.mcp) throw new Error("The Higgsfield connector isn't available in this view.");
  const voices = await fetchVoices(panel.mcp);
  cache = { voices, fetched: new Date().toISOString() };
  await panel.retrying(() => doc.set(cache)).catch(() => {});
  return cache;
}

/** The picker element shown on the Voice tab when Claude makes the voice. */
export function voicePicker() {
  const defaultId = api._panel.data.voice.default_voice_id;
  let gender = "all";
  let query = "";
  let limit = PAGE;
  let voices = [];

  const search = h("input", { class: "input", type: "search", placeholder: "Search voices", "aria-label": "Search voices", autocomplete: "off" });
  const list = h("ul", { class: "voice-grid", "aria-label": "Voices" });
  const status = h("p", { class: "muted small", role: "status" }, "Loading your Higgsfield voices…");
  const more = button("Show more", { variant: "ghost", size: "sm" });
  const refresh = button("Refresh", { icon: "refresh", variant: "ghost", size: "sm" });
  const filter = segmented({ options: [{ value: "all", label: "All" }, { value: "female", label: "Female" }, { value: "male", label: "Male" }],
                             value: gender, label: "Gender", size: "sm", onChange: (v) => { gender = v; limit = PAGE; paint(); } });

  const current = () => state.project.voice.voice_id || defaultId;

  function choose(v) {
    setValue("voice.voice_id", v.id === defaultId ? null : v.id);
    setValue("voice.voice_type", v.type);
    paint();
  }

  function paint() {
    const q = query.trim().toLowerCase();
    const shown = voices.filter((v) => (gender === "all" || v.gender === gender) && (!q || v.name.toLowerCase().includes(q)));
    const selected = voices.find((v) => v.id === current());
    // the chosen voice is always visible, first
    const ordered = selected && shown.includes(selected) ? [selected, ...shown.filter((v) => v !== selected)] : shown;
    list.replaceChildren(...ordered.slice(0, limit).map((v) => {
      const on = v.id === current();
      const id = `voice-${v.id}`;
      const input = h("input", { type: "radio", name: "voice-pick", id, class: "visually-hidden", checked: on });
      input.addEventListener("change", () => input.checked && choose(v));
      return h("li", { class: ["voice-card", on && "is-on"] }, input,
        h("label", { for: id, class: "voice-main" },
          h("span", { class: "voice-avatar", "aria-hidden": "true" }, v.name.slice(0, 1)),
          h("span", { class: "voice-text" },
            h("span", { class: "voice-name" }, v.name, v.id === defaultId ? h("span", { class: "chip-note" }, " default") : null),
            h("span", { class: "voice-sub" }, [v.gender, v.type === "element" ? "your voice" : null].filter(Boolean).join(" · ") || "voice"))),
        v.preview ? h("a", { class: "btn btn-ghost btn-sm voice-listen", href: v.preview, target: "_blank", rel: "noopener",
                            "aria-label": `Listen to ${v.name}`, html: `${icon("play", { size: 14 })}<span class="btn-label">Listen</span>` }) : null);
    }));
    more.hidden = ordered.length <= limit;
    status.textContent = voices.length
      ? `${selected ? `Using ${selected.name}. ` : ""}${shown.length} of ${voices.length} voices. Listen opens the sample in a new tab.`
      : status.textContent;
  }

  async function load(refreshing = false) {
    try {
      const res = await loadVoices({ refresh: refreshing });
      voices = res.voices || [];
      paint();
    } catch (err) {
      status.textContent = "";
      toastError("Could not load your Higgsfield voices", err);
      status.textContent = "Your voices could not be loaded. Allow the panel to use Higgsfield, then press Refresh.";
    }
  }

  search.addEventListener("input", () => { query = search.value; limit = PAGE; paint(); });
  more.addEventListener("click", () => { limit += PAGE * 2; paint(); });
  refresh.addEventListener("click", () => load(true));
  const el = h("div", { class: "voice-picker stack" },
    h("div", { class: "voice-toolbar" }, search, filter, refresh), status, list, more);
  const off = on("change", ({ paths } = {}) => {
    if (!el.isConnected) return off(); // the Voice tab was redrawn or left
    if ((paths || []).includes("voice.voice_id")) paint();
  });
  load();
  return el;
}
