/**
 * The one place the UI talks to a backend.
 *
 * `api` is a plain object of async functions that mirror the server's HTTP
 * endpoints one to one. Another backend (for example one hosted inside a
 * Claude artifact) only has to provide the same functions: call
 * `useBackend(impl)` before the app starts and every module that imported
 * `api` sees the new implementation. A backend that lacks a feature should
 * throw an `ApiError` with status 404 or 501; the UI then hides that feature
 * (see `isUnsupported`).
 */

export class ApiError extends Error {
  /**
   * @param {string} message  human readable, shown in toasts
   * @param {number} status   HTTP status (0 = network failure)
   * @param {any} detail      parsed error body, if any
   */
  constructor(message, status = 0, detail = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** True when the backend does not offer this endpoint at all. */
export function isUnsupported(err) {
  return err instanceof ApiError && (err.status === 404 || err.status === 405 || err.status === 501);
}

/** FastAPI puts messages in `detail` (a string, or a list of validation errors). */
function messageFrom(body, status, statusText) {
  const detail = body && typeof body === "object" ? body.detail ?? body.error ?? body.message : body;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((d) => (d && d.msg ? `${(d.loc || []).slice(1).join(".")} ${d.msg}`.trim() : String(d))).join("; ");
  }
  if (status === 0) return "Cannot reach the reelforge server. Is it still running?";
  return `${status} ${statusText || "request failed"}`;
}

function parseBody(text) {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** Build the HTTP backend for a server at `base` ("" = same origin). */
export function createHttpApi(base = "") {
  const url = (path, query) => {
    const q = query
      ? "?" + new URLSearchParams(Object.entries(query).filter(([, v]) => v !== undefined && v !== null)).toString()
      : "";
    return `${base}${path}${q}`;
  };

  async function request(method, path, { json, query, keepalive = false, signal } = {}) {
    const init = { method, headers: { Accept: "application/json" }, keepalive, signal };
    if (json !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(json);
    }
    let res;
    try {
      res = await fetch(url(path, query), init);
    } catch (err) {
      if (err && err.name === "AbortError") throw err;
      throw new ApiError(messageFrom(null, 0), 0);
    }
    const body = parseBody(await res.text());
    if (!res.ok) throw new ApiError(messageFrom(body, res.status, res.statusText), res.status, body);
    return body;
  }

  const enc = encodeURIComponent;

  /** Multipart upload with progress (fetch cannot report upload progress). */
  function upload(projectId, file, role, { onProgress, signal } = {}) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", url(`/api/projects/${enc(projectId)}/upload`));
      xhr.responseType = "text";
      xhr.upload.onprogress = (e) => {
        if (onProgress && e.lengthComputable) onProgress(e.loaded / e.total);
      };
      xhr.onload = () => {
        const body = parseBody(xhr.responseText);
        if (xhr.status >= 200 && xhr.status < 300) resolve(body);
        else reject(new ApiError(messageFrom(body, xhr.status, xhr.statusText), xhr.status, body));
      };
      xhr.onerror = () => reject(new ApiError(messageFrom(null, 0), 0));
      xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
      if (signal) signal.addEventListener("abort", () => xhr.abort(), { once: true });
      const form = new FormData();
      form.append("file", file, file.name);
      form.append("role", role);
      xhr.send(form);
    });
  }

  return {
    name: "http",

    // Reference data
    meta: () => request("GET", "/api/meta"),
    schema: () => request("GET", "/api/schema"),
    getPreset: (name) => request("GET", `/api/presets/${enc(name)}`),

    // The user's saved defaults
    getDefaults: () => request("GET", "/api/defaults"),
    saveDefaults: (project) => request("PUT", "/api/defaults", { json: project }),
    resetDefaults: () => request("DELETE", "/api/defaults"),

    // Projects
    listProjects: () => request("GET", "/api/projects"),
    createProject: ({ name, preset } = {}) => request("POST", "/api/projects", { json: { name, preset } }),
    getProject: (id) => request("GET", `/api/projects/${enc(id)}`),
    saveProject: (id, project, { keepalive = false } = {}) =>
      request("PUT", `/api/projects/${enc(id)}`, { json: project, keepalive }),
    deleteProject: (id) => request("DELETE", `/api/projects/${enc(id)}`),
    duplicateProject: (id) => request("POST", `/api/projects/${enc(id)}/duplicate`),

    // Files
    upload,
    deleteUpload: (id, name) => request("DELETE", `/api/projects/${enc(id)}/uploads/${enc(name)}`),
    waveform: (id, file, buckets = 1000) =>
      request("GET", `/api/projects/${enc(id)}/waveform`, { query: { file, buckets } }),
    /** URL of a file inside the project folder (synchronous: it is only a link). */
    fileUrl: (id, path) => url(`/api/projects/${enc(id)}/files/${String(path).split("/").map(enc).join("/")}`),

    // Planning and rendering
    plan: (id) => request("POST", `/api/projects/${enc(id)}/plan`),
    render: (id) => request("POST", `/api/projects/${enc(id)}/render`),
    job: (jobId) => request("GET", `/api/jobs/${enc(jobId)}`),
    cancelJob: (jobId) => request("POST", `/api/jobs/${enc(jobId)}/cancel`),
    higgsfieldRequest: (id) => request("GET", `/api/projects/${enc(id)}/higgsfield-request`),
  };
}

/** The backend in use. Mutated in place by `useBackend` so imports stay valid. */
export const api = createHttpApi(globalThis.REELFORGE_API_BASE ?? "");

/** Swap in another backend (an object with the same functions). */
export function useBackend(impl) {
  for (const key of Object.keys(api)) delete api[key];
  Object.assign(api, impl);
}

export default api;
