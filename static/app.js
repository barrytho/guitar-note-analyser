// The page: records or uploads notes, shows each note's working, and the analysis.
"use strict";

const $ = selector => document.querySelector(selector);
const RED = "#c0392b", GREEN = "#2c7a3f", BLUE = "#1f5fa8", WAVE = "#3b6ea8";

const state = {notes: [], selected: null, detail: null, analysis: null, recorder: null};

// ---------------------------------------------------------------- helpers
function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

const fixed = (value, digits) => value == null ? "–" : Number(value).toFixed(digits).replace("-", "−");
const sig = (value, digits) => value == null ? "–" : Number(value).toPrecision(digits || 4);
const when = iso => new Date(iso).toLocaleString("en-GB", {dateStyle: "short", timeStyle: "short"});
const slug = text => (text || "notes").replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "").toLowerCase();
const labelParam = () => $("#label-filter").value === "__all__" ? null : $("#label-filter").value;

function remember(key, value) { try { localStorage.setItem("guitarnote." + key, value); } catch (e) { /* private window */ } }
function recall(key) { try { return localStorage.getItem("guitarnote." + key); } catch (e) { return null; } }

function setStatus(message, isError) {
  $("#status").textContent = message || "";
  $("#status").classList.toggle("error", Boolean(isError));
}

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    // The page is re-read from disk on every load but the server's Python is not,
    // so a server started before an update knows nothing of newer requests.
    if (response.status === 404 && /^No such page/.test(body.error || "")) {
      throw new Error("The running server is older than this page. Stop it with Ctrl+C in its "
                      + "terminal, start it again with python3 -m guitarnote, then reload.");
    }
    throw new Error(body.error || `The server answered ${response.status}.`);
  }
  return body;
}

function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== null && value !== undefined) search.set(key, value);
  return search.toString();
}

function renderSteps(list, steps) {
  list.replaceChildren(...steps.map(step => h("li", {},
    h("span", {class: "t"}, step.title),
    h("span", {class: "f"}, step.formula),
    h("span", {class: "w"}, step.working),
    h("span", {class: "r"}, step.result),
    step.explain ? h("p", {class: "x"}, step.explain) : null)));
}

// Where this crest factor sits between a square wave (0 dB) and a clean note,
// with the comparison against the pedal's lowest setting. The words come from
// Python; this only places the markers.
function renderReading(detail) {
  const crest = detail.measurement.crest_db, reading = detail.reading;
  const top = Math.max(10, Math.ceil(crest + 1));
  const at = db => `${Math.max(0, Math.min(100, db / top * 100))}%`;
  const scale = h("div", {class: "scale", role: "img",
                          "aria-label": `Crest factor scale from 0 to ${top} dB, this note at ${fixed(crest, 2)} dB`},
    h("div", {class: "bar"}),
    // Labels at either end are aligned inwards so they stay inside the scale.
    ...reading.references.map(r => h("div", {class: "ref" + (r.db / top < 0.15 ? " start" : r.db / top > 0.85 ? " end" : ""),
                                              style: `left:${at(r.db)}`},
      h("span", {class: "tick"}), h("span", {class: "ref-label"}, `${fixed(r.db, r.db % 1 ? 2 : 0)} dB`))),
    h("div", {class: "this", style: `left:${at(crest)}`}, h("span", {}, `this note ${fixed(crest, 2)} dB`)));
  $("#note-reading").replaceChildren(
    h("p", {class: "reading-text"}, h("strong", {}, `Reads as ${reading.label}. `), reading.text),
    scale,
    h("p", {class: "scale-key"}, reading.references.map(r => `${fixed(r.db, r.db % 1 ? 2 : 0)} dB: ${r.label}`).join(" \u00b7 ")),
    detail.baseline ? h("p", {class: "reading-text"}, h("strong", {}, "Against the lowest setting: "),
                        detail.baseline.text) : null);
}

function renderWarnings(box, warnings) {
  box.replaceChildren(...(warnings || []).map(w => h("div", {class: "warning"}, w)));
}

function chartButtons(chartId, filename) {
  const box = document.querySelector(`.downloads[data-chart="${chartId}"]`);
  const svg = () => document.querySelector(`#${chartId} svg`);
  box.replaceChildren(
    h("button", {type: "button", onclick: () => Charts.downloadSVG(svg(), filename)}, "Chart (SVG)"),
    h("button", {type: "button", onclick: () => Charts.downloadPNG(svg(), filename)}, "Chart (PNG)"));
}

