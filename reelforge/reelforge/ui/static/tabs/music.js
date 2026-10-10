/**
 * Music tab: upload a track, choose the part of the song on its waveform
 * (source_in..source_out), place it on the video timeline (start_at..end_at)
 * and set level, fades, looping and ducking.
 */

import { api } from "../api.js";
import { callout, dropzone, uploadAll } from "../components.js";
import { basename, button, clamp, clear, formatTime, h, toast, toastError } from "../dom.js";
import { findSection, renderFields } from "../fields.js";
import { icon } from "../icons.js";
import { lengthPlan, parseScript } from "../script-model.js";
import {
  fileUrl, on, optional, projectBinding, removeUpload, schemaSections, setValue, state, supports, uploadFor,
} from "../store.js";
import { RangeTrack, axis, drawWaveform } from "../waveform.js";

const HANDLED = ["music.file", "music.source_in", "music.source_out", "music.start_at", "music.end_at"];
const peakCache = new Map(); // url/path -> {duration, peaks}

const round1 = (v) => Math.round(v * 10) / 10;

/** A small numeric seconds input that commits on change/Enter. */
function secondsInput({ id, label, value, placeholder = "", onCommit }) {
  const input = h("input", { id, type: "text", inputmode: "decimal", class: "input input-num", value: value == null ? "" : round1(value).toFixed(1), placeholder, autocomplete: "off" });
  input.addEventListener("change", () => {
    const text = input.value.trim();
    if (text === "") return onCommit(null);
    const parts = text.split(":").map(Number);
    const v = parts.length === 2 ? parts[0] * 60 + parts[1] : parts[0];
    if (!isFinite(v)) {
      input.value = value == null ? "" : round1(value).toFixed(1);
      return;
    }
    onCommit(Math.max(0, v));
  });
  input.setValue = (v) => {
    value = v;
    if (document.activeElement !== input) input.value = v == null ? "" : round1(v).toFixed(1);
  };
  return { input, el: h("div", { class: "field field-compact" }, h("label", { class: "field-label", for: id }, label), h("div", { class: "input-group" }, input, h("span", { class: "input-suffix" }, "s"))) };
}

async function loadPeaks(path, uploadInfo) {
  if (peakCache.has(path)) return peakCache.get(path);
  if (uploadInfo && uploadInfo.peaks && uploadInfo.peaks.peaks) {
    peakCache.set(path, uploadInfo.peaks);
    return uploadInfo.peaks;
  }
  const res = await optional("waveform", () => api.waveform(state.projectId, path, 1000));
  if (res) peakCache.set(path, res);
  return res || null;
}

function videoLength() {
  const parsed = parseScript(state.project.script);
  const voice = uploadFor(state.project.voice.file);
  const plan = lengthPlan(state.project, parsed, voice && voice.info ? voice.info.duration : null);
  return { plan, total: Math.max(plan.total, 3) };
}

/** Song editor: waveform with the selection, play controls and numbers. */
function songEditor(path, peaksData, audio) {
  const m = state.project.music;
  const duration = (peaksData && peaksData.duration) || audio.duration || 60;
  let sIn = clamp(m.source_in || 0, 0, duration);
  let sOut = clamp(m.source_out ?? duration, sIn, duration);

  const canvas = h("canvas", { class: "wave-canvas", "aria-hidden": "true" });
  const playhead = h("div", { class: "playhead", hidden: true });
  const lenOut = h("output", { class: "stat-value" });
  const inField = secondsInput({ id: "music-in", label: "From", value: sIn, onCommit: (v) => commit(v ?? 0, sOut) });
  const outField = secondsInput({ id: "music-out", label: "To", value: sOut, onCommit: (v) => commit(sIn, v ?? duration) });

  const redraw = () => drawWaveform(canvas, peaksData && peaksData.peaks, duration, sIn, sOut);
  const show = () => {
    inField.input.setValue(sIn);
    outField.input.setValue(sOut);
    lenOut.textContent = formatTime(sOut - sIn);
    redraw();
  };
  const commit = (a, b) => {
    sIn = clamp(a, 0, duration - 0.5);
    sOut = clamp(b, sIn + 0.5, duration);
    track.set({ start: sIn, end: sOut });
    show();
    setValue("music.source_in", round1(sIn));
    setValue("music.source_out", sOut >= duration - 0.05 ? null : round1(sOut));
  };

  // Playback of the selection ---------------------------------------------
  let raf = 0;
  let stopAt = sOut;
  const playBtn = button("Play selection", { icon: "play", variant: "primary", size: "sm" });
  const setPlaying = (on) => {
    playBtn.querySelector(".btn-label").textContent = on ? "Stop" : "Play selection";
    playBtn.querySelector("svg").outerHTML = icon(on ? "stop" : "play", { size: 16 });
    playhead.hidden = !on;
  };
  const tick = () => {
    playhead.style.left = `${(audio.currentTime / duration) * 100}%`;
    if (audio.currentTime >= stopAt || audio.ended) stop();
    else raf = requestAnimationFrame(tick);
  };
  const play = (from) => {
    audio.currentTime = from;
    stopAt = from < sOut ? sOut : duration;
    audio.play().then(() => {
      setPlaying(true);
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(tick);
    }, (err) => toastError("Cannot play this file", err));
  };
  const stop = () => {
    audio.pause();
    cancelAnimationFrame(raf);
    setPlaying(false);
  };
  playBtn.addEventListener("click", () => (audio.paused ? play(sIn) : stop()));

  const track = new RangeTrack({
    duration, start: sIn, end: sOut, minLength: 0.5, className: "wave-track",
    labels: { start: "Song selection start", end: "Song selection end", range: "Song selection (move with arrow keys)" },
    onInput: (a, b) => {
      sIn = a;
      sOut = b;
      show();
    },
    onCommit: (a, b) => commit(a, b),
    onClick: (t) => play(t),
  });
  track.content.append(canvas, playhead);

  const editor = h("div", { class: "wave-editor" },
    track.el, axis(duration),
    h("div", { class: "wave-controls" },
      playBtn,
      h("p", { class: "muted small wave-hint" }, "Drag the edges or the highlighted part. Click the waveform to listen from there."),
      h("div", { class: "wave-numbers" }, inField.el, outField.el,
        h("div", { class: "field field-compact" }, h("span", { class: "field-label" }, "Length"), lenOut))));
  editor.addEventListener("keydown", (e) => {
    if (e.key === " " && !e.target.matches("input, button, textarea, select")) {
      e.preventDefault();
      playBtn.click();
    }
  });
  const ro = new ResizeObserver(redraw);
  ro.observe(canvas);
  show();
  return {
    el: editor,
    selection: () => sOut - sIn,
    destroy: () => {
      stop();
      ro.disconnect();
    },
  };
}

