/**
 * Footage tab, control panel only: choose the stock clips before rendering.
 *
 * "Find clips" asks Claude (a "review" task) to search the stock sites with
 * the reel's settings; Claude writes the candidates to reviews/<project id>
 *   {last_task, created, sheet: asset id of one picture with every thumbnail,
 *    tile: {w, h, cols, rows}, rest_shots, stock_shots, opening: [clip], pool: [clip], warnings}
 * (a clip is footage.pick_of plus its tile number). Tapping a clip keeps it,
 * "Never" rules it out; the project saves them as footage.picks (slot
 * "opening" for the first, skyline shot) and footage.banned, which the render
 * uses before searching.
 */

import { api } from "./api.js";
import { callout } from "./components.js";
import { button, h, relativeTime, toast, toastError } from "./dom.js";
import { icon } from "./icons.js";
import { flush, state, touched } from "./store.js";

const P = () => api._panel;
const SITES = { mixkit: "Mixkit", pexels: "Pexels", pixabay: "Pixabay" };

/** "close up of a clockface ticking" from a stock page address. */
function clipTitle(c) {
  const slug = String(c.page_url || "").replace(/[?#].*$/, "").replace(/\/+$/, "").split("/").pop() || "";
  const words = slug.replace(/[-_]?\d+$/, "").replace(/[-_]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : `${SITES[c.provider] || c.provider} clip`;
}

const keyOf = (c) => c.key || `${c.provider}_${c.id}`;
const pickOf = (c, slot) => {
  const { tile, portrait, ...rest } = c; // eslint-disable-line no-unused-vars
  return slot ? { ...rest, slot } : rest;
};

export function footageReview() {
  const pid = state.projectId;
  const status = h("p", { class: "card-sub", role: "status", "aria-live": "polite" });
  const find = button("Find clips", { icon: "search", size: "sm", variant: "secondary" });
  const body = h("div", { class: "stack-lg" });
  const el = h("section", { class: "card review-card", "aria-labelledby": "review-title" },
    h("div", { class: "card-head card-head-row" },
      h("div", {}, h("h2", { class: "card-title", id: "review-title" }, "Choose the stock clips"), status),
      find),
    body);
  if (!P() || !pid) return { el, cleanup() {} };

  const { db, retrying, blobUrl, newTask } = P();
  let review = null;
  let task = null;
  let unsubTask = null;
  let watching = null;
  const fs = () => state.project.footage;

  const watchTask = (id) => {
    if (unsubTask) unsubTask();
    unsubTask = db.doc(`tasks/${id}`).onSnapshot((s) => {
      task = s.exists ? s.data() : null;
      paintStatus();
    }, () => {});
  };
  const unsubReview = db.doc(`reviews/${pid}`).onSnapshot((s) => {
    const next = s.exists ? s.data() : null;
    const fresh = !review || !next || next.created !== review.created;
    review = next;
    if (review && review.last_task && review.last_task !== watching) {
      watching = review.last_task;
      watchTask(review.last_task);
    }
    if (fresh) paint();
    else paintStatus();
  }, () => paint());

  find.addEventListener("click", async () => {
    if (!fs().stock) return toast("Turn on stock footage first.", { kind: "error" });
    find.classList.add("is-loading");
    try {
      await flush(); // Claude reads the saved settings
      const { id, problem } = await newTask("review", pid);
      const ref = db.doc(`reviews/${pid}`);
      const snap = await retrying(() => ref.get());
      await retrying(() => (snap.exists ? ref.update({ last_task: id }) : ref.set({ last_task: id, created: null })));
      watching = id;
      watchTask(id);
      toast(problem ? "Saved. Claude wasn't reached: send “review my clips” in your Claude chat." : "Sent to Claude: finding clips…",
        { kind: problem ? "error" : "success" });
    } catch (err) {
      toastError("Could not ask for clips", err);
    } finally {
      find.classList.remove("is-loading");
    }
  });

  function paintStatus() {
    const busy = task && ["queued", "running"].includes(task.status);
    find.querySelector(".btn-label").textContent = review && review.created ? "Find new clips" : "Find clips";
    find.disabled = Boolean(busy);
    if (busy) status.textContent = task.notify_error ? task.message : (task.message || "Claude is searching the stock sites…");
    else if (task && task.status === "error") status.textContent = `The search failed: ${task.error || task.message || "unknown error"}`;
    else if (review && review.created) {
      const n = (review.opening || []).length + (review.pool || []).length;
      status.textContent = `${n} clips found ${relativeTime(review.created)}. Tap the ones you want; the render uses them first.`;
    } else status.textContent = "Claude searches the stock sites with this reel's settings and shows you the clips, so you can keep the ones you like before rendering.";
    el.classList.toggle("is-busy", Boolean(busy));
  }

  function paint() {
    paintStatus();
    if (!fs().stock) {
      body.replaceChildren(callout("info", "Stock footage is off for this reel, so there's nothing to choose."));
      return;
    }
    if (!review || !review.created || !review.sheet) {
      body.replaceChildren();
      return;
    }
    const sheet = blobUrl(review.sheet);
    const tile = review.tile || { cols: 1, rows: 1 };
    const opening = review.opening || [];
    const pool = review.pool || [];
    const need = review.rest_shots || 0;
    const known = new Set([...opening, ...pool].map(keyOf));
    const tiles = new Map(); // key -> {li, pick, ban}

    const picks = () => fs().picks || [];
    const banned = () => new Set(fs().banned || []);
    const auto = () => !picks().length;
    const autoSet = () => [...(opening[0] ? [pickOf(opening[0], "opening")] : []),
      ...pool.filter((c) => !banned().has(keyOf(c))).slice(0, need).map((c) => pickOf(c))];
    const chosenKeys = () => (auto() ? autoSet() : picks()).map(keyOf);
    const save = (nextPicks, nextBanned) => {
      fs().picks = nextPicks;
      fs().banned = [...nextBanned];
      touched(["footage.picks", "footage.banned"]);
      refresh();
    };
    const toggle = (c, isOpening) => {
      const key = keyOf(c);
      let list = (auto() ? autoSet() : picks()).slice();
      const ban = banned();
      ban.delete(key);
      if (list.some((p) => keyOf(p) === key)) list = list.filter((p) => keyOf(p) !== key);
      else if (isOpening) list = [pickOf(c, "opening"), ...list.filter((p) => p.slot !== "opening")];
      else list.push(pickOf(c));
      save(list, ban);
    };
    const never = (c) => {
      const key = keyOf(c);
      const ban = banned();
      if (ban.has(key)) {
        ban.delete(key);
        save(picks(), ban);
        return;
      }
      ban.add(key);
      save((auto() ? autoSet() : picks()).filter((p) => keyOf(p) !== key), ban);
    };

    const tileFor = (c, isOpening) => {
      const col = c.tile % tile.cols;
      const row = Math.floor(c.tile / tile.cols);
      const x = tile.cols > 1 ? (col / (tile.cols - 1)) * 100 : 0;
      const y = tile.rows > 1 ? (row / (tile.rows - 1)) * 100 : 0;
      const name = clipTitle(c);
      const badge = h("span", { class: "rv-badge", "aria-hidden": "true" });
      // a wide (landscape) clip keeps less than half its picture in a tall frame: it looks zoomed in
      const wide = c.width && c.height ? c.width > c.height : c.portrait === false;
      const pick = h("button", { type: "button", class: "rv-pick" },
        h("span", { class: "rv-img", style: `background-image:url("${sheet}");background-size:${tile.cols * 100}% ${tile.rows * 100}%;background-position:${x}% ${y}%` }),
        badge,
        wide ? h("span", { class: "rv-wide", title: "A wide clip: in a tall video most of it is cut off, so it looks zoomed in" }, "Wide · zoomed") : null);
      pick.addEventListener("click", () => toggle(c, isOpening));
      const ban = h("button", { type: "button", class: "rv-ban", title: "Never use this clip", html: `${icon("x", { size: 14 })}<span>Never</span>` });
      ban.addEventListener("click", () => never(c));
      const li = h("li", { class: "rv-tile" }, pick,
        h("div", { class: "rv-tools" },
          h("a", { class: "rv-link", href: c.page_url, target: "_blank", rel: "noopener", title: `Watch on ${SITES[c.provider] || c.provider}` },
            h("span", { html: icon("play", { size: 12 }) }), SITES[c.provider] || c.provider),
          ban),
        h("span", { class: "rv-name" }, name));
      tiles.set(keyOf(c), { li, pick, ban, badge, name, isOpening, wide });
      return li;
    };

    const counter = h("p", { class: "muted small", "aria-live": "polite" });
    const stale = h("div", {});
    const sections = [];
    if (opening.length) {
      sections.push(h("div", { class: "stack" },
        h("p", { class: "subsection-title" }, "Opening shot"),
        h("p", { class: "muted small" }, "The first line of the script runs along its skyline. Pick one, or leave it to Claude."),
        h("ul", { class: "rv-grid", "aria-label": "Clips for the opening shot" }, opening.map((c) => tileFor(c, true)))));
    }
    if (pool.length) {
      sections.push(h("div", { class: "stack" },
        h("div", { class: "row row-wrap rv-pool-head" },
          h("p", { class: "subsection-title" }, opening.length ? "The other shots" : "Shots"),
          h("div", { class: "row" },
            button("Use Claude's picks", { size: "sm", variant: "ghost", onClick: () => save(autoSet(), banned()) }),
            button("Start over", { size: "sm", variant: "ghost", onClick: () => save([], []) }))),
        counter,
        h("ul", { class: "rv-grid", "aria-label": "Clips for the other shots" }, pool.map((c) => tileFor(c, false)))));
    }
    const warnings = (review.warnings || []).map((w) => callout("warn", w));
    body.replaceChildren(...sections, stale, ...warnings,
      h("p", { class: "muted small" }, "Clips you don't choose can still be used when shots are left over; clips marked Never are not. The number of shots is an estimate until the voiceover is made."));

    function refresh() {
      const chosen = chosenKeys();
      const ban = banned();
      const isAuto = auto();
      const openingKeys = new Set(opening.map(keyOf));
      const poolOrder = chosen.filter((k) => !openingKeys.has(k)); // the order the render uses them in
      for (const [key, t] of tiles) {
        const on = chosen.includes(key);
        const order = t.isOpening ? "1st" : on ? String(poolOrder.indexOf(key) + 1) : "";
        t.li.classList.toggle("is-chosen", on && !isAuto);
        t.li.classList.toggle("is-auto", on && isAuto);
        t.li.classList.toggle("is-banned", ban.has(key));
        t.badge.textContent = on ? (t.isOpening ? "✓" : order) : ban.has(key) ? "✕" : "";
        t.pick.setAttribute("aria-pressed", String(on && !isAuto));
        t.pick.setAttribute("aria-label", `${t.name}${t.wide ? ", wide clip, looks zoomed in" : ""}${on ? (isAuto ? ", Claude's pick" : t.isOpening ? ", chosen for the opening" : `, chosen as clip ${order}`) : ""}${ban.has(key) ? ", never used" : ""}`);
        t.ban.setAttribute("aria-pressed", String(ban.has(key)));
      }
      const chosenPool = chosen.filter((k) => pool.some((c) => keyOf(c) === k)).length;
      counter.textContent = isAuto
        ? `Claude would use the ${Math.min(need, pool.length)} marked clips for about ${need} shots. Tap clips to choose your own.`
        : `${chosenPool} chosen for about ${need} shots.${chosenPool > need ? ` Only the first ${need} are used.` : chosenPool < need ? " The other shots get clips from the search." : ""}`;
      const old = picks().filter((p) => !known.has(keyOf(p)));
      stale.replaceChildren(old.length
        ? callout("info", h("span", {}, `${old.length} clip${old.length === 1 ? "" : "s"} you chose earlier ${old.length === 1 ? "isn't" : "aren't"} in this list and still ${old.length === 1 ? "comes" : "come"} first. `,
            button("Remove them", { size: "sm", variant: "ghost", onClick: () => save(picks().filter((p) => known.has(keyOf(p))), banned()) })))
        : "");
    }
    refresh();
  }

  paint();
  return {
    el,
    cleanup() {
      unsubReview();
      if (unsubTask) unsubTask();
    },
  };
}