// ---------------------------------------------------------------- lists
async function loadPedals() {
  const pedals = await api("api/pedals");
  $("#pedal-list").replaceChildren(...pedals.map(p => h("option", {value: p.pedal})));
  const current = pedals.find(p => p.pedal === $("#pedal").value.trim());
  const labels = current ? current.labels : [];
  $("#label-list").replaceChildren(...labels.filter(Boolean).map(l => h("option", {value: l})));
  const filter = $("#label-filter"), chosen = filter.value;
  filter.replaceChildren(h("option", {value: "__all__"}, "All labels"),
    ...labels.map(l => h("option", {value: l}, l || "(no label)")));
  filter.value = labels.includes(chosen) ? chosen : "__all__";
  return pedals;
}

async function refresh() {
  const pedal = $("#pedal").value.trim();
  await loadPedals();
  state.notes = pedal ? await api("api/notes?" + query({pedal})) : [];
  renderNotesTable();
  updateGainCount();
  $("#dl-notes").href = "api/export/notes.csv?" + query({pedal});
  if (pedal) {
    state.analysis = await api("api/analysis?" + query({pedal, label: labelParam()}));
    renderAnalysis(state.analysis);
  }
}

function updateGainCount() {
  const gain = Number($("#gain").value), label = $("#label").value.trim();
  const count = state.notes.filter(n => n.gain === gain && (!label || n.label === label)).length;
  $("#gain-count").textContent = `${count} note${count === 1 ? "" : "s"} recorded at this setting` +
    (label ? ` with this label` : "") + " (aim for about 5)";
}

function renderNotesTable() {
  const table = $("#notes-table");
  if (!state.notes.length) {
    table.replaceChildren(h("tbody", {}, h("tr", {}, h("td", {class: "text"}, "No notes for this pedal yet."))));
    return;
  }
  const head = h("thead", {}, h("tr", {},
    ["#", "Recorded", "Gain", "Label", "Crest factor (dB)", "Peak ÷ RMS", "Take peak (dBFS)", "Source", "Warnings", "Included"]
      .map((t, i) => h("th", {class: [1, 3, 7, 8].includes(i) ? "text" : ""}, t))));
  const body = h("tbody", {}, state.notes.map(n => {
    const box = h("input", {type: "checkbox", "aria-label": `Include note ${n.id}`,
      onclick: event => event.stopPropagation(),
      onchange: async event => {
        try { await api(`api/notes/${n.id}`, {method: "PATCH", body: JSON.stringify({included: event.target.checked})}); }
        catch (error) { setStatus(error.message, true); }
        await refresh();
        if (state.selected === n.id) await showNote(n.id);
      }});
    box.checked = n.included;
    return h("tr", {class: (n.included ? "" : "excluded ") + (state.selected === n.id ? "selected" : ""),
                    onclick: () => showNote(n.id)},
      h("td", {}, n.id), h("td", {class: "text"}, when(n.created_at)), h("td", {}, n.gain),
      h("td", {class: "text"}, n.label || "–"), h("td", {}, fixed(n.crest_db, 2)),
      h("td", {}, fixed(n.crest_ratio, 3)), h("td", {}, fixed(n.take_peak_dbfs, 1)),
      h("td", {class: "text"}, n.source), h("td", {class: "text"}, n.warnings.length ? `⚠ ${n.warnings.length}` : ""),
      h("td", {}, box));
  }));
  table.replaceChildren(head, body);
}

// ---------------------------------------------------------------- one note
async function showNote(id) {
  try {
    state.detail = await api(`api/notes/${id}`);
  } catch (error) {
    setStatus(error.message, true);
    return;
  }
  state.selected = id;
  renderNote(state.detail);
  renderNotesTable();
}

