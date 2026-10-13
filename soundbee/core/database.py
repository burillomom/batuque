"""
SQLite Database backend for SoundBee.
Fast indexing and retrieval for large local & NAS music collections.
"""

import sqlite3
import os
from contextlib import contextmanager
from typing import List, Optional, Dict, Any, Set
from .models import Track, NASSource


class LibraryDatabase:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            data_dir = os.path.expanduser("~/.local/share/soundbee")
            os.makedirs(data_dir, exist_ok=True)
            db_path = os.path.join(data_dir, "library.db")

        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                title TEXT,
                artist TEXT,
                album_artist TEXT,
                album TEXT,
                genre TEXT,
                year INTEGER,
                track_number INTEGER,
                disc_number INTEGER,
                duration_seconds REAL,
                bitrate_kbps INTEGER,
                sample_rate INTEGER,
                file_format TEXT,
                size_bytes INTEGER,
                mtime REAL,
                has_artwork INTEGER,
                source_id TEXT DEFAULT 'local'
            )
            """)

            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_source ON tracks(source_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_mtime ON tracks(mtime)")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS nas_sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                uri TEXT NOT NULL,
                mount_path TEXT,
                username TEXT,
                password TEXT,
                auto_connect INTEGER DEFAULT 1,
                last_scanned REAL,
                total_tracks INTEGER DEFAULT 0
            )
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                created_at REAL
            )
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS playlist_tracks (
                playlist_id INTEGER,
                track_id INTEGER,
                position INTEGER,
                PRIMARY KEY (playlist_id, track_id),
                FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
                FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
            )
            """)

    def upsert_track(self, track: Track) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute("""
            INSERT INTO tracks (
                file_path, title, artist, album_artist, album, genre, year,
                track_number, disc_number, duration_seconds, bitrate_kbps,
                sample_rate, file_format, size_bytes, mtime, has_artwork, source_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                title = excluded.title,
                artist = excluded.artist,
                album_artist = excluded.album_artist,
                album = excluded.album,
                genre = excluded.genre,
                year = excluded.year,
                track_number = excluded.track_number,
                disc_number = excluded.disc_number,
                duration_seconds = excluded.duration_seconds,
                bitrate_kbps = excluded.bitrate_kbps,
                sample_rate = excluded.sample_rate,
                file_format = excluded.file_format,
                size_bytes = excluded.size_bytes,
                mtime = excluded.mtime,
                has_artwork = excluded.has_artwork,
                source_id = excluded.source_id
            """, (
                track.file_path, track.title, track.artist, track.album_artist,
                track.album, track.genre, track.year, track.track_number,
                track.disc_number, track.duration_seconds, track.bitrate_kbps,
                track.sample_rate, track.file_format, track.size_bytes,
                track.mtime, 1 if track.has_artwork else 0, track.source_id
            ))
            return cursor.lastrowid

    def get_track_by_path(self, file_path: str) -> Optional[Track]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM tracks WHERE file_path = ?", (file_path,)).fetchone()
            if row:
                return self._row_to_track(row)
        return None

    def get_all_tracks(self, source_id: Optional[str] = None) -> List[Track]:
        with self._get_connection() as conn:
            if source_id:
                cursor = conn.execute("SELECT * FROM tracks WHERE source_id = ? ORDER BY artist, album, track_number", (source_id,))
            else:
                cursor = conn.execute("SELECT * FROM tracks ORDER BY artist, album, track_number")
            return [self._row_to_track(row) for row in cursor.fetchall()]

    def search_tracks(self, query: str) -> List[Track]:
        with self._get_connection() as conn:
            pattern = f"%{query}%"
            cursor = conn.execute("""
            SELECT * FROM tracks
            WHERE title LIKE ? OR artist LIKE ? OR album LIKE ? OR genre LIKE ?
            ORDER BY artist, album, track_number
            """, (pattern, pattern, pattern, pattern))
            return [self._row_to_track(row) for row in cursor.fetchall()]

    def prune_missing_files(self, existing_paths: Set[str], source_id: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT id, file_path FROM tracks WHERE source_id = ?", (source_id,))
            to_delete = [row["id"] for row in cursor.fetchall() if row["file_path"] not in existing_paths]
            if to_delete:
                conn.executemany("DELETE FROM tracks WHERE id = ?", [(tid,) for tid in to_delete])
            return len(to_delete)

    def get_stats(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            tracks_count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            artists_count = conn.execute("SELECT COUNT(DISTINCT COALESCE(NULLIF(album_artist, ''), artist)) FROM tracks").fetchone()[0]
            albums_count = conn.execute("SELECT COUNT(DISTINCT album) FROM tracks").fetchone()[0]
            total_duration = conn.execute("SELECT COALESCE(SUM(duration_seconds), 0) FROM tracks").fetchone()[0]
            total_size = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM tracks").fetchone()[0]
            return {
                "tracks": tracks_count,
                "artists": artists_count,
                "albums": albums_count,
                "duration_seconds": total_duration,
                "size_bytes": total_size
            }

    # NAS Sources management
    def upsert_nas_source(self, source: NASSource):
        with self._get_connection() as conn:
            conn.execute("""
            INSERT INTO nas_sources (id, name, uri, mount_path, username, password, auto_connect, last_scanned, total_tracks)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                uri = excluded.uri,
                mount_path = excluded.mount_path,
                username = excluded.username,
                password = excluded.password,
                auto_connect = excluded.auto_connect,
                last_scanned = excluded.last_scanned,
                total_tracks = excluded.total_tracks
            """, (
                source.id, source.name, source.uri, source.mount_path,
                source.username, source.password, 1 if source.auto_connect else 0,
                source.last_scanned, source.total_tracks
            ))

    def get_nas_sources(self) -> List[NASSource]:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM nas_sources")
            sources = []
            for row in cursor.fetchall():
                sources.append(NASSource(
                    id=row["id"],
                    name=row["name"],
                    uri=row["uri"],
                    mount_path=row["mount_path"] or "",
                    username=row["username"],
                    password=row["password"],
                    auto_connect=bool(row["auto_connect"]),
                    last_scanned=row["last_scanned"],
                    total_tracks=row["total_tracks"] or 0
                ))
            return sources

    def delete_nas_source(self, source_id: str):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM nas_sources WHERE id = ?", (source_id,))
            conn.execute("DELETE FROM tracks WHERE source_id = ?", (source_id,))

    def _row_to_track(self, row: sqlite3.Row) -> Track:
        return Track(
            id=row["id"],
            file_path=row["file_path"],
            title=row["title"] or "",
            artist=row["artist"] or "",
            album_artist=row["album_artist"] or "",
            album=row["album"] or "",
            genre=row["genre"] or "",
            year=row["year"],
            track_number=row["track_number"],
            disc_number=row["disc_number"] or 1,
            duration_seconds=row["duration_seconds"] or 0.0,
            bitrate_kbps=row["bitrate_kbps"] or 0,
            sample_rate=row["sample_rate"] or 44100,
            file_format=row["file_format"] or "",
            size_bytes=row["size_bytes"] or 0,
            mtime=row["mtime"] or 0.0,
            has_artwork=bool(row["has_artwork"]),
            source_id=row["source_id"] or "local"
        )
