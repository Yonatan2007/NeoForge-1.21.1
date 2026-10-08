/**
 * Client-side mirror of the script rules in reelforge/script.py,
 * captions.py and timing.py, used for instant feedback while typing
 * (word count, hook sentence, caption chunks, estimated length). The Render
 * tab's plan comes from the server and is authoritative; automatic key-word
 * detection only happens there.
 */

const MARK = /(\*\*|\*)([\s\S]+?)\1/g;
const TRAIL = String.raw`["'”’)\]]*$`;
const SENTENCE_END = new RegExp(String.raw`[.!?…]+` + TRAIL);
const CLAUSE_END = new RegExp(String.raw`([,;:]|—|–|-{2}|\.\.\.)` + TRAIL);
const DASHES = new Set(["—", "–", "-", "--"]);
const WEAK = new Set(("a an the of to in on at by for with from and or but so your my his her " +
  "their our its as than that this before after into").split(" "));

/** Same as script.normalize: lower-case, letters and digits only. */
export const normalize = (token) => token.toLowerCase().replace(/[^a-z0-9]/g, "");

function stripMarkup(raw) {
  const spans = [];
  let out = "";
  let pos = 0;
  for (const m of raw.matchAll(MARK)) {
    out += raw.slice(pos, m.index);
    spans.push([out.length, out.length + m[2].length, m[1] === "**" ? 2 : 1]);
    out += m[2];
    pos = m.index + m[0].length;
  }
  return { text: out + raw.slice(pos), spans };
}

/** Words with sentence/clause ends and markup emphasis (1 yellow, 2 red). */
export function parseScript(raw) {
  const { text, spans } = stripMarkup(String(raw || "").trim().replace(/\s+/g, " "));
  const words = [];
  let sentence = 0;
  for (const m of text.matchAll(/\S+/g)) {
    const tok = m[0];
    if (DASHES.has(tok) || !normalize(tok)) {
      if (words.length) words[words.length - 1].endsClause = true;
      continue;
    }
    if (words.length && words[words.length - 1].endsSentence) sentence++;
    const w = {
      text: tok, norm: normalize(tok), index: words.length, sentence,
      endsSentence: SENTENCE_END.test(tok), endsClause: CLAUSE_END.test(tok),
      emphasis: 0, group: null,
    };
    spans.forEach(([start, end, level], i) => {
      if (m.index < end && m.index + tok.length > start) {
        w.emphasis = level;
        w.group = -1 - i;
      }
    });
    words.push(w);
  }
  const sentences = words.length ? words[words.length - 1].sentence + 1 : 0;
  return { text, words, sentences };
}

/** Caption chunks, following captions._chunk (without voice timing). */
export function chunkWords(words, style) {
  const maxWords = Math.max(1, style.max_words || 1);
  const maxChars = style.max_chars || 18;
  if (maxWords === 1) return words.map((w) => [w]);
  const chunks = [];
  let cur = [];
  const groupLen = (k) => {
    let n = 0;
    while (k + n < words.length && words[k + n].group === words[k].group) n++;
    return n;
  };
  const leftInSentence = (k) => {
    let n = 0;
    while (k + n < words.length && words[k + n].sentence === words[k].sentence) n++;
    return n;
  };
  const hardCap = maxWords + 2;
  words.forEach((w, k) => {
    if (cur.length) {
      const prev = cur[cur.length - 1];
      const chars = cur.reduce((s, x) => s + x.text.length + 1, 0) + w.text.length;
      let full;
      if (w.group !== null && w.group === prev.group) {
        full = cur.length >= hardCap;
      } else {
        full = cur.length >= maxWords || chars > maxChars;
        if (w.group !== null && groupLen(k) > 1) full = full || cur.length + groupLen(k) > hardCap;
        else if (full && leftInSentence(k) === 1 && cur.length <= maxWords && chars <= maxChars) full = false;
      }
      if (full) {
        let carry = null;
        if (cur.length > 1 && prev.group === null &&
            ((WEAK.has(prev.norm) && !WEAK.has(w.norm)) || leftInSentence(k) === 1)) carry = cur.pop();
        chunks.push(cur);
        cur = carry ? [carry] : [];
      }
    }
    cur.push(w);
    if (w.endsSentence || w.endsClause) {
      chunks.push(cur);
      cur = [];
    }
  });
  if (cur.length) chunks.push(cur);
  return chunks;
}

