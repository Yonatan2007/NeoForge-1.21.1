/**
 * Music: the songs kept in the panel (music/<id>, see panel-backend.js), in a
 * library per account (the Autopilot accounts) with a half for its longer
 * videos and one for its comment videos: a song's `use` lists where it plays
 * ("gym/comment"; a song from before the libraries: the main account's both).
 * Autopilot deals each reel a song from its half of the account's library
 * (reelforge/autopilot.py, songs_for and pick_songs), and every reel's Music
 * tab can use any song with a tap. Each song has where it starts (to skip a
 * slow intro) and whether it is in the shuffle.
 */

import { api } from "./api.js";
import { callout, dropzone } from "./components.js";
import { button, confirmDialog, formatBytes, formatTime, h, toast, toastError } from "./dom.js";
import { segmented, switchControl } from "./fields.js";
import { icon } from "./icons.js";

const P = () => api._panel;
const ACCEPT = "audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac";
const MAIN = "main";
export const VIDEO_KINDS = [["watch", "Longer videos", "Under the narrated reels, quietly behind the voice."],
                            ["comment", "Comment videos", "Under the 7–10 s comment videos, which have no voice: the song carries them."]];
const KIND_NAME = Object.fromEntries(VIDEO_KINDS.map(([k, label]) => [k, label]));

/** The Autopilot accounts (panel-data/niches.json), the main one first. */
const accountList = () => (P().data.niches && P().data.niches.accounts) || [{ id: MAIN, niche: "philosophy", name: "Main account" }];
const accountName = (id) => (accountList().find((a) => a.id === id) || { name: id }).name;

/** Where a song plays: "<account>/<kind>" places (a song from before the libraries: the main account's both halves). */
export const songPlaces = (song) => (Array.isArray(song.use) ? song.use : VIDEO_KINDS.map(([k]) => `${MAIN}/${k}`));

/**
 * The songs a reel of `kind` on `account` is dealt from, and their place: that half of the account's library,
 * else its other half, else the main account's library (the same half first) - like autopilot.songs_for.
 */
export function songsFor(songs, account, kind) {
  const on = (songs || []).filter((s) => s.enabled !== false);
  const other = kind === "watch" ? "comment" : "watch";
  for (const place of new Set([`${account}/${kind}`, `${account}/${other}`, `${MAIN}/${kind}`, `${MAIN}/${other}`])) {
    const hit = on.filter((s) => songPlaces(s).includes(place));
    if (hit.length) return { songs: hit, place };
  }
  return { songs: [], place: null };
}

/** "the main account's comment videos" */
export const placeName = (place) => {
  const [account, kind] = place.split("/");
  return `${account === MAIN ? "the main account" : accountName(account)}'s ${KIND_NAME[kind].toLowerCase()}`;
};

/** One line on where an account's reels get their music (the Autopilot page). */
export function musicNote(songs, account) {
  const parts = VIDEO_KINDS.map(([kind, label]) => {
    const { songs: got, place } = songsFor(songs, account, kind);
    if (!place) return `${label}: no songs yet`;
    const n = `${got.length} song${got.length === 1 ? "" : "s"}`;
    return place === `${account}/${kind}` ? `${label}: ${n}` : `${label}: ${n} from ${placeName(place)}`;
  });
  return `${parts.join(" · ")}.`;
}
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

