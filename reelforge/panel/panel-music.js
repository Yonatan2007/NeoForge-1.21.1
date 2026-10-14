/**
 * Music: the songs kept in the panel (music/<id>, see panel-backend.js).
 * Autopilot deals them at random to its reels (reelforge/autopilot.py,
 * pick_songs), and every reel's Music tab can use one with a tap. Each song
 * has where it starts (to skip a slow intro) and whether it is in the shuffle.
 */

import { api } from "./api.js";
import { callout, dropzone } from "./components.js";
import { button, confirmDialog, formatBytes, formatTime, h, toast, toastError } from "./dom.js";
import { switchControl } from "./fields.js";
import { icon } from "./icons.js";

const P = () => api._panel;
const ACCEPT = "audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac";
const GENRES = [["LO-FI", "Lo-fi"], ["CHILL_BEATS", "Chill"], ["EPIC", "Cinematic"], ["ALL", "Everything"]];

/**
 * TikTok's trending licensed sounds this week (the viewer's Higgsfield
 * connector, tiktok_music_trending). With Autopilot's "Remind me to add a
 * trending sound", each TikTok gets one of these to add at volume 0: TikTok
 * shows the reel to that sound's listeners while the reel's own music plays.
 */
function trendingCard(player) {
  let genre = "LO-FI";
  let playing = null;
  let tracks = [];
  let connector = null;
  const list = h("div", {});
  const pick = h("select", { class: "input select", "aria-label": "Kind of sound" },
    GENRES.map(([v, l]) => h("option", { value: v, selected: v === genre }, l)));
  pick.addEventListener("change", () => { genre = pick.value; load(); });
  player.addEventListener("ended", () => { playing = null; paint(); });

  function paint() {
    list.replaceChildren(tracks.length ? h("ul", { class: "trend-list" }, tracks.map((t) => {
      const on = playing === t.song_clip_id;
      const play = button(on ? `Pause ${t.name}` : `Play ${t.name}`, { icon: on ? "pause" : "play", iconOnly: true, variant: "ghost",
        onClick: () => {
          if (on) {
            player.pause();
            playing = null;
            return paint();
          }
          player.src = t.preview_url;
          player.play().then(() => { playing = t.song_clip_id; paint(); })
            .catch((err) => toastError(`Could not play “${t.name}”`, err));
        } });
      return h("li", { class: "trend-row" }, t.thumbnail_url ? h("img", { src: t.thumbnail_url, alt: "" }) : null,
        h("div", { class: "trend-main" }, h("strong", {}, `${t.rank}. ${t.name}`), h("span", { class: "muted small" }, t.artist || "")),
        t.preview_url ? play : null);
    })) : h("p", { class: "muted empty-inline" }, "TikTok has no chart for this kind of sound right now."));
  }

  async function load() {
    const mcp = P().mcp;
    if (!mcp) {
      list.replaceChildren(h("p", { class: "muted empty-inline" }, "Connectors can't be used in this view."));
      return;
    }
    list.replaceChildren(h("p", { class: "muted" }, "Reading TikTok's chart…"));
    try {
      if (!connector) {
        const acc = await mcp.callTool("higgsfield", "tiktok_accounts", {});
        const account = ((acc && acc.payload && acc.payload.accounts) || []).find((a) => a.status === "active");
        if (!account) {
          list.replaceChildren(h("p", { class: "muted empty-inline" }, "Connect TikTok on the Accounts page to see its trending sounds."));
          return;
        }
        connector = account.connector_id;
      }
      const res = await mcp.callTool("higgsfield", "tiktok_music_trending", { connector_id: connector, genre, limit: 10 });
      tracks = (res && res.payload && res.payload.tracks) || [];
      paint();
    } catch (err) {
      list.replaceChildren(h("p", { class: "muted empty-inline" }, `TikTok's chart couldn't be read (${(err && (err.message || err.code)) || "unknown error"}).`));
    }
  }
  load();
  return h("section", { class: "card", "aria-labelledby": "music-trending" },
    h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "music-trending" }, "Trending on TikTok"),
      h("p", { class: "card-sub" }, "Licensed sounds TikTok is pushing this week. With “Remind me to add a trending sound” on the Autopilot page, each TikTok gets one of these to add at volume 0: TikTok shows the reel to that sound's listeners while your own music plays.")),
    h("div", { class: "row trend-pick" }, pick), list);
}

function head(title, lead, ...actions) {
  return h("header", { class: "home-head" },
    h("div", {}, h("h1", { class: "page-title" }, title), lead ? h("p", { class: "page-lead" }, lead) : null),
    actions.length ? h("div", { class: "row" }, actions) : null);
}

