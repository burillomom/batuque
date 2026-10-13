"""
Library scanner for SoundBee.
Performs fast incremental scanning over local folders and NAS network shares.
"""

import os
import time
from typing import Callable, Optional, Set
from .models import Track
from .database import LibraryDatabase
from .metadata import MetadataReader


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
        Uses incremental mtime checks to skip unchanged tracks.
        """
        self._cancel_requested = False
        if not os.path.isdir(root_path):
            return 0

        # Step 1: Collect valid audio file paths
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

                # Check if we already have this file with the same mtime and size in DB
                cached_track = self.db.get_track_by_path(file_path)
                if cached_track and abs(cached_track.mtime - mtime) < 0.01 and cached_track.size_bytes == size:
                    continue  # Unchanged, skip expensive tag parsing

                # File is new or changed
                track = MetadataReader.read_track(file_path, source_id=source_id)
                if track:
                    self.db.upsert_track(track)
                    updated_count += 1

            except Exception:
                continue

        # Clean up deleted files from this source
        if not self._cancel_requested and source_id:
            self.db.prune_missing_files(existing_paths, source_id=source_id)

        return updated_count
