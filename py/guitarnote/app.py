"""The app's requests, answered without any network code.

``handle()`` takes a method, a path, query parameters and a body, and returns a
``Response``. Two things call it:

* ``server.py``, the local web server, for ``python3 -m guitarnote``;
* ``static/browser.js``, which runs this same Python inside the browser with
  Pyodide when the page is hosted as a static site (GitHub Pages).

Keeping the routing here, rather than in either caller, means the hosted page
and the local one cannot drift apart. This module must not import anything
the browser lacks (no sockets, no ``http.server``).

API
---
GET   /api/ping                           which backend is answering
GET   /api/pedals                         pedals with note counts and labels
GET   /api/notes?pedal=P                  notes for a pedal (all pedals if omitted)
POST  /api/notes?pedal=P&gain=G&label=L   body: a WAV file; measures and stores it
GET   /api/notes/ID                       one note, with its working and chart data
PATCH /api/notes/ID                       JSON body: pedal, gain, label, comment, included
GET   /api/notes/ID/audio.wav             the take exactly as it was stored
GET   /api/notes/ID/window.csv            the analysed samples
GET   /api/analysis?pedal=P&label=L       crest factor against gain, with working
GET   /api/export/notes.csv?pedal=P       every note's numbers
GET   /api/export/summary.csv?pedal=P&label=L   the per-setting summary
GET   /api/export/notes.db                Save results: the whole database, audio included
POST  /api/import                         Load results: body is a saved .db; merged in
POST  /api/clear                          Clear results (archived first by the server)
POST  /api/demo                           add the synthetic demo notes, once
GET   /api/explainers                     the crest-factor picture and the demo recipe
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import sqlite3
import statistics
import tempfile
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path

from . import __version__, explain, stats
from .demo import DEMO_PEDAL, add_demo_notes
from .measure import NoteError, chart_data, describe_crest, measure
from .store import EXPORT_COLUMNS, Store
from .wav import WavError, read_wav


class HTTPError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str
    filename: str | None = None     #: set for downloads


def _finite(value):
    """JSON has no NaN or infinity; send null instead."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


def _json(payload, status: HTTPStatus = HTTPStatus.OK) -> Response:
    body = json.dumps(_finite(payload), allow_nan=False).encode("utf-8")
    return Response(int(status), body, "application/json")


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower() or "notes"


def _csv(header: list[str], rows) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def _pedal(params: dict) -> str:
    pedal = params.get("pedal", "").strip()
    if not pedal:
        raise HTTPError(HTTPStatus.BAD_REQUEST, "Choose a pedal.")
    return pedal


