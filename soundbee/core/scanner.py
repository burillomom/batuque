"""
Library scanner for SoundBee.
Performs fast incremental scanning over local folders and NAS network shares (POSIX and GIO URIs).
"""

import os
from typing import Callable, Optional, Set, List, Tuple, Any
from .models import Track
from .database import LibraryDatabase
from .metadata import MetadataReader

try:
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio
    GIO_AVAILABLE = True
except Exception:
    GIO_AVAILABLE = False


class LibraryScanner:
    def __init__(self, db: LibraryDatabase):
        self.db = db
        self._cancel_requested = False

    def cancel(self):
        self._cancel_requested = True

    def scan_directory(
        self,
        root_path: str,
        source_id: str = "local",
        on_progress: Optional[Callable[[int, int, str], None]] = None
    ) -> int:
        """
        Scans a directory tree for audio files.
        Supports both local filesystem paths and GIO network URIs (smb://, nfs://, etc.).
        """
        self._cancel_requested = False

        if root_path.startswith(("smb://", "nfs://", "sftp://", "davs://", "ftp://")):
            return self._scan_gio_uri(root_path, source_id, on_progress)

        if not os.path.isdir(root_path):
            return 0

        # Local POSIX scanning
        audio_files = []
        for root, _, files in os.walk(root_path):
            if self._cancel_requested:
                break
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in MetadataReader.SUPPORTED_EXTENSIONS:
                    audio_files.append(os.path.join(root, f))

        total_files = len(audio_files)
        scanned_count = 0
        updated_count = 0
        existing_paths: Set[str] = set()

        for file_path in audio_files:
            if self._cancel_requested:
                break

            existing_paths.add(file_path)
            scanned_count += 1

            if on_progress:
                on_progress(scanned_count, total_files, file_path)

            try:
                stat = os.stat(file_path)
                mtime = stat.st_mtime
                size = stat.st_size

                cached_track = self.db.get_track_by_path(file_path)
                if cached_track and abs(cached_track.mtime - mtime) < 0.01 and cached_track.size_bytes == size:
                    continue  # Unchanged, skip expensive tag parsing

                track = MetadataReader.read_track(file_path, source_id=source_id)
                if track:
                    self.db.upsert_track(track)
                    updated_count += 1

            except Exception:
                continue

        if not self._cancel_requested and source_id:
            self.db.prune_missing_files(existing_paths, source_id=source_id)

        return updated_count

    def _scan_gio_uri(
        self,
        root_uri: str,
        source_id: str,
        on_progress: Optional[Callable[[int, int, str], None]] = None
    ) -> int:
        """Scans a network share over GIO/GVFS (e.g. smb://)."""
        if not GIO_AVAILABLE:
            return 0

        root_gfile = Gio.File.new_for_uri(root_uri)
        audio_items: List[Tuple[Any, Any]] = []

        def recurse(folder):
            if self._cancel_requested:
                return
            try:
                enumerator = folder.enumerate_children(
                    "standard::name,standard::type,standard::size,time::modified",
                    Gio.FileQueryInfoFlags.NONE,
                    None
                )
                for info in enumerator:
                    if self._cancel_requested:
                        break
                    child = folder.get_child(info.get_name())
                    ftype = info.get_file_type()
                    if ftype == Gio.FileType.DIRECTORY:
                        recurse(child)
                    elif ftype == Gio.FileType.REGULAR:
                        ext = os.path.splitext(info.get_name())[1].lower()
                        if ext in MetadataReader.SUPPORTED_EXTENSIONS:
                            audio_items.append((child, info))
            except Exception:
                pass

        recurse(root_gfile)

        total_files = len(audio_items)
        scanned_count = 0
        updated_count = 0
        existing_paths: Set[str] = set()

        for gfile, info in audio_items:
            if self._cancel_requested:
                break

            uri = gfile.get_uri()
            existing_paths.add(uri)
            scanned_count += 1

            if on_progress:
                on_progress(scanned_count, total_files, info.get_name())

            try:
                size = info.get_size()
                dt = info.get_modification_date_time()
                mtime = float(dt.to_unix()) if dt else 0.0

                # Fast incremental check in SQLite DB
                cached_track = self.db.get_track_by_path(uri)
                if cached_track and abs(cached_track.mtime - mtime) < 0.01 and cached_track.size_bytes == size:
                    continue

                track = MetadataReader.read_track_from_gio(gfile, source_id=source_id)
                if track:
                    self.db.upsert_track(track)
                    updated_count += 1
            except Exception:
                continue

        if not self._cancel_requested and source_id:
            self.db.prune_missing_files(existing_paths, source_id=source_id)

        return updated_count