export function renderMusic(container) {
  const { listSongs, addSong, updateSong, deleteSong, songUrl } = P();
  let songs = [];
  let playing = null; // id of the song playing
  const audio = new Audio();
  audio.preload = "none";
  const trendAudio = new Audio(); // TikTok's trending sounds, apart from the songs
  trendAudio.preload = "none";
  const uploads = h("ul", { class: "upload-list" });
  const list = h("div", { class: "card song-card" });

  container.append(
    head("Music", "Songs you add here are kept in the panel. Autopilot gives each reel one of them at random, and any reel's Music tab can use them.",
      h("a", { class: "btn btn-ghost", href: "#/x/autopilot", html: `${icon("wand", { size: 18 })}<span class="btn-label">Autopilot</span>` })),
    h("div", { class: "stack-lg" },
      h("section", { class: "card", "aria-labelledby": "music-add" },
        h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "music-add" }, "Add songs"),
          h("p", { class: "card-sub" }, "Several at once is fine. They stay until you delete them.")),
        dropzone({ accept: ACCEPT, multiple: true, title: "Drop songs here, or browse", hint: "MP3, WAV, M4A, OGG, FLAC",
                   iconName: "music", onFiles: add }),
        uploads),
      list,
      callout("info", "Use songs you're allowed to post: Instagram and YouTube can mute copyrighted music."),
      trendingCard(trendAudio)));

  async function add(files) {
    for (const file of files) {
      const bar = h("div", { class: "progress-fill" });
      const pct = h("span", { class: "upload-pct" }, "0 %");
      const row = h("li", { class: "upload-row" }, h("span", { class: "upload-name" }, file.name),
        h("span", { class: "upload-size" }, formatBytes(file.size)), pct,
        h("div", { class: "progress progress-thin", role: "progressbar", "aria-label": `Adding ${file.name}` }, bar));
      uploads.append(row);
      try {
        await addSong(file, { onProgress: (f) => {
          bar.style.width = `${Math.round(f * 100)}%`;
          pct.textContent = f >= 1 ? "Saved" : `${Math.round(f * 100)} %`;
        } });
      } catch (err) {
        toastError(`Could not add “${file.name}”`, err);
      } finally {
        row.remove();
      }
    }
    await load();
    toast(files.length === 1 ? "Song added." : "Songs added.", { kind: "success", timeout: 2500 });
  }

  async function play(song, btn) {
    if (playing === song.id) {
      audio.pause();
      playing = null;
      return paint();
    }
    try {
      btn.classList.add("is-loading");
      const url = await songUrl(song);
      if (!url) throw new Error("its file couldn't be read");
      audio.src = url;
      audio.currentTime = Number(song.start) || 0;
      await audio.play();
      playing = song.id;
    } catch (err) {
      toastError(`Could not play “${song.name}”`, err);
      playing = null;
    } finally {
      btn.classList.remove("is-loading");
      paint();
    }
  }
  audio.addEventListener("ended", () => {
    playing = null;
    paint();
  });

  function row(song) {
    const duration = song.info && song.info.duration;
    const playBtn = button(playing === song.id ? `Pause ${song.name}` : `Play ${song.name}`, {
      icon: playing === song.id ? "pause" : "play", iconOnly: true, variant: "ghost", class: "song-play",
      "aria-pressed": String(playing === song.id), onClick: () => play(song, playBtn) });
    const start = h("input", { class: "input input-num", type: "number", min: 0, step: 0.5, value: song.start || 0,
                               max: duration ? Math.floor(duration) : null, "aria-label": `Where ${song.name} starts, in seconds` });
    start.addEventListener("change", async () => {
      const v = Math.max(0, Math.min(Number(start.value) || 0, duration ? duration - 5 : Infinity));
      start.value = v;
      try {
        await updateSong(song.id, { start: v });
        song.start = v;
      } catch (err) {
        toastError("Could not save where the song starts", err);
      }
    });
    const shuffle = switchControl({ id: `song-on-${song.id}`, checked: song.enabled !== false, label: "In the shuffle",
      onChange: async (v) => {
        try {
          await updateSong(song.id, { enabled: v });
          song.enabled = v;
          paintCount();
        } catch (err) {
          toastError("Could not save that", err);
        }
      } });
    const remove = button(`Delete ${song.name}`, { icon: "trash", iconOnly: true, variant: "ghost", onClick: async () => {
      const ok = await confirmDialog({ title: `Delete “${song.name}”?`, message: "It leaves the library. Reels that already use it keep it.",
                                        confirmLabel: "Delete", danger: true });
      if (!ok) return;
      try {
        if (playing === song.id) audio.pause();
        await deleteSong(song.id);
        await load();
      } catch (err) {
        toastError("Could not delete the song", err);
      }
    } });
    return h("li", { class: ["song-row", song.enabled === false && "is-off"] }, playBtn,
      h("div", { class: "song-main" }, h("span", { class: "song-name" }, song.name),
        h("span", { class: "muted small" }, [duration ? formatTime(duration, false) : null, song.size ? formatBytes(song.size) : null].filter(Boolean).join(" · "))),
      h("label", { class: "song-start" }, h("span", { class: "muted small" }, "Starts at"), start, h("span", { class: "muted small" }, "s")),
      h("div", { class: "song-shuffle" }, shuffle), remove);
  }

  const count = h("p", { class: "card-sub" });
  const paintCount = () => {
    const on = songs.filter((s) => s.enabled !== false).length;
    count.textContent = songs.length ? `${on} of ${songs.length} in the shuffle Autopilot deals from.` : "";
  };

  function paint() {
    paintCount();
    list.replaceChildren(
      h("div", { class: "card-head" }, h("h2", { class: "card-title" }, songs.length ? `Your songs (${songs.length})` : "Your songs"), count),
      songs.length ? h("ul", { class: "song-list" }, songs.map(row))
        : h("p", { class: "muted empty-inline" }, "No songs yet. Add the songs you want your reels to use."));
  }

  async function load() {
    try {
      songs = await listSongs();
    } catch (err) {
      toastError("Could not load your songs", err);
    }
    paint();
  }

  load();
  return () => {
    for (const a of [audio, trendAudio]) {
      a.pause();
      a.removeAttribute("src");
    }
  };
}