function renderNote(detail) {
  const note = detail.note, m = detail.measurement, chart = detail.chart;
  $("#note-empty").hidden = true;
  $("#note-body").hidden = false;
  $("#note-title").textContent = `Note ${note.id}`;
  $("#note-crest").textContent = fixed(m.crest_db, 2);
  $("#note-facts").replaceChildren(
    h("div", {}, `peak ${sig(m.peak)} ÷ RMS ${sig(m.rms)} = ${fixed(m.crest_ratio, 4)}`),
    h("div", {}, `${note.pedal} · gain ${note.gain}` + (note.label ? ` · ${note.label}` : "") +
                 ` · ${when(note.created_at)}` + (note.included ? "" : " · excluded from the analysis")));
  renderReading(detail);
  renderWarnings($("#note-warnings"), note.warnings);

  const duration = m.n_samples / m.sample_rate;
  const reach = Math.max(1e-6, ...chart.overview.map(p => Math.max(-p[1], p[2]))) * 1.35;
  Charts.plot($("#chart-overview"), {
    title: "Whole recording", height: 220,
    x: {min: 0, max: duration, label: "Time (s)"},
    y: {min: -reach, max: reach, label: "Amplitude", count: 4},
    layers: [
      {type: "vspan", x0: m.window_start_s, x1: m.window_end_s, fill: BLUE, opacity: 0.14},
      {type: "band", points: chart.overview, fill: WAVE},
      {type: "vline", x: m.onset_s, stroke: RED, dash: "4 3", label: "onset"},
    ],
    legend: [{type: "span", label: `analysed window ${fixed(m.window_start_s, 3)}–${fixed(m.window_end_s, 3)} s`, fill: BLUE, opacity: 0.25}],
  });

  const zoomEnd = chart.zoom.length ? chart.zoom[chart.zoom.length - 1][0] : 30;
  const zreach = m.peak * 1.45;
  Charts.plot($("#chart-zoom"), {
    title: "Close-up around the peak", height: 260,
    x: {min: 0, max: zoomEnd, label: `Time (ms) from ${fixed(chart.zoom_start_s, 4)} s`},
    y: {min: -zreach, max: zreach, label: "Amplitude", count: 4},
    layers: [
      {type: "hline", y: m.peak, stroke: RED, dash: "5 4", label: `+peak ${sig(m.peak)}`},
      {type: "hline", y: -m.peak, stroke: RED, dash: "5 4", label: `−peak`, below: true},
      {type: "hline", y: m.rms, stroke: GREEN, width: 1.6, label: `+RMS ${sig(m.rms)}`, below: true},
      {type: "hline", y: -m.rms, stroke: GREEN, width: 1.6, label: `−RMS`},
      {type: "line", points: chart.zoom, stroke: WAVE, width: 1.3},
    ],
    legend: [{label: "waveform", stroke: WAVE}, {label: "±peak", stroke: RED, dash: "5 4"},
             {label: "±RMS", stroke: GREEN, width: 1.6}],
  });
  const stem = `note-${note.id}`;
  chartButtons("chart-overview", `${stem}-recording`);
  chartButtons("chart-zoom", `${stem}-peak-and-rms`);

  renderSteps($("#note-steps"), detail.steps);
  $("#dl-audio").href = `api/notes/${note.id}/audio.wav`;
  $("#dl-window").href = `api/notes/${note.id}/window.csv`;

  const form = $("#edit-form");
  form.pedal.value = note.pedal;
  form.gain.value = note.gain;
  form.label.value = note.label;
  form.comment.value = note.comment;
  form.included.checked = note.included;
}

$("#edit-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.target, id = state.selected;
  const changes = {pedal: form.pedal.value.trim(), gain: Number(form.gain.value),
                   label: form.label.value.trim(), comment: form.comment.value.trim(),
                   included: form.included.checked};
  try {
    await api(`api/notes/${id}`, {method: "PATCH", body: JSON.stringify(changes)});
    setStatus(`Note ${id} updated.`);
  } catch (error) {
    setStatus(error.message, true);
    return;
  }
  await refresh();
  await showNote(id);
});