/** Where the music sits on the video, next to the voice. */
function placementEditor(selectionLength) {
  const m = state.project.music;
  const { plan, total } = videoLength();
  let start = clamp(m.start_at || 0, 0, total - 0.5);
  let end = m.end_at == null ? total : clamp(m.end_at, start + 0.5, total);
  let toEnd = m.end_at == null;

  const voiceBar = h("div", { class: "lane-voice", title: "Voice" });
  const loops = h("div", { class: "lane-loops" });
  const fadeIn = h("div", { class: "fade fade-in" });
  const fadeOut = h("div", { class: "fade fade-out" });
  const startField = secondsInput({ id: "music-start", label: "Starts at", value: start, onCommit: (v) => commit(v ?? 0, toEnd ? total : end) });
  const endField = secondsInput({ id: "music-end", label: "Stops at", value: toEnd ? null : end, placeholder: "End", onCommit: (v) => commit(start, v == null ? total : v, v == null) });
  const toEndId = "music-to-end";
  const toEndBox = h("input", { type: "checkbox", id: toEndId, checked: toEnd });
  toEndBox.addEventListener("change", () => commit(start, toEndBox.checked ? total : Math.min(end, total - 0.1), toEndBox.checked));

  const decorate = () => {
    const ms = state.project.music;
    const span = end - start;
    voiceBar.style.left = `${(plan.intro / total) * 100}%`;
    voiceBar.style.width = `${(Math.min(plan.spoken, total - plan.intro) / total) * 100}%`;
    clear(loops);
    if (ms.loop && selectionLength > 0.5 && span > selectionLength) {
      for (let t = selectionLength; t < span - 0.05; t += selectionLength) {
        loops.append(h("span", { class: "loop-mark", style: { left: `${(t / span) * 100}%` } }));
      }
    }
    const playable = ms.loop ? span : Math.min(span, selectionLength || span);
    loops.style.width = `${(playable / span) * 100}%`;
    fadeIn.style.width = `${clamp((ms.fade_in || 0) / span, 0, 0.5) * 100}%`;
    fadeOut.style.width = `${clamp((ms.fade_out || 0) / span, 0, 0.5) * 100}%`;
    fadeOut.style.right = `${100 - (playable / span) * 100}%`;
  };
  const show = () => {
    startField.input.setValue(start);
    endField.input.setValue(toEnd ? null : end);
    toEndBox.checked = toEnd;
    decorate();
  };
  const commit = (a, b, untilEnd = b >= total - 0.05) => {
    start = clamp(a, 0, total - 0.5);
    end = clamp(b, start + 0.5, total);
    toEnd = untilEnd;
    if (toEnd) end = total;
    track.set({ start, end });
    show();
    setValue("music.start_at", round1(start));
    setValue("music.end_at", toEnd ? null : round1(end));
  };

  const track = new RangeTrack({
    duration: total, start, end, minLength: 0.5, className: "timeline-track",
    labels: { start: "Music starts in the video", end: "Music stops in the video", range: "Music on the video timeline" },
    onInput: (a, b) => {
      start = a;
      end = b;
      toEnd = b >= total - 0.05;
      show();
    },
    onCommit: (a, b) => commit(a, b),
  });
  track.region.querySelector(".track-region-inner").append(loops, fadeIn, fadeOut,
    h("span", { class: "region-label", html: `${icon("music", { size: 14 })}<span>Music</span>` }));
  track.content.append(voiceBar);

  const el = h("div", { class: "timeline" },
    h("div", { class: "timeline-legend", "aria-hidden": "true" },
      h("span", { class: "legend legend-music" }, "Music"), h("span", { class: "legend legend-voice" }, "Voice"),
      h("span", { class: "muted" }, `Video ≈ ${formatTime(total, false)}`)),
    track.el, axis(total),
    h("div", { class: "wave-numbers" }, startField.el, endField.el,
      h("label", { class: "checkbox", for: toEndId }, toEndBox, h("span", {}, "Play until the video ends"))));
  show();
  return { el, refresh: decorate };
}