class App:
    """Everything the routes do, kept apart from any transport."""

    def __init__(self, store: Store, backend: str = "server"):
        self.store = store
        self.backend = backend          #: "server" or "browser", reported by /api/ping
        self._demo_lock = threading.Lock()
        self._explainers: dict | None = None

    # -- requests ---------------------------------------------------------
    def handle(self, method: str, path: str, params: dict, body: bytes = b"") -> Response:
        """Answer one request; errors come back as JSON with a reason."""
        try:
            return self._route(method, path, params, body)
        except HTTPError as error:
            return _json({"error": str(error)}, error.status)
        except (NoteError, WavError, ValueError) as error:
            return _json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def _route(self, method: str, path: str, params: dict, body: bytes) -> Response:
        label = params.get("label")             # absent means every label
        if method == "GET" and path == "/api/ping":
            return _json({"ok": True, "backend": self.backend, "version": __version__})
        if method == "GET" and path == "/api/explainers":
            return _json(self.explainers())
        if method == "POST" and path == "/api/import":
            return _json(self.import_results(body), HTTPStatus.CREATED)
        if method == "POST" and path == "/api/clear":
            return _json(self.clear_results())
        if method == "POST" and path == "/api/demo":
            return _json(self.load_demo(), HTTPStatus.CREATED)
        if method == "GET" and path == "/api/pedals":
            return _json(self.store.pedals())
        if path == "/api/notes":
            if method == "GET":
                return _json(self.store.notes(pedal=params.get("pedal")))
            if method == "POST":
                return _json(self.add_note(params, body), HTTPStatus.CREATED)
        match = re.fullmatch(r"/api/notes/(\d+)(/audio\.wav|/window\.csv)?", path)
        if match:
            note_id, part = int(match.group(1)), match.group(2)
            if method == "GET" and part is None:
                return _json(self.note_detail(note_id))
            if method == "PATCH" and part is None:
                note = self.store.update(note_id, json.loads(body or b"{}"))
                if note is None:
                    raise HTTPError(HTTPStatus.NOT_FOUND, f"There is no note {note_id}.")
                return _json(note)
            if method == "GET" and part == "/audio.wav":
                wav = self.store.audio(note_id)
                if wav is None:
                    raise HTTPError(HTTPStatus.NOT_FOUND, f"There is no note {note_id}.")
                return Response(200, wav, "audio/wav", f"note-{note_id}.wav")
            if method == "GET" and part == "/window.csv":
                return Response(200, self.window_csv(note_id), "text/csv",
                                f"note-{note_id}-window.csv")
        if method == "GET" and path == "/api/analysis":
            return _json(self.analysis(_pedal(params), label))
        if method == "GET" and path == "/api/export/notes.csv":
            pedal = params.get("pedal") or None
            name = f"{_slug(pedal)}-notes.csv" if pedal else "all-notes.csv"
            return Response(200, self.notes_csv(pedal), "text/csv", name)
        if method == "GET" and path == "/api/export/summary.csv":
            pedal = _pedal(params)
            return Response(200, self.summary_csv(pedal, label), "text/csv",
                            f"{_slug(pedal)}-summary.csv")
        if method == "GET" and path == "/api/export/notes.db":
            with tempfile.TemporaryDirectory() as folder:
                copy = Path(folder) / "notes.db"
                self.store.backup(copy)
                return Response(200, copy.read_bytes(), "application/vnd.sqlite3",
                                f"guitarnote-results-{datetime.now():%Y-%m-%d}.db")
        raise HTTPError(HTTPStatus.NOT_FOUND, f"No such page: {method} {path}")

    # -- the work ---------------------------------------------------------
    def explainers(self) -> dict:
        """The background explanations; worked out once, since they never change."""
        if self._explainers is None:
            self._explainers = {"crest": explain.crest_picture(), "fourier": explain.demo_recipe()}
        return self._explainers

    def import_results(self, body: bytes) -> dict:
        """Load results saved earlier with Save results, adding them to what is here.

        Merging rather than replacing means nothing already recorded is lost, and
        a note that is already here (same take, same time) is skipped, so loading
        the same file twice changes nothing. Every loaded note is measured again
        from its audio, so its numbers follow the current recipe whatever version
        saved the file.
        """
        if not body.startswith(b"SQLite format 3\x00"):
            raise HTTPError(HTTPStatus.BAD_REQUEST, "That is not a results file. Choose a .db "
                                                    "file saved with Save results.")
        with tempfile.TemporaryDirectory() as folder:
            incoming = Path(folder) / "incoming.db"
            incoming.write_bytes(body)
            try:
                with closing(sqlite3.connect(incoming)) as db:
                    db.row_factory = sqlite3.Row
                    rows = db.execute("""SELECT notes.*, audio.wav FROM notes
                                         JOIN audio ON audio.note_id = notes.id
                                         ORDER BY notes.id""").fetchall()
            except sqlite3.DatabaseError:
                raise HTTPError(HTTPStatus.BAD_REQUEST, "That database was not saved by this "
                                                        "app, so it has no notes to load.")
        added, skipped, unreadable, pedals = 0, 0, 0, []
        for row in rows:
            wav = bytes(row["wav"])
            if self.store.has_note(row["created_at"], wav):
                skipped += 1
                continue
            try:
                audio = read_wav(wav)
                m, _ = measure(audio.samples, audio.sample_rate)
            except (WavError, NoteError):
                unreadable += 1
                continue
            self.store.add(pedal=row["pedal"], gain=row["gain"], label=row["label"],
                           comment=row["comment"], source=row["source"], wav=wav, m=m,
                           created_at=row["created_at"], included=bool(row["included"]))
            added += 1
            if row["pedal"] not in pedals:
                pedals.append(row["pedal"])
        return {"added": added, "skipped": skipped, "unreadable": unreadable,
                "pedals": pedals}

    def clear_results(self) -> dict:
        """Remove every note. The local server keeps a dated copy first."""
        archived = None
        if self.backend == "server" and self.store.count():
            folder = self.store.path.parent / "archive"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"notes-{datetime.now():%Y-%m-%d-%H%M%S}.db"
            self.store.backup(target)
            archived = str(target)
        return {"cleared": self.store.clear(), "archived": archived}

    def load_demo(self) -> dict:
        """Add the synthetic demo notes, unless they are already there.

        Loading them twice would duplicate every note, and notes are never
        deleted, so a second request only reports what is loaded. The lock
        stops two quick clicks both finding the database empty.
        """
        with self._demo_lock:
            existing = len(self.store.notes(pedal=DEMO_PEDAL))
            if existing:
                return {"pedal": DEMO_PEDAL, "added": 0, "notes": existing}
            add_demo_notes(self.store)
            loaded = len(self.store.notes(pedal=DEMO_PEDAL))
            return {"pedal": DEMO_PEDAL, "added": loaded, "notes": loaded}

    def add_note(self, params: dict, body: bytes) -> dict:
        pedal = params.get("pedal", "").strip()
        if not pedal:
            raise HTTPError(HTTPStatus.BAD_REQUEST, "Give the pedal a name.")
        try:
            gain = float(params.get("gain", ""))
        except ValueError:
            raise HTTPError(HTTPStatus.BAD_REQUEST, "The gain setting must be a number.")
        audio = read_wav(body)
        m, _ = measure(audio.samples, audio.sample_rate)
        source = params.get("source", "upload").strip() or "upload"
        if audio.channels > 1:
            m.warnings.append(f"The file has {audio.channels} channels; only the first was "
                              "measured.")
        note_id = self.store.add(pedal=pedal, gain=gain, label=params.get("label", "").strip(),
                                 comment=params.get("comment", "").strip(),
                                 source=f"{source} ({audio.encoding})", wav=body, m=m)
        return self.note_detail(note_id)

    def note_detail(self, note_id: int) -> dict:
        note = self.store.note(note_id)
        if note is None:
            raise HTTPError(HTTPStatus.NOT_FOUND, f"There is no note {note_id}.")
        audio = read_wav(self.store.audio(note_id))
        # Re-measured from the stored audio, so the working shown is always
        # the calculation that produced the stored numbers.
        m, steps = measure(audio.samples, audio.sample_rate)
        return {"note": note, "steps": steps, "chart": chart_data(audio.samples, m),
                "measurement": m.as_dict(), "reading": describe_crest(m.crest_db),
                "baseline": self.baseline(note, m.crest_db)}

    def baseline(self, note: dict, crest_db: float) -> dict | None:
        """This note against the same pedal and label at its lowest gain setting.

        A crest factor means most as a change from the clean end of the knob,
        since how spiky a note starts out depends on the guitar, the string and
        the plucking.
        """
        same = self.store.notes(pedal=note["pedal"], label=note["label"], included_only=True)
        if not same:
            return None
        lowest = min(n["gain"] for n in same)
        values = [n["crest_db"] for n in same if n["gain"] == lowest]
        mean = statistics.fmean(values)
        where = f"{note['pedal']}" + (f", {note['label']}" if note["label"] else "")
        if note["gain"] == lowest:
            text = (f"This note is at the lowest gain setting recorded for {where} "
                    f"(setting {lowest:g}), which is the baseline the other settings are read "
                    f"against. The {len(values)} note{'s' if len(values) != 1 else ''} there "
                    f"average {mean:.2f} dB.")
        else:
            difference = crest_db - mean
            direction = "below" if difference < 0 else "above"
            meaning = ("That drop is the peak-flattening the pedal adds between the two "
                       "settings." if difference < 0 else
                       "A clipper should lower the crest factor, so this is unexpected: "
                       "uneven plucking can do it at low settings, or the gain may have "
                       "been entered wrongly.")
            text = (f"{abs(difference):.2f} dB {direction} the average of the {len(values)} "
                    f"note{'s' if len(values) != 1 else ''} at setting {lowest:g} "
                    f"({mean:.2f} dB), the lowest gain recorded for {where}. {meaning}")
        return {"gain": lowest, "n": len(values), "mean_db": mean,
                "difference_db": crest_db - mean, "text": text}

    def window_csv(self, note_id: int) -> bytes:
        note = self.store.note(note_id)
        if note is None:
            raise HTTPError(HTTPStatus.NOT_FOUND, f"There is no note {note_id}.")
        audio = read_wav(self.store.audio(note_id))
        sr = audio.sample_rate
        start = int(round(note["window_start_s"] * sr))
        end = int(round(note["window_end_s"] * sr))
        return _csv(["time_s", "sample"],
                    ((f"{i / sr:.6f}", f"{audio.samples[i]:.9f}") for i in range(start, end)))

    def analysis(self, pedal: str, label: str | None) -> dict:
        notes = self.store.notes(pedal=pedal, label=label, included_only=True)
        result = stats.analyse([n["gain"] for n in notes], [n["crest_db"] for n in notes])
        labels = sorted({n["label"] for n in notes})
        if label is None and len(labels) > 1:
            result["warnings"].insert(0, "These notes have different labels ("
                                      + ", ".join(l or "no label" for l in labels)
                                      + "). Crest factor differs from note to note, so compare "
                                      "one label at a time for a clean result.")
        excluded = len(self.store.notes(pedal=pedal, label=label)) - len(notes)
        result.update(pedal=pedal, label=label, excluded=excluded,
                      points=[{"id": n["id"], "gain": n["gain"], "crest_db": n["crest_db"],
                               "label": n["label"]} for n in notes])
        return result

    def notes_csv(self, pedal: str | None) -> bytes:
        notes = self.store.notes(pedal=pedal)
        return _csv(list(EXPORT_COLUMNS),
                    ([("; ".join(n[c]) if c == "warnings" else n[c]) for c in EXPORT_COLUMNS]
                     for n in notes))

    def summary_csv(self, pedal: str, label: str | None) -> bytes:
        result = self.analysis(pedal, label)
        rows = [[g["gain"], g["n"], g["mean"], g["sd"], g["se"]] for g in result["groups"]]
        return _csv(["gain", "n", "mean_crest_db", "sd_db", "se_db"],
                    ([("" if isinstance(v, float) and math.isnan(v) else v) for v in row]
                     for row in rows))
