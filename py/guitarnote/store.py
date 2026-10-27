"""The notes database: one SQLite file holding every take and its result.

The audio itself is stored alongside the numbers, so the database is the whole
record of an experiment: any note can be re-measured or re-exported later, and
copying the one file backs everything up.

A single note is never deleted: a bad take is *excluded*, which leaves it in
the record but out of the analysis, and can be undone. The only removal is
``clear()``, behind the page's Clear results button, which asks first and
offers to save a copy; the local server also archives the file before
clearing (see ``App.clear_results``).
"""
from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .measure import METHOD, Measurement

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id              INTEGER PRIMARY KEY,
    created_at      TEXT    NOT NULL,
    pedal           TEXT    NOT NULL,
    gain            REAL    NOT NULL,
    label           TEXT    NOT NULL DEFAULT '',
    comment         TEXT    NOT NULL DEFAULT '',
    included        INTEGER NOT NULL DEFAULT 1,
    source          TEXT    NOT NULL,
    method          TEXT    NOT NULL,
    sample_rate     INTEGER NOT NULL,
    n_samples       INTEGER NOT NULL,
    take_peak_dbfs  REAL    NOT NULL,
    onset_s         REAL    NOT NULL,
    window_start_s  REAL    NOT NULL,
    window_end_s    REAL    NOT NULL,
    peak            REAL    NOT NULL,
    mean_square     REAL    NOT NULL,
    rms             REAL    NOT NULL,
    crest_ratio     REAL    NOT NULL,
    crest_db        REAL    NOT NULL,
    warnings        TEXT    NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS audio (
    note_id  INTEGER PRIMARY KEY REFERENCES notes(id),
    wav      BLOB    NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_by_pedal ON notes (pedal, gain);
"""

#: Columns the page may change after a note is recorded.
EDITABLE = {"pedal": str, "gain": float, "label": str, "comment": str, "included": bool}

#: Column order for CSV export.
EXPORT_COLUMNS = ("id", "created_at", "pedal", "gain", "label", "included", "crest_db",
                  "crest_ratio", "peak", "rms", "mean_square", "take_peak_dbfs",
                  "onset_s", "window_start_s", "window_end_s", "sample_rate",
                  "n_samples", "source", "method", "comment", "warnings")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # A fresh connection per call: the web server answers requests on
        # several threads, and sqlite3 connections must not cross threads.
        # sqlite3's own `with connection:` commits or rolls back but never
        # closes, so on its own it leaked one connection per request.
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, *, pedal: str, gain: float, label: str, comment: str, source: str,
            wav: bytes, m: Measurement, created_at: str | None = None,
            included: bool = True) -> int:
        """Store one measured note. ``created_at`` and ``included`` are given when
        loading notes saved earlier, so they keep their history."""
        created_at = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as db:
            cursor = db.execute(
                """INSERT INTO notes (created_at, pedal, gain, label, comment, included, source,
                       method, sample_rate, n_samples, take_peak_dbfs, onset_s, window_start_s,
                       window_end_s, peak, mean_square, rms, crest_ratio, crest_db, warnings)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (created_at, pedal, gain, label, comment, int(included),
                 source, METHOD, m.sample_rate, m.n_samples,
                 m.take_peak_dbfs, m.onset_s, m.window_start_s, m.window_end_s, m.peak,
                 m.mean_square, m.rms, m.crest_ratio, m.crest_db, "\n".join(m.warnings)))
            note_id = cursor.lastrowid
            db.execute("INSERT INTO audio (note_id, wav) VALUES (?, ?)", (note_id, wav))
        return note_id

    def notes(self, pedal: str | None = None, label: str | None = None,
              included_only: bool = False) -> list[dict]:
        clauses, params = [], []
        if pedal is not None:
            clauses.append("pedal = ?")
            params.append(pedal)
        if label is not None:
            clauses.append("label = ?")
            params.append(label)
        if included_only:
            clauses.append("included = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM notes {where} ORDER BY gain, id", params)
            return [self._row(r) for r in rows]

    def note(self, note_id: int) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return self._row(row) if row else None

    def audio(self, note_id: int) -> bytes | None:
        with self._connect() as db:
            row = db.execute("SELECT wav FROM audio WHERE note_id = ?", (note_id,)).fetchone()
        return bytes(row["wav"]) if row else None

    def update(self, note_id: int, changes: dict) -> dict | None:
        unknown = set(changes) - set(EDITABLE)
        if unknown:
            raise ValueError(f"cannot change {', '.join(sorted(unknown))}")
        values = {key: EDITABLE[key](value) for key, value in changes.items()}
        if "pedal" in values and not values["pedal"].strip():
            raise ValueError("the pedal name cannot be empty")
        if values:
            assignments = ", ".join(f"{key} = ?" for key in values)
            with self._connect() as db:
                db.execute(f"UPDATE notes SET {assignments} WHERE id = ?",
                           [*values.values(), note_id])
        return self.note(note_id)

    def pedals(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT pedal, COUNT(*) AS notes, SUM(included) AS included,
                          MAX(created_at) AS latest
                   FROM notes GROUP BY pedal ORDER BY latest DESC""").fetchall()
            # Labels are free text and may contain commas, so they are not
            # packed into one column with GROUP_CONCAT.
            labels: dict[str, list[str]] = {}
            for r in db.execute("SELECT DISTINCT pedal, label FROM notes ORDER BY label"):
                labels.setdefault(r["pedal"], []).append(r["label"])
        return [{"pedal": r["pedal"], "notes": r["notes"], "included": r["included"],
                 "labels": labels.get(r["pedal"], []), "latest": r["latest"]} for r in rows]

    def backup(self, destination: str | Path) -> None:
        """A consistent copy of the database, safe to take while it is in use."""
        with self._connect() as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)

    def has_note(self, created_at: str, wav: bytes) -> bool:
        """Is this exact take already stored? Used so loading a file twice adds nothing."""
        with self._connect() as db:
            row = db.execute("""SELECT 1 FROM notes JOIN audio ON audio.note_id = notes.id
                                WHERE notes.created_at = ? AND audio.wav = ? LIMIT 1""",
                             (created_at, wav)).fetchone()
        return row is not None

    def count(self) -> int:
        with self._connect() as db:
            return db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]

    def clear(self) -> int:
        """Remove every note and its audio; return how many there were."""
        with self._connect() as db:
            removed = db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            db.execute("DELETE FROM audio")
            db.execute("DELETE FROM notes")
        # Give the space back: VACUUM cannot run inside the transaction above.
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("VACUUM")
        return removed

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        record = dict(row)
        record["included"] = bool(record["included"])
        record["warnings"] = [w for w in record["warnings"].split("\n") if w]
        return record