// ---------------------------------------------------------------- analysis
function renderAnalysis(result) {
  $("#verdict").textContent = result.verdict;
  const notes = result.excluded ? ` ${result.excluded} excluded note${result.excluded === 1 ? " is" : "s are"} left out.` : "";
  renderWarnings($("#analysis-warnings"), result.warnings.concat(notes ? [notes.trim()] : []));
  const pedal = result.pedal, label = result.label;
  $("#dl-summary").href = "api/export/summary.csv?" + query({pedal, label});

  const points = result.points;
  if (!points.length) {
    $("#chart-gain").replaceChildren(h("p", {class: "hint"}, "No included notes yet."));
    $("#summary-table").replaceChildren();
    $("#analysis-steps").replaceChildren();
    $("#rank-table").replaceChildren();
    document.querySelector('.downloads[data-chart="chart-gain"]').replaceChildren();
    return;
  }
  const gains = result.groups.map(g => g.gain);
  const crest = points.map(p => p.crest_db);
  const lo = Math.max(0, Math.min(...crest) - 0.6), hi = Math.max(...crest) + 0.6;
  const integerSettings = gains.every(g => Number.isInteger(g));
  // Spread notes at the same setting sideways a little so they do not hide each other.
  const seen = {};
  const spread = points.map(p => {
    const k = (seen[p.gain] = (seen[p.gain] || 0) + 1) - 1;
    const n = points.filter(q => q.gain === p.gain).length;
    return [p.gain + (n > 1 ? (k / (n - 1) - 0.5) * 0.24 : 0), p.crest_db];
  });
  const means = result.groups.map(g => [g.gain, g.mean]);
  Charts.plot($("#chart-gain"), {
    title: "Crest factor against gain setting", height: 320,
    x: {min: Math.min(...gains) - 0.6, max: Math.max(...gains) + 0.6, label: "Gain setting",
        ticks: integerSettings && gains.length <= 21 ? Array.from({length: Math.max(...gains) - Math.min(...gains) + 1}, (_, i) => Math.min(...gains) + i) : undefined},
    y: {min: lo, max: hi, label: "Crest factor (dB)"},
    layers: [
      {type: "points", points: spread, r: 3.2, fill: WAVE, opacity: 0.45},
      {type: "line", points: means, stroke: "#1d1d1b", width: 1.2},
      {type: "errorbars", items: result.groups.map(g => [g.gain, g.mean, g.se == null ? null : g.mean - g.se, g.se == null ? null : g.mean + g.se]), stroke: "#1d1d1b", width: 1.4},
      {type: "points", points: means, r: 4.6, fill: "#1d1d1b"},
    ],
    legend: [{type: "point", label: "one note", fill: WAVE, opacity: 0.45, r: 3.2},
             {type: "point", label: "mean ± standard error", fill: "#1d1d1b", r: 4.6}],
  });
  chartButtons("chart-gain", `${slug(pedal)}${label ? "-" + slug(label) : ""}-crest-vs-gain`);

  $("#summary-table").replaceChildren(
    h("thead", {}, h("tr", {}, ["Gain", "Notes (n)", "Crest factors (dB)", "Sum", "Mean = sum ÷ n",
      "Standard deviation", "Standard error = SD ÷ √n"].map((t, i) => h("th", {class: i === 2 ? "text" : ""}, t)))),
    h("tbody", {}, result.groups.map(g => h("tr", {},
      h("td", {}, g.gain), h("td", {}, g.n),
      h("td", {class: "values"}, g.values.map(v => fixed(v, 2)).join(", ")),
      h("td", {}, fixed(g.sum, 2)), h("td", {}, `${fixed(g.sum, 2)} ÷ ${g.n} = ${fixed(g.mean, 2)}`),
      h("td", {}, fixed(g.sd, 3)),
      h("td", {}, g.se == null ? "– (needs 2 notes)" : `${fixed(g.sd, 3)} ÷ √${g.n} = ${fixed(g.se, 3)}`)))));

  renderSteps($("#analysis-steps"), result.steps);
  $("#summary-explain").textContent = result.summary_explain || "";
  $("#rank-table").replaceChildren(
    h("thead", {}, h("tr", {}, ["Gain", "Gain rank (a)", "Crest factor (dB)", "Crest rank (b)"].map(t => h("th", {}, t)))),
    h("tbody", {}, result.ranks.map(r => h("tr", {},
      h("td", {}, r.gain), h("td", {}, r.gain_rank), h("td", {}, fixed(r.crest_db, 3)), h("td", {}, r.crest_rank)))));
}

// ---------------------------------------------------------------- saving notes
async function submit(wav, source) {
  const pedal = $("#pedal").value.trim();
  const gain = $("#gain").value;
  if (!pedal) throw new Error("Give the pedal a name first.");
  if (gain === "" || Number.isNaN(Number(gain))) throw new Error("Enter the gain setting first.");
  const params = {pedal, gain, label: $("#label").value.trim(), source};
  const detail = await api("api/notes?" + query(params),
                           {method: "POST", headers: {"Content-Type": "audio/wav"}, body: wav});
  state.selected = detail.note.id;
  state.detail = detail;
  renderNote(detail);
  await refresh();
  return detail;
}

