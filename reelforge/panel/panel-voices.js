/**
 * Voice picker for the control panel: every voice in the user's Higgsfield
 * account (built-in and their own), read through the Higgsfield connector,
 * with search, a gender filter and the voice's sample played in the page
 * (samples are stored in the panel; voices without one link to Higgsfield's).
 * The list is kept in settings/voices so it shows at once next time.
 */

import { api } from "./api.js";
import { button, h, toastError } from "./dom.js";
import { segmented } from "./fields.js";
import { icon } from "./icons.js";
import { on, setValue, state } from "./store.js";

const PAGE = 24;
let cache = null; // {voices, fetched}
const player = new Audio();
let playing = null; // voice id

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
  const { data, blobUrl } = api._panel;
  const defaultId = data.voice.default_voice_id;
  const samples = data.voicePreviews || {};
  let gender = "all";
  let query = "";
  let limit = PAGE;
  let voices = [];

  const search = h("input", { class: "input", type: "search", placeholder: "Search 100+ voices", "aria-label": "Search voices", autocomplete: "off" });
  const list = h("ul", { class: "voice-grid", "aria-label": "Voices" });
  const status = h("p", { class: "muted small", role: "status" }, "Loading your Higgsfield voices…");
  const more = button("Show more voices", { variant: "secondary", size: "sm" });
  const refresh = button("Refresh", { icon: "refresh", variant: "ghost", size: "sm" });
  const filter = segmented({ options: [{ value: "all", label: "All" }, { value: "female", label: "Female" }, { value: "male", label: "Male" }],
                             value: gender, label: "Gender", size: "sm", onChange: (v) => { gender = v; limit = PAGE; paint(); } });

  const current = () => state.project.voice.voice_id || defaultId;

  function choose(v) {
    setValue("voice.voice_id", v.id === defaultId ? null : v.id);
    setValue("voice.voice_type", v.type);
    paint();
  }

  function play(v) {
    if (playing === v.id) {
      player.pause();
      return;
    }
    player.src = blobUrl(samples[v.id]);
    player.dataset.voice = v.id;
    playing = v.id; // shown at once; the media events below keep it true
    player.play().catch(() => {
      playing = null;
      if (el.isConnected) paint();
    });
    paint();
  }

  function paint() {
    // the list is redrawn; keep keyboard focus on the same voice's play button
    const focused = document.activeElement && document.activeElement.closest ? document.activeElement.closest(".voice-play") : null;
    const focusVoice = focused && list.contains(focused) ? focused.dataset.voice : null;
    const q = query.trim().toLowerCase();
    const shown = voices.filter((v) => (gender === "all" || v.gender === gender) && (!q || v.name.toLowerCase().includes(q)));
    const selected = voices.find((v) => v.id === current());
    // the chosen voice is always first, so it is never hidden behind "Show more"
    const ordered = selected && shown.includes(selected) ? [selected, ...shown.filter((v) => v !== selected)] : shown;
    list.replaceChildren(...ordered.slice(0, limit).map((v) => {
      const isOn = v.id === current();
      const id = `voice-${v.id}`;
      const input = h("input", { type: "radio", name: "voice-pick", id, class: "visually-hidden", checked: isOn });
      input.addEventListener("change", () => input.checked && choose(v));
      let listen = null;
      if (samples[v.id]) {
        const isPlaying = playing === v.id;
        listen = h("button", { type: "button", class: ["voice-play", isPlaying && "is-playing"], "aria-pressed": String(isPlaying), dataset: { voice: v.id },
                               "aria-label": `${isPlaying ? "Stop" : "Play"} ${v.name}'s sample`,
                               html: icon(isPlaying ? "stop" : "play", { size: 16 }) });
        listen.addEventListener("click", () => play(v));
      } else if (v.preview) {
        listen = h("a", { class: "voice-play", href: v.preview, target: "_blank", rel: "noopener",
                          "aria-label": `Listen to ${v.name} (opens a new tab)`, html: icon("play", { size: 16 }) });
      }
      return h("li", { class: ["voice-card", isOn && "is-on"] }, input,
        listen,
        h("label", { for: id, class: "voice-main" },
          h("span", { class: "voice-text" },
            h("span", { class: "voice-name" }, v.name),
            h("span", { class: "voice-sub" }, [v.gender, v.type === "element" ? "your voice" : null, v.id === defaultId ? "default" : null]
              .filter(Boolean).join(" · ") || "voice")),
          h("span", { class: "voice-check", html: icon("check", { size: 14 }) })));
    }));
    if (focusVoice) {
      const again = [...list.querySelectorAll(".voice-play")].find((b) => b.dataset.voice === focusVoice);
      if (again) again.focus();
    }
    more.hidden = ordered.length <= limit;
    if (voices.length) {
      status.textContent = `${selected ? `Reading voice: ${selected.name}. ` : ""}${shown.length} of ${voices.length} voices.`;
    }
  }

  async function load(refreshing = false) {
    try {
      const res = await loadVoices({ refresh: refreshing });
      voices = res.voices || [];
      paint();
    } catch (err) {
      toastError("Could not load your Higgsfield voices", err);
      status.textContent = "Your voices could not be loaded. Allow the panel to use Higgsfield, then press Refresh.";
    }
  }

  // a stale "pause" from the previous sample can arrive after a new one starts
  player.onplaying = () => {
    playing = player.dataset.voice || null;
    if (el.isConnected) paint();
  };
  player.onended = player.onpause = () => {
    if (!player.paused && !player.ended) return;
    playing = null;
    if (el.isConnected) paint();
  };
  search.addEventListener("input", () => { query = search.value; limit = PAGE; paint(); });
  more.addEventListener("click", () => { limit += PAGE * 2; paint(); });
  refresh.addEventListener("click", () => load(true));
  const el = h("div", { class: "voice-picker stack" },
    h("div", { class: "voice-toolbar" }, search, filter, refresh), status, list, h("div", { class: "row" }, more));
  const off = on("change", ({ paths } = {}) => {
    if (!el.isConnected) {
      player.pause();
      return off(); // the Voice tab was redrawn or left
    }
    if ((paths || []).includes("voice.voice_id")) paint();
  });
  load();
  return el;
}