/** Same as fonts.apply_case. */
export function applyCase(text, mode) {
  if (mode === "upper") return text.toUpperCase();
  if (mode === "lower") return text.toLowerCase();
  return text;
}

/** Which words form the hook (opening sentence(s)) for this project. */
export function splitHook(words, project) {
  const hs = project.style.hook;
  if (hs.mode === "center" || !(hs.sentences > 0)) return { hook: [], body: words };
  return {
    hook: words.filter((w) => w.sentence < hs.sentences),
    body: words.filter((w) => w.sentence >= hs.sentences),
  };
}

/**
 * Character ranges of the raw script text for the backdrop highlighter:
 * [{start, end, kind}] with kind "hook" | "em1" | "em2".
 */
export function scriptRanges(raw, hookSentences) {
  const ranges = [];
  for (const m of raw.matchAll(MARK)) {
    ranges.push({ start: m.index, end: m.index + m[0].length, kind: m[1] === "**" ? "em2" : "em1" });
  }
  const start = raw.search(/\S/);
  if (hookSentences > 0 && start >= 0) {
    let seen = 0;
    let end = raw.length; // a script without a full stop is one long hook sentence
    for (const m of raw.matchAll(/\S+/g)) {
      const tok = m[0].replace(/\*/g, "");
      if (normalize(tok) && SENTENCE_END.test(tok) && ++seen >= hookSentences) {
        end = m.index + m[0].length;
        break;
      }
    }
    ranges.push({ start, end, kind: "hook" });
  }
  return ranges;
}

// --------------------------------------------------------------------------- length

const DEFAULT_PACE = 2.6; // words per second of a typical narration

/** Seconds of speech, as pipeline._estimate_seconds does without a server. */
export function speechSeconds(project, parsed, voiceDuration) {
  const v = project.voice;
  if (v.source === "none") {
    const ends = parsed.words.filter((w) => w.endsSentence).length;
    return parsed.words.length / Math.max(v.words_per_second || DEFAULT_PACE, 0.5) + 0.6 * ends;
  }
  if (voiceDuration && v.file) return voiceDuration;
  return parsed.words.length / DEFAULT_PACE + 0.4 * parsed.sentences;
}

/**
 * The timing plan (timing.fit_duration): video length, where the voice sits
 * and whether the target can be met. All in seconds.
 */
export function lengthPlan(project, parsed, voiceDuration) {
  const vs = project.style.video;
  const ds = project.duration;
  const speech = speechSeconds(project, parsed, voiceDuration);
  const delay = vs.voice_delay ?? 0.1;
  const tail = vs.tail ?? 0.8;
  const natural = delay + speech + tail;
  const target = ds.target && ds.target > 0 ? Number(ds.target) : null;
  if (!target) return { speech, natural, total: natural, intro: delay, spoken: speech, tempo: 1, target, overBy: 0 };
  const outroMin = tail > 0 ? Math.min(tail, 0.5) : 0;
  const room = target - delay - outroMin;
  if (speech > room) {
    const tempo = Math.min(ds.max_tempo || 1.12, speech / Math.max(room, 1e-6));
    const spoken = speech / tempo;
    const total = Math.max(target, delay + spoken + outroMin);
    return { speech, natural, total, intro: delay, spoken, tempo, target, overBy: Math.max(0, total - target) };
  }
  const extra = target - natural;
  const intro = extra < 0 ? delay : delay + Math.min(extra * 0.4, Math.max(0, (ds.max_intro ?? 2) - delay));
  return { speech, natural, total: target, intro, spoken: speech, tempo: 1, target, overBy: 0 };
}