function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 4), view = new DataView(buffer);
  const tag = (offset, text) => { for (let i = 0; i < 4; i++) view.setUint8(offset + i, text.charCodeAt(i)); };
  tag(0, "RIFF"); view.setUint32(4, 36 + samples.length * 4, true); tag(8, "WAVE");
  tag(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 3, true);      // 3 = IEEE float
  view.setUint16(22, 1, true); view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 4, true); view.setUint16(32, 4, true); view.setUint16(34, 32, true);
  tag(36, "data"); view.setUint32(40, samples.length * 4, true);
  for (let i = 0; i < samples.length; i++) view.setFloat32(44 + i * 4, samples[i], true);
  return new Blob([buffer], {type: "audio/wav"});
}

$("#upload").addEventListener("change", async event => {
  const files = Array.from(event.target.files);
  event.target.value = "";
  for (const file of files) {
    setStatus(`Measuring ${file.name}…`);
    try {
      const detail = await submit(await file.arrayBuffer(), `upload: ${file.name}`);
      setStatus(`${file.name}: crest factor ${fixed(detail.measurement.crest_db, 2)} dB (note ${detail.note.id}).`);
    } catch (error) {
      setStatus(`${file.name}: ${error.message}`, true);
      return;
    }
  }
});

// ---------------------------------------------------------------- recording
async function listInputs() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "audioinput");
  const select = $("#device"), chosen = select.value || recall("device") || "";
  select.replaceChildren(h("option", {value: ""}, "Default input"),
    ...devices.filter(d => d.deviceId && d.deviceId !== "default")
              .map((d, i) => h("option", {value: d.deviceId}, d.label || `Input ${i + 1}`)));
  if ([...select.options].some(o => o.value === chosen)) select.value = chosen;
}

function meter(peak) {
  const db = peak > 0 ? 20 * Math.log10(peak) : -Infinity;
  const width = Math.max(0, Math.min(1, (db + 60) / 60)) * 100;
  $("#meter-fill").style.width = `${width}%`;
  $("#meter-fill").classList.toggle("hot", db > -6);
  return db;
}

async function startRecording() {
  if (!navigator.mediaDevices || !window.AudioWorkletNode) {
    throw new Error("This browser cannot record here. Open the page at http://127.0.0.1 in a current browser, or upload a WAV file.");
  }
  const audio = {echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: {ideal: 1}};
  if ($("#device").value) audio.deviceId = {exact: $("#device").value};
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio});
  } catch (error) {
    if (error.name === "NotAllowedError") throw new Error("Microphone access was refused. Allow it in the browser's site settings, or upload a WAV file instead.");
    if (error.name === "NotFoundError" || error.name === "OverconstrainedError") throw new Error("That input was not found. Check the interface is connected and choose it under Input.");
    throw error;
  }
  const track = stream.getAudioTracks()[0];
  const settings = track.getSettings();
  const processing = ["echoCancellation", "noiseSuppression", "autoGainControl"].filter(k => settings[k] === true);
  $("#processing").textContent = processing.length
    ? `Warning: the browser kept ${processing.join(", ")} switched on, which alters the waveform. Upload WAV files recorded in other software instead.`
    : `Recording from “${track.label || "default input"}” with the browser's audio processing off.`;
  listInputs();

  const context = new AudioContext();
  await context.audioWorklet.addModule("static/recorder-worklet.js");
  const source = context.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(context, "capture");
  const mute = context.createGain();
  mute.gain.value = 0;                          // keeps the graph running without playing the input back
  source.connect(node).connect(mute).connect(context.destination);

  const rec = {stream, context, node, chunks: [], peak: 0, shown: 0, started: performance.now(),
               seconds: Number($("#duration").value)};
  node.port.onmessage = event => {
    const block = event.data;
    rec.chunks.push(block);
    let blockPeak = 0;
    for (let i = 0; i < block.length; i++) blockPeak = Math.max(blockPeak, Math.abs(block[i]));
    rec.peak = Math.max(rec.peak, blockPeak);
    rec.shown = Math.max(blockPeak, rec.shown * 0.9);
    const db = meter(rec.shown);
    const left = Math.max(0, rec.seconds - (performance.now() - rec.started) / 1000);
    $("#meter-text").textContent = `Level ${Number.isFinite(db) ? db.toFixed(1) : "−∞"} dBFS · ` +
      `loudest so far ${rec.peak > 0 ? (20 * Math.log10(rec.peak)).toFixed(1) : "−∞"} dBFS · ${left.toFixed(1)} s left`;
  };
  rec.timer = setTimeout(() => stopRecording(), rec.seconds * 1000);
  state.recorder = rec;
  $("#record").textContent = "■ Stop";
  $("#record").classList.add("recording");
  setStatus("Recording – pluck the note now.");
}

