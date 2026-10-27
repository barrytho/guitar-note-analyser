// Runs the page without a server, by running guitarnote's own Python inside
// the browser with Pyodide. This is how the hosted copy (GitHub Pages) works.
//
// When the page comes from `python3 -m guitarnote`, the server answers
// api/ping and nothing here is used. Otherwise Pyodide is loaded, the package's
// .py files are fetched from py/guitarnote/, and every request the page makes
// to api/... is answered by the same App.handle() the server calls, so the two
// cannot disagree. Notes are kept in this browser's IndexedDB and never leave it.
"use strict";

const GuitarnoteBackend = (() => {
  const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
  // Every module app.py needs, directly or not. tests/test_site.py checks this
  // list against the package, so a new module cannot be forgotten here.
  const MODULES = ["__init__", "wav", "measure", "stats", "store", "demo", "app"];
  const DB_DIR = "/data";
  const nativeFetch = window.fetch.bind(window);
  const base = new URL(".", document.baseURI);    // the folder index.html came from
  let py = null, handle = null;
  let queue = Promise.resolve();                  // Python runs one request at a time

  const say = text => { const s = document.getElementById("status"); if (s) { s.textContent = text; s.classList.remove("error"); } };

  async function serverAnswers() {
    try {
      const response = await nativeFetch(new URL("api/ping", base), {cache: "no-store"});
      return response.ok && (await response.json()).backend === "server";
    } catch (error) {
      return false;
    }
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.onload = resolve;
      script.onerror = () => reject(new Error("Python (Pyodide) could not be downloaded. Check the internet connection and reload."));
      document.head.appendChild(script);
    });
  }

  function syncDatabase(fromStorage) {
    return new Promise((resolve, reject) => py.FS.syncfs(fromStorage, error => error ? reject(error) : resolve()));
  }

  async function startPython() {
    say("Loading Python into this browser (about 10 MB the first time, then cached)…");
    await loadScript(PYODIDE + "pyodide.js");
    py = await loadPyodide({indexURL: PYODIDE});
    say("Loading the measurement code…");
    py.FS.mkdirTree("/home/pyodide/guitarnote");
    for (const name of MODULES) {
      const response = await nativeFetch(new URL(`py/guitarnote/${name}.py`, base));
      if (!response.ok) throw new Error(`The site is missing py/guitarnote/${name}.py.`);
      py.FS.writeFile(`/home/pyodide/guitarnote/${name}.py`, await response.text());
    }
    py.FS.mkdirTree(DB_DIR);
    py.FS.mount(py.FS.filesystems.IDBFS, {}, DB_DIR);
    await syncDatabase(true);                     // bring back notes from earlier visits
    py.runPython(`
import sys
from urllib.parse import parse_qs
sys.path.insert(0, "/home/pyodide")
from guitarnote.app import App
from guitarnote.store import Store

_app = App(Store("${DB_DIR}/notes.db"), backend="browser")

def _handle(method, path, query, body):
    params = {k: v[-1] for k, v in parse_qs(query, keep_blank_values=True).items()}
    # A JS Uint8Array arrives with to_bytes(); JavaScript null arrives as JsNull,
    # not None, in recent Pyodide, so test for the method rather than for None.
    data = body.to_bytes() if hasattr(body, "to_bytes") else b""
    response = _app.handle(method, path, params, data)
    return [response.status, response.content_type, response.filename or "", response.body]
`);
    handle = py.globals.get("_handle");
    say("");
  }

  async function answer(method, path, query, body) {
    const result = handle(method, path, query, body);
    try {
      const [status, type, filename, payload] = result.toJs();
      const bytes = payload instanceof Uint8Array ? payload : new Uint8Array(payload);
      if (method !== "GET" && status < 400) await syncDatabase(false);    // save to IndexedDB
      const headers = {"Content-Type": type};
      if (filename) headers["Content-Disposition"] = `attachment; filename="${filename}"`;
      return new Response(bytes, {status, headers});
    } finally {
      result.destroy();
    }
  }

  async function pythonFetch(input, init) {
    const url = new URL(input instanceof Request ? input.url : input, document.baseURI);
    const apiRoot = base.pathname + "api/";
    if (url.origin !== location.origin || !url.pathname.startsWith(apiRoot)) return nativeFetch(input, init);
    const method = ((init && init.method) || "GET").toUpperCase();
    const body = init && init.body != null ? new Uint8Array(await new Response(init.body).arrayBuffer()) : null;
    const path = "/" + url.pathname.slice(base.pathname.length);
    const run = queue.then(() => answer(method, path, url.search.slice(1), body));
    queue = run.catch(() => {});
    return run;
  }

  // Download links point at api/...; with no server behind them, fetch the
  // file from Python instead and hand it to the browser to save.
  async function downloadThroughPython(event) {
    const link = event.target.closest && event.target.closest("a[href]");
    if (!link || !link.getAttribute("href").startsWith("api/")) return;
    event.preventDefault();
    const response = await pythonFetch(link.getAttribute("href"));
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      say(body.error || "That download failed.");
      return;
    }
    const match = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "");
    const url = URL.createObjectURL(await response.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = match ? match[1] : "download";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const ready = (async () => {
    if (await serverAnswers()) return "server";
    await startPython();
    window.fetch = pythonFetch;
    document.addEventListener("click", downloadThroughPython, true);
    const note = document.getElementById("mode-note");
    if (note) note.hidden = false;
    return "browser";
  })();

  return {ready};
})();