export function render(panel) {
  const body = h("div", { class: "stack-lg" });
  panel.append(body);
  let song = null;
  let placement = null;
  let audio = null;
  const offs = [];

  const teardown = () => {
    if (song) song.destroy();
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    song = placement = audio = null;
  };

  const draw = async () => {
    teardown();
    clear(body);
    const m = state.project.music;
    const progress = h("ul", { class: "upload-list" });
    const upload = (files) => uploadAll(files, "music", progress).then((done) => {
      if (done.length) {
        if (done[0].peaks) peakCache.set(state.project.music.file, done[0].peaks);
        toast("Music added.", { kind: "success" });
        draw();
      }
    });

    if (!m.file) {
      body.append(h("section", { class: "card", "aria-labelledby": "music-add" },
        h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "music-add" }, "Add a music track"),
          h("p", { class: "card-sub" }, "Background music under the voice. Use music you have the rights to.")),
        supports("upload")
          ? [dropzone({ accept: "audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac", title: "Drop a song here, or browse", hint: "MP3, WAV, M4A, OGG, FLAC", iconName: "music", onFiles: upload }), progress]
          : callout("info", "This server does not accept uploads.")));
      body.append(callout("info", "No music? That’s fine: the reel uses only the voice."));
      return;
    }

    const info = uploadFor(m.file);
    audio = new Audio();
    audio.preload = "metadata";
    audio.src = fileUrl(m.file);
    const header = h("div", { class: "file-card" },
      h("span", { class: "file-icon", html: icon("music", { size: 20 }) }),
      h("div", { class: "file-main" },
        h("div", { class: "file-name" }, basename(m.file)),
        h("div", { class: "file-sub" }, info && info.info && info.info.duration ? `${formatTime(info.info.duration, false)} long` : "Music track")),
      supports("upload") ? dropzone({ accept: "audio/*", title: "Replace", iconName: "refresh", compact: true, onFiles: upload }) : null,
      button("Remove music", {
        icon: "trash", variant: "ghost", iconOnly: true, onClick: async () => {
          try {
            await removeUpload(m.file);
            draw();
          } catch (err) {
            toastError("Could not remove the music", err);
          }
        },
      }));
    const songCard = h("section", { class: "card", "aria-labelledby": "music-part" },
      h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "music-part" }, "Pick the part of the song")),
      header, progress, h("div", { class: "wave-loading" }, h("span", { class: "spinner", role: "status", "aria-label": "Loading waveform" })));
    const placeCard = h("section", { class: "card", "aria-labelledby": "music-place" },
      h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "music-place" }, "Where it plays in the video"),
        h("p", { class: "card-sub" }, "Drag the music block or its edges. It loops to fill the block when looping is on.")));
    body.append(songCard, placeCard);

    const section = findSection(schemaSections(), "music");
    const sound = section && renderFields(section.fields, projectBinding, { exclude: HANDLED });
    if (sound) {
      body.append(h("section", { class: "card", "aria-labelledby": "music-sound" },
        h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "music-sound" }, "Sound")), sound));
    }

    let peaks = null;
    try {
      peaks = await loadPeaks(m.file, info);
    } catch (err) {
      toastError("Could not read the waveform", err);
    }
    if (!peaks || !peaks.duration) {
      await new Promise((resolve) => {
        if (audio.readyState >= 1) resolve();
        audio.addEventListener("loadedmetadata", resolve, { once: true });
        audio.addEventListener("error", resolve, { once: true });
      });
    }
    if (!audio || state.project.music.file !== m.file) return; // replaced meanwhile
    song = songEditor(m.file, peaks, audio);
    songCard.querySelector(".wave-loading").replaceWith(song.el);
    if (!peaks) songCard.append(h("p", { class: "muted small" }, "Waveform not available on this server; the selection still works."));
    placement = placementEditor(song.selection());
    placeCard.append(placement.el);
  };

  draw();
  offs.push(on("change", ({ paths }) => {
    if (!placement) return;
    if (paths.some((p) => p.startsWith("music.source_") || p === "music.loop" || p.startsWith("music.fade"))) placement.refresh();
    if (paths.some((p) => p.startsWith("music.source_"))) {
      const fresh = placementEditor(song.selection());
      placement.el.replaceWith(fresh.el);
      placement = fresh;
    }
  }));
  return () => {
    offs.forEach((f) => f());
    teardown();
  };
}