async function stopRecording() {
  const rec = state.recorder;
  if (!rec) return;
  state.recorder = null;
  clearTimeout(rec.timer);
  rec.stream.getTracks().forEach(t => t.stop());
  const sampleRate = rec.context.sampleRate;
  await rec.context.close();
  meter(0);
  $("#record").textContent = "● Record";
  $("#record").classList.remove("recording");
  const length = rec.chunks.reduce((sum, c) => sum + c.length, 0);
  const samples = new Float32Array(length);
  let offset = 0;
  for (const chunk of rec.chunks) { samples.set(chunk, offset); offset += chunk.length; }
  setStatus("Measuring…");
  $("#record").disabled = true;
  try {
    const detail = await submit(encodeWav(samples, sampleRate), "microphone");
    setStatus(`Saved note ${detail.note.id}: crest factor ${fixed(detail.measurement.crest_db, 2)} dB.`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    $("#record").disabled = false;
  }
}

$("#record").addEventListener("click", async () => {
  if (state.recorder) return stopRecording();
  try {
    await startRecording();
  } catch (error) {
    setStatus(error.message, true);
  }
});

// ---------------------------------------------------------------- settings
function nudgeGain(delta) {
  const value = Math.max(0, (Number($("#gain").value) || 0) + delta);
  $("#gain").value = value;
  remember("gain", value);
  updateGainCount();
}
$("#gain-down").addEventListener("click", () => nudgeGain(-1));
$("#gain-up").addEventListener("click", () => nudgeGain(1));
$("#gain").addEventListener("input", () => { remember("gain", $("#gain").value); updateGainCount(); });
$("#label").addEventListener("input", () => { remember("label", $("#label").value); updateGainCount(); });
async function openPedal(name) {
  $("#pedal").value = name;
  remember("pedal", name);
  await refresh();
  // Show this pedal's latest note, not one left over from the previous pedal.
  if (state.notes.length) {
    await showNote(state.notes[state.notes.length - 1].id);
  } else {
    state.selected = null;
    $("#note-body").hidden = true;
    $("#note-empty").hidden = false;
    $("#note-title").textContent = "This note";
  }
}

$("#load-demo").addEventListener("click", async event => {
  const button = event.target;
  button.disabled = true;
  setStatus("Generating the demo notes\u2026");
  try {
    const result = await api("api/demo", {method: "POST"});
    await openPedal(result.pedal);
    setStatus(result.added
      ? `Loaded ${result.added} synthetic notes under \u201c${result.pedal}\u201d.`
      : `The demo notes are already loaded, so this shows \u201c${result.pedal}\u201d.`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#pedal").addEventListener("change", async () => {
  try {
    await openPedal($("#pedal").value.trim());
  } catch (error) {
    setStatus(error.message, true);
  }
});
$("#label-filter").addEventListener("change", () => refresh().catch(e => setStatus(e.message, true)));
$("#device").addEventListener("change", () => remember("device", $("#device").value));
$("#duration").addEventListener("change", () => remember("duration", $("#duration").value));

(async function start() {
  try {
    await GuitarnoteBackend.ready;        // a local server, or Python in this browser
  } catch (error) {
    setStatus(error.message, true);
    return;
  }
  for (const key of ["pedal", "gain", "label", "duration"]) {
    const value = recall(key);
    if (value !== null && value !== "") $("#" + key).value = value;
  }
  if (!recall("pedal")) {
    // First visit: open whichever pedal has the most recent notes, if any.
    const pedals = await api("api/pedals").catch(() => []);
    if (pedals.length) $("#pedal").value = pedals[0].pedal;
  }
  await listInputs().catch(() => {});
  try {
    await refresh();
    if (state.notes.length) await showNote(state.notes[state.notes.length - 1].id);
  } catch (error) {
    setStatus(error.message, true);
  }
})();