export function renderMusic(container, sub) {
  const { listSongs, addSong, updateSong, deleteSong, songUrl } = P();
  const all = accountList();
  const account = all.find((a) => a.id === sub) || all[0];
  let songs = [];
  let target = "both"; // where new songs go: "watch" | "comment" | "both"
  let playing = null; // id of the song playing
  const audio = new Audio();
  audio.preload = "none";
  const trendAudio = new Audio(); // TikTok's trending sounds, apart from the songs
  trendAudio.preload = "none";
  const uploads = h("ul", { class: "upload-list" });
  const reuse = h("div", { class: "music-reuse" });
  const halves = Object.fromEntries(VIDEO_KINDS.map(([kind]) => [kind, h("section", { class: "card song-card", dataset: { kind } })]));
  const placesFor = (t) => (t === "both" ? VIDEO_KINDS.map(([k]) => `${account.id}/${k}`) : [`${account.id}/${t}`]);

  container.append(
    head("Music", "Each account has its own songs: one list for its longer videos and one for its comment videos. Autopilot gives each reel a song from its list at random, and any reel's Music tab can use any of them.",
      h("a", { class: "btn btn-ghost", href: account.id === MAIN ? "#/x/autopilot" : `#/x/autopilot/${account.id}`,
               html: `${icon("wand", { size: 18 })}<span class="btn-label">Autopilot</span>` })),
    h("nav", { class: "ap-accounts", "aria-label": "Accounts" }, all.map((a) => h("a", {
      class: ["ap-account", a.id === account.id && "is-current"], href: a.id === MAIN ? "#/x/music" : `#/x/music/${a.id}`,
      "aria-current": a.id === account.id ? "page" : null }, a.name))),
    h("div", { class: "stack-lg" },
      h("section", { class: "card", "aria-labelledby": "music-add" },
        h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "music-add" }, `Add songs to ${account.name}`),
          h("p", { class: "card-sub" }, "Several at once is fine. They stay until you delete them.")),
        h("div", { class: "field" }, h("div", { class: "field-head" }, h("span", { class: "field-label", id: "music-target" }, "Add them to")),
          segmented({ labelledBy: "music-target", value: target, onChange: (v) => { target = v; paintReuse(); },
                      options: [{ value: "watch", label: "Longer videos" }, { value: "comment", label: "Comment videos" },
                                { value: "both", label: "Both" }] })),
        dropzone({ accept: ACCEPT, multiple: true, title: "Drop songs here, or browse", hint: "MP3, WAV, M4A, OGG, FLAC",
                   iconName: "music", onFiles: add }),
        uploads, reuse),
      ...VIDEO_KINDS.map(([kind]) => halves[kind]),
      callout("info", "Use songs you're allowed to post: Instagram and YouTube can mute copyrighted music."),
      trendingCard(trendAudio)));

  async function add(files) {
    const use = placesFor(target);
    for (const file of files) {
      const bar = h("div", { class: "progress-fill" });
      const pct = h("span", { class: "upload-pct" }, "0 %");
      const row = h("li", { class: "upload-row" }, h("span", { class: "upload-name" }, file.name),
        h("span", { class: "upload-size" }, formatBytes(file.size)), pct,
        h("div", { class: "progress progress-thin", role: "progressbar", "aria-label": `Adding ${file.name}` }, bar));
      uploads.append(row);
      try {
        await addSong(file, { use, onProgress: (f) => {
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

  /** Put a song in (or take it out of) some of this account's lists; a song in no list at all is deleted. */
  async function setPlaces(song, places) {
    if (!places.length) {
      const ok = await confirmDialog({ title: `Delete “${song.name}”?`, message: "It isn't in any other list, so it leaves the panel. Reels that already use it keep it.",
                                        confirmLabel: "Delete", danger: true });
      if (!ok) return;
      if (playing === song.id) audio.pause();
      await deleteSong(song.id);
    } else {
      await updateSong(song.id, { use: places });
    }
    await load();
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

  function row(song, kind) {
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
    const shuffle = switchControl({ id: `song-on-${kind}-${song.id}`, checked: song.enabled !== false, label: "In the shuffle",
      onChange: async (v) => {
        try {
          await updateSong(song.id, { enabled: v });
          song.enabled = v;
          paint();
        } catch (err) {
          toastError("Could not save that", err);
        }
      } });
    const elsewhere = songPlaces(song).filter((p) => p !== `${account.id}/${kind}`);
    const remove = button(`Remove ${song.name} from ${KIND_NAME[kind].toLowerCase()}`, { icon: "x", iconOnly: true, variant: "ghost",
      onClick: () => setPlaces(song, elsewhere).catch((err) => toastError("Could not change the list", err)) });
    const also = elsewhere.length ? h("span", { class: "muted small" }, `Also in ${elsewhere.map(placeName).join(", ")}`) : null;
    return h("li", { class: ["song-row", song.enabled === false && "is-off"] }, playBtn,
      h("div", { class: "song-main" }, h("span", { class: "song-name" }, song.name),
        h("span", { class: "muted small" }, [duration ? formatTime(duration, false) : null, song.size ? formatBytes(song.size) : null].filter(Boolean).join(" · ")),
        also),
      h("label", { class: "song-start" }, h("span", { class: "muted small" }, "Starts at"), start, h("span", { class: "muted small" }, "s")),
      h("div", { class: "song-shuffle" }, shuffle), remove);
  }

  function paintHalf(kind, label, sub) {
    const mine = songs.filter((s) => songPlaces(s).includes(`${account.id}/${kind}`));
    const { place } = songsFor(songs, account.id, kind);
    const on = mine.filter((s) => s.enabled !== false).length;
    const note = mine.length
      ? `${on} of ${mine.length} in the shuffle.`
      : place ? `No songs here yet, so these videos use ${placeName(place)}.` : "No songs here yet, so these videos use the music of the reel Autopilot copies.";
    halves[kind].replaceChildren(
      h("div", { class: "card-head" }, h("h2", { class: "card-title" }, mine.length ? `${label} (${mine.length})` : label),
        h("p", { class: "card-sub" }, sub), h("p", { class: "card-sub music-note" }, note)),
      mine.length ? h("ul", { class: "song-list" }, mine.map((s) => row(s, kind)))
        : h("p", { class: "muted empty-inline" }, "Add songs above, or pick one you already added."));
  }

  /** Songs added before (to any list) that aren't in the chosen list yet: one tap puts them there too. */
  function paintReuse() {
    const want = placesFor(target);
    const others = songs.filter((s) => want.some((p) => !songPlaces(s).includes(p)));
    if (!others.length) return reuse.replaceChildren();
    const pick = h("select", { class: "input select", "aria-label": "A song you already added" },
      h("option", { value: "" }, "Or add a song you already have…"),
      others.map((s) => h("option", { value: s.id }, s.name)));
    pick.addEventListener("change", async () => {
      const song = songs.find((s) => s.id === pick.value);
      if (!song) return;
      try {
        await setPlaces(song, [...new Set([...songPlaces(song), ...want])]);
        toast(`“${song.name}” added.`, { kind: "success", timeout: 2500 });
      } catch (err) {
        toastError("Could not add the song", err);
      }
    });
    reuse.replaceChildren(pick);
  }

  function paint() {
    VIDEO_KINDS.forEach(([kind, label, sub]) => paintHalf(kind, label, sub));
    paintReuse();
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
