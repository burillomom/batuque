"""
SQLite Database backend for Batuque.
Fast indexing and retrieval for large local & NAS music collections.
"""

import sqlite3
import os
from contextlib import contextmanager
from typing import List, Optional, Dict, Any, Set, Tuple
from .models import Track, NASSource


class LibraryDatabase:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            data_dir = os.path.expanduser("~/.local/share/batuque")
            old_dir = os.path.expanduser("~/.local/share/soundbee")
            if not os.path.exists(data_dir):
                if os.path.exists(old_dir):
                    try:
                        os.makedirs(os.path.dirname(data_dir), exist_ok=True)
                        os.symlink(old_dir, data_dir)
                    except Exception:
                        data_dir = old_dir
                else:
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

    def upsert_tracks_batch(self, tracks: List[Track]) -> int:
        if not tracks:
            return 0
        data = [
            (
                t.file_path, t.title, t.artist, t.album_artist,
                t.album, t.genre, t.year, t.track_number,
                t.disc_number, t.duration_seconds, t.bitrate_kbps,
                t.sample_rate, t.file_format, t.size_bytes,
                t.mtime, 1 if t.has_artwork else 0, t.source_id
            )
            for t in tracks
        ]
        with self._get_connection() as conn:
            conn.executemany("""
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
            """, data)
            return len(tracks)

    def get_cached_paths_map(self, source_id: Optional[str] = None) -> Dict[str, Tuple[float, int, str]]:
        """
        Returns a dictionary mapping file_path -> (mtime, size_bytes, source_id).
        Enables ultra-fast O(1) in-memory differential comparison during library updates.
        """
        with self._get_connection() as conn:
            if source_id:
                cursor = conn.execute(
                    "SELECT file_path, mtime, size_bytes, source_id FROM tracks WHERE source_id = ?",
                    (source_id,)
                )
            else:
                cursor = conn.execute("SELECT file_path, mtime, size_bytes, source_id FROM tracks")
            return {
                row[0]: (float(row[1] or 0), int(row[2] or 0), str(row[3] or ""))
                for row in cursor.fetchall()
            }

    def update_source_id_for_paths(self, file_paths: List[str], new_source_id: str):
        """Bulk updates the source_id for existing tracks."""
        if not file_paths:
            return
        with self._get_connection() as conn:
            conn.executemany(
                "UPDATE tracks SET source_id = ? WHERE file_path = ?",
                [(new_source_id, p) for p in file_paths]
            )

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
        return self.get_tracks_sorted(search_query=query)

    def get_tracks_sorted(
        self,
        order_by: str = "album",
        order_desc: bool = False,
        search_query: Optional[str] = None,
        filter_artist: Optional[str] = None,
        filter_album: Optional[str] = None,
        filter_year: Optional[int] = None,
        filter_genre: Optional[str] = None
    ) -> List[Track]:
        """
        Retrieves tracks with customizable sorting and filtering.
        Supported order_by: 'album', 'artist', 'year', 'title', 'duration', 'track_number'
        """
        sql = "SELECT * FROM tracks WHERE 1=1"
        params = []

        if search_query:
            p = f"%{search_query.strip()}%"
            sql += " AND (title LIKE ? OR artist LIKE ? OR album LIKE ? OR genre LIKE ?)"
            params.extend([p, p, p, p])

        if filter_artist:
            sql += " AND (COALESCE(NULLIF(album_artist, ''), artist) = ? OR artist = ?)"
            params.extend([filter_artist, filter_artist])

        if filter_album:
            sql += " AND album = ?"
            params.append(filter_album)

        if filter_year is not None:
            if filter_year == 0:
                sql += " AND (year IS NULL OR year = 0)"
            else:
                sql += " AND year = ?"
                params.append(filter_year)

        if filter_genre is not None:
            if filter_genre in ("Sem Gênero", ""):
                sql += " AND (genre IS NULL OR genre = '')"
            else:
                sql += " AND genre LIKE ?"
                params.append(f"%{filter_genre}%")

        direction = "DESC" if order_desc else "ASC"

        if order_by == "artist":
            sql += f" ORDER BY COALESCE(NULLIF(album_artist, ''), artist) {direction}, album, disc_number, track_number"
        elif order_by == "album":
            sql += f" ORDER BY album {direction}, COALESCE(NULLIF(album_artist, ''), artist), disc_number, track_number"
        elif order_by == "year":
            sql += f" ORDER BY COALESCE(year, 0) {direction}, album, disc_number, track_number"
        elif order_by == "title":
            sql += f" ORDER BY title {direction}"
        elif order_by == "duration":
            sql += f" ORDER BY duration_seconds {direction}"
        elif order_by == "track_number":
            sql += f" ORDER BY disc_number {direction}, track_number {direction}, title"
        else:
            sql += f" ORDER BY artist, album, track_number"

        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            return [self._row_to_track(row) for row in cursor.fetchall()]

    def get_albums_grouped(
        self,
        order_by: str = "artist",
        order_desc: bool = False,
        search_query: Optional[str] = None,
        filter_artist: Optional[str] = None,
        filter_year: Optional[int] = None,
        filter_genre: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Groups tracks into Albums with metadata, track list, and total duration.
        """
        tracks = self.get_tracks_sorted(
            order_by="track_number",
            search_query=search_query,
            filter_artist=filter_artist,
            filter_year=filter_year,
            filter_genre=filter_genre
        )

        albums_dict: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for t in tracks:
            key = (t.display_album, t.display_artist)
            if key not in albums_dict:
                albums_dict[key] = {
                    "album": t.display_album,
                    "artist": t.display_artist,
                    "year": t.year or 0,
                    "genre": t.genre or "",
                    "tracks": [],
                    "track_count": 0,
                    "duration_seconds": 0.0,
                    "has_artwork": t.has_artwork,
                    "sample_path": t.file_path,
                    "sample_paths": [t.file_path]
                }
            album_info = albums_dict[key]
            album_info["tracks"].append(t)
            album_info["track_count"] += 1
            album_info["duration_seconds"] += t.duration_seconds
            if len(album_info["sample_paths"]) < 5 and t.file_path not in album_info["sample_paths"]:
                album_info["sample_paths"].append(t.file_path)
            if t.has_artwork:
                album_info["has_artwork"] = True
            if not album_info["year"] and t.year:
                album_info["year"] = t.year

        result = list(albums_dict.values())

        reverse = order_desc
        if order_by == "year":
            result.sort(key=lambda a: (a["year"], a["album"].lower()), reverse=reverse)
        elif order_by == "album":
            result.sort(key=lambda a: a["album"].lower(), reverse=reverse)
        elif order_by == "artist":
            result.sort(key=lambda a: (a["artist"].lower(), a["year"], a["album"].lower()), reverse=reverse)

        return result

    def get_artists_summary(self, search_query: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns artists with their album and track counts."""
        sql = """
        SELECT 
            COALESCE(NULLIF(album_artist, ''), NULLIF(artist, ''), 'Artista Desconhecido') as artist_name,
            COUNT(DISTINCT album) as album_count,
            COUNT(*) as track_count,
            MIN(year) as min_year,
            MAX(year) as max_year
        FROM tracks
        WHERE 1=1
        """
        params = []
        if search_query:
            sql += " AND (title LIKE ? OR artist LIKE ? OR album LIKE ?)"
            p = f"%{search_query.strip()}%"
            params.extend([p, p, p])

        sql += " GROUP BY artist_name ORDER BY artist_name COLLATE NOCASE ASC"

        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_years_summary(self, search_query: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns distribution by year."""
        sql = """
        SELECT 
            COALESCE(year, 0) as year_val,
            COUNT(DISTINCT album) as album_count,
            COUNT(*) as track_count
        FROM tracks
        WHERE 1=1
        """
        params = []
        if search_query:
            sql += " AND (title LIKE ? OR artist LIKE ? OR album LIKE ?)"
            p = f"%{search_query.strip()}%"
            params.extend([p, p, p])

        sql += " GROUP BY year_val ORDER BY year_val DESC"

        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_genres_summary(self, search_query: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns distribution by genre."""
        sql = """
        SELECT 
            COALESCE(NULLIF(genre, ''), 'Sem Gênero') as genre_name,
            COUNT(*) as track_count
        FROM tracks
        WHERE 1=1
        """
        params = []
        if search_query:
            sql += " AND (title LIKE ? OR artist LIKE ? OR album LIKE ?)"
            p = f"%{search_query.strip()}%"
            params.extend([p, p, p])

        sql += " GROUP BY genre_name ORDER BY track_count DESC, genre_name ASC"

        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

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
