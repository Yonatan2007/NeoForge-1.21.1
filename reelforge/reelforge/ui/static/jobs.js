/**
 * Render jobs: start, poll every second, cancel, and resume polling after a
 * page reload (job ids are remembered per project in localStorage).
 */

import { api, isUnsupported } from "./api.js";
import { toast } from "./dom.js";
import { emit, flush, markUnsupported, refreshFiles, state } from "./store.js";

const STORAGE_KEY = "reelforge.jobs";
const POLL_MS = 1000;
const ACTIVE = new Set(["queued", "running"]);

/** projectId -> latest job snapshot ({job_id, status, stage, progress, message, log, result, error}). */
export const jobs = {};
const timers = {};

function remembered() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function remember(projectId, jobId) {
  const all = remembered();
  if (jobId) all[projectId] = jobId;
  else delete all[projectId];
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch {
    /* private mode: polling still works for this page view */
  }
}

export const isActive = (job) => Boolean(job && ACTIVE.has(job.status));

function update(projectId, job) {
  jobs[projectId] = job;
  emit("job", { projectId, job });
}

async function poll(projectId, jobId) {
  clearTimeout(timers[projectId]);
  let job;
  try {
    job = await api.job(jobId);
  } catch (err) {
    if (err.status === 404) {
      remember(projectId, null); // the server restarted and forgot the job
      if (jobs[projectId] && jobs[projectId].job_id === jobId) update(projectId, null);
      return;
    }
    timers[projectId] = setTimeout(() => poll(projectId, jobId), POLL_MS * 3);
    return;
  }
  const previous = jobs[projectId];
  update(projectId, { ...job, job_id: jobId, startedAt: previous && previous.job_id === jobId ? previous.startedAt : Date.now() });
  if (isActive(job)) {
    timers[projectId] = setTimeout(() => poll(projectId, jobId), POLL_MS);
    return;
  }
  remember(projectId, null);
  if (job.status === "done") {
    if (projectId === state.projectId) await refreshFiles().catch(() => {});
    toast("Your video is ready.", { kind: "success" });
  } else if (job.status === "error") {
    toast(`Render failed: ${job.error || job.message || "unknown error"}`, { kind: "error" });
  }
}

/** Save, then start a render of the project. */
export async function startRender(projectId) {
  await flush();
  let res;
  try {
    res = await api.render(projectId);
  } catch (err) {
    if (isUnsupported(err)) markUnsupported("render");
    throw err;
  }
  const jobId = res.job_id || res.id;
  remember(projectId, jobId);
  update(projectId, { job_id: jobId, status: "queued", stage: null, progress: 0, message: "Starting…", log: [], startedAt: Date.now() });
  poll(projectId, jobId);
  return jobId;
}

export async function cancelRender(projectId) {
  const job = jobs[projectId];
  if (!job) return;
  await api.cancelJob(job.job_id);
  poll(projectId, job.job_id);
}

/** Pick up a render that was running when the page was (re)loaded. */
export function resumeJob(projectId) {
  const jobId = remembered()[projectId];
  if (jobId && !(jobs[projectId] && jobs[projectId].job_id === jobId)) poll(projectId, jobId);
}

/** Forget a finished job's panel (e.g. "dismiss" after an error). */
export function clearJob(projectId) {
  if (jobs[projectId] && !isActive(jobs[projectId])) update(projectId, null);
}
