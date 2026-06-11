"""SQLite workspace: schema, connection, CRUD.

Layout: $CUTROOM_HOME (default ~/.cutroom)/
    library.db
    media/<video_id>/{source.mp4, audio.wav, frames/, renders/}

All reads return domain types from cutroom.types. FTS5 (external content table)
indexes segment text for budgeted transcript search.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from cutroom.types import AudioEvent, Scene, Segment, Shot, VideoMeta, Word

_SCHEMA = """
CREATE TABLE IF NOT EXISTS videos(
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    duration REAL NOT NULL,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    fps REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS segments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    t0 REAL NOT NULL,
    t1 REAL NOT NULL,
    text TEXT NOT NULL,
    words_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_segments_video_time ON segments(video_id, t0);
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    text, content='segments', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS segments_ai AFTER INSERT ON segments BEGIN
    INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS segments_ad AFTER DELETE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS segments_au AFTER UPDATE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TABLE IF NOT EXISTS shots(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    t0 REAL NOT NULL,
    t1 REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shots_video_time ON shots(video_id, t0);
CREATE TABLE IF NOT EXISTS audio_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    t0 REAL NOT NULL,
    t1 REAL NOT NULL,
    value REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_audio_events_video_time ON audio_events(video_id, t0);
CREATE TABLE IF NOT EXISTS scenes(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    t0 REAL NOT NULL,
    t1 REAL NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_scenes_video_time ON scenes(video_id, t0);
"""


def video_id_for(source: str) -> str:
    """Stable short id for a URL or file path."""
    return hashlib.sha256(source.encode()).hexdigest()[:12]


class Workspace:
    """Owns the on-disk layout and the SQLite connection."""

    def __init__(self, home: str | Path | None = None):
        self.home = Path(home or os.environ.get("CUTROOM_HOME") or Path.home() / ".cutroom")
        self.home.mkdir(parents=True, exist_ok=True)
        self.db_path = self.home / "library.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # --- paths ---------------------------------------------------------

    def media_dir(self, video_id: str) -> Path:
        d = self.home / "media" / video_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def frames_dir(self, video_id: str) -> Path:
        d = self.media_dir(video_id) / "frames"
        d.mkdir(exist_ok=True)
        return d

    def renders_dir(self, video_id: str) -> Path:
        d = self.media_dir(video_id) / "renders"
        d.mkdir(exist_ok=True)
        return d

    def source_path(self, video_id: str) -> Path:
        return self.media_dir(video_id) / "source.mp4"

    def audio_path(self, video_id: str) -> Path:
        return self.media_dir(video_id) / "audio.wav"

    # --- writes --------------------------------------------------------

    def upsert_video(self, meta: VideoMeta) -> None:
        self.conn.execute(
            "INSERT INTO videos(id, source, title, duration, width, height, fps, created_at)"
            " VALUES(?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET source=excluded.source, title=excluded.title,"
            " duration=excluded.duration, width=excluded.width, height=excluded.height,"
            " fps=excluded.fps",
            (
                meta.id,
                meta.source,
                meta.title,
                meta.duration,
                meta.width,
                meta.height,
                meta.fps,
                meta.created_at,
            ),
        )
        self.conn.commit()

    def add_segments(self, segments: list[Segment]) -> list[int]:
        ids = []
        for s in segments:
            words_json = json.dumps([[w.text, w.t0, w.t1] for w in s.words])
            cur = self.conn.execute(
                "INSERT INTO segments(video_id, t0, t1, text, words_json) VALUES(?,?,?,?,?)",
                (s.video_id, s.t0, s.t1, s.text, words_json),
            )
            ids.append(cur.lastrowid)
        self.conn.commit()
        return ids

    def delete_segments(self, video_id: str) -> None:
        self.conn.execute("DELETE FROM segments WHERE video_id=?", (video_id,))
        self.conn.commit()

    def add_shots(self, shots: list[Shot]) -> None:
        self.conn.executemany(
            "INSERT INTO shots(video_id, t0, t1) VALUES(?,?,?)",
            [(s.video_id, s.t0, s.t1) for s in shots],
        )
        self.conn.commit()

    def replace_shots(self, video_id: str, shots: list[Shot]) -> None:
        self.conn.execute("DELETE FROM shots WHERE video_id=?", (video_id,))
        self.add_shots(shots)

    def add_audio_events(self, events: list[AudioEvent]) -> None:
        self.conn.executemany(
            "INSERT INTO audio_events(video_id, kind, t0, t1, value) VALUES(?,?,?,?,?)",
            [(e.video_id, e.kind, e.t0, e.t1, e.value) for e in events],
        )
        self.conn.commit()

    def replace_audio_events(self, video_id: str, events: list[AudioEvent]) -> None:
        self.conn.execute("DELETE FROM audio_events WHERE video_id=?", (video_id,))
        self.add_audio_events(events)

    def replace_scenes(self, video_id: str, scenes: list[Scene]) -> None:
        self.conn.execute("DELETE FROM scenes WHERE video_id=?", (video_id,))
        self.conn.executemany(
            "INSERT INTO scenes(video_id, t0, t1, title, summary) VALUES(?,?,?,?,?)",
            [(s.video_id, s.t0, s.t1, s.title, s.summary) for s in scenes],
        )
        self.conn.commit()

    def delete_video(self, video_id: str) -> None:
        self.conn.execute("DELETE FROM videos WHERE id=?", (video_id,))
        self.conn.commit()

    # --- reads ---------------------------------------------------------

    def get_video(self, video_id: str) -> VideoMeta | None:
        row = self.conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        return _video_from_row(row) if row else None

    def list_videos(self) -> list[VideoMeta]:
        rows = self.conn.execute("SELECT * FROM videos ORDER BY created_at").fetchall()
        return [_video_from_row(r) for r in rows]

    def resolve_video(self, ref: str) -> VideoMeta | None:
        """Resolve a user-supplied reference: exact id, id prefix, or source substring."""
        for q, arg in (
            ("SELECT * FROM videos WHERE id=?", ref),
            ("SELECT * FROM videos WHERE id LIKE ?", ref + "%"),
            ("SELECT * FROM videos WHERE source LIKE ?", "%" + ref + "%"),
        ):
            rows = self.conn.execute(q, (arg,)).fetchall()
            if len(rows) == 1:
                return _video_from_row(rows[0])
        return None

    def get_segments(
        self, video_id: str, t0: float | None = None, t1: float | None = None
    ) -> list[Segment]:
        q = "SELECT * FROM segments WHERE video_id=?"
        args: list = [video_id]
        if t0 is not None:
            q += " AND t1 > ?"
            args.append(t0)
        if t1 is not None:
            q += " AND t0 < ?"
            args.append(t1)
        q += " ORDER BY t0"
        return [_segment_from_row(r) for r in self.conn.execute(q, args).fetchall()]

    def get_segments_by_ids(self, ids: list[int]) -> list[Segment]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT * FROM segments WHERE id IN ({marks}) ORDER BY t0", ids
        ).fetchall()
        return [_segment_from_row(r) for r in rows]

    def get_shots(self, video_id: str) -> list[Shot]:
        rows = self.conn.execute(
            "SELECT * FROM shots WHERE video_id=? ORDER BY t0", (video_id,)
        ).fetchall()
        return [Shot(id=r["id"], video_id=r["video_id"], t0=r["t0"], t1=r["t1"]) for r in rows]

    def get_audio_events(self, video_id: str, kind: str | None = None) -> list[AudioEvent]:
        q = "SELECT * FROM audio_events WHERE video_id=?"
        args: list = [video_id]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY t0"
        return [
            AudioEvent(
                id=r["id"], video_id=r["video_id"], kind=r["kind"],
                t0=r["t0"], t1=r["t1"], value=r["value"],
            )
            for r in self.conn.execute(q, args).fetchall()
        ]

    def get_scenes(self, video_id: str) -> list[Scene]:
        rows = self.conn.execute(
            "SELECT * FROM scenes WHERE video_id=? ORDER BY t0", (video_id,)
        ).fetchall()
        return [
            Scene(
                id=r["id"], video_id=r["video_id"], t0=r["t0"], t1=r["t1"],
                title=r["title"], summary=r["summary"],
            )
            for r in rows
        ]

    def fts_search(self, video_id: str, query: str, limit: int = 8) -> list[Segment]:
        """Full-text search over this video's transcript, best matches first."""
        rows = self.conn.execute(
            "SELECT s.* FROM segments_fts f JOIN segments s ON s.id = f.rowid"
            " WHERE segments_fts MATCH ? AND s.video_id = ?"
            " ORDER BY rank LIMIT ?",
            (query, video_id, limit),
        ).fetchall()
        return [_segment_from_row(r) for r in rows]


def _video_from_row(r: sqlite3.Row) -> VideoMeta:
    return VideoMeta(
        id=r["id"], source=r["source"], title=r["title"], duration=r["duration"],
        width=r["width"], height=r["height"], fps=r["fps"], created_at=r["created_at"],
    )


def _segment_from_row(r: sqlite3.Row) -> Segment:
    words = [Word(text=w[0], t0=w[1], t1=w[2]) for w in json.loads(r["words_json"])]
    return Segment(
        id=r["id"], video_id=r["video_id"], t0=r["t0"], t1=r["t1"],
        text=r["text"], words=words,
    )
