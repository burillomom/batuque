"""
Library scanner for Batuque.
Performs ultra-fast incremental differential scanning over local folders and NAS network shares (POSIX and GIO URIs).
Uses in-memory differential cache comparison and concurrent thread workers.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional, Set, List, Tuple, Any, Dict
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
        Performs fast differential scanning: only new or modified files are parsed.
        """
        self._cancel_requested = False

        if root_path.startswith(("smb://", "nfs://", "sftp://", "davs://", "ftp://")):
            return self._scan_gio_uri(root_path, source_id, on_progress)

        if not os.path.isdir(root_path):
            return 0

        # Load in-memory cache of already indexed files
        cached_map = self.db.get_cached_paths_map(source_id)

        # 1. Fast discovery of candidate audio files
        existing_paths: Set[str] = set()
        files_to_parse: List[str] = []
        unchanged_count = 0

        for root, _, files in os.walk(root_path):
            if self._cancel_requested:
                break
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in MetadataReader.SUPPORTED_EXTENSIONS:
                    full_p = os.path.abspath(os.path.join(root, f))
                    existing_paths.add(full_p)
                    try:
                        stat = os.stat(full_p)
                        mtime = stat.st_mtime
                        size = stat.st_size
                        cached = cached_map.get(full_p)
                        if cached and abs(cached[0] - mtime) < 0.01 and cached[1] == size:
                            unchanged_count += 1
                        else:
                            files_to_parse.append(full_p)
                    except OSError:
                        continue

        total_files = len(existing_paths)
        if on_progress:
            on_progress(unchanged_count, total_files, f"{unchanged_count} faixas inalteradas identificadas.")

        updated_count = 0

        # 2. Parse new or modified files in parallel
        if files_to_parse and not self._cancel_requested:
            num_workers = min(8, max(2, os.cpu_count() or 4))
            batch: List[Track] = []

            def worker_read(fp):
                return MetadataReader.read_track(fp, source_id=source_id)

            last_report_time = 0.0
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = {executor.submit(worker_read, fp): fp for fp in files_to_parse}
                for future in as_completed(futures):
                    if self._cancel_requested:
                        executor.shutdown(wait=False, cancel_futures=True)
                        break
                    track = future.result()
                    if track:
                        batch.append(track)
                        updated_count += 1

                    if len(batch) >= 50:
                        self.db.upsert_tracks_batch(batch)
                        batch.clear()

                    now = time.time()
                    if on_progress and (now - last_report_time > 0.08 or updated_count == len(files_to_parse)):
                        last_report_time = now
                        curr = unchanged_count + updated_count
                        name = os.path.basename(futures[future])
                        on_progress(curr, total_files, name)

            if batch:
                self.db.upsert_tracks_batch(batch)

        # 3. Prune deleted files
        if not self._cancel_requested and source_id:
            self.db.prune_missing_files(existing_paths, source_id=source_id)

        return updated_count

    def _scan_gio_uri(
        self,
        root_uri: str,
        source_id: str,
        on_progress: Optional[Callable[[int, int, str], None]] = None
    ) -> int:
        """Scans a network share over GIO/GVFS with fast in-memory diffing and streaming tag parsing."""
        if not GIO_AVAILABLE:
            return 0

        root_gfile = Gio.File.new_for_uri(root_uri)
        # Load in-memory cache of already indexed files across DB
        cached_map = self.db.get_cached_paths_map()

        audio_items: List[Tuple[Any, Any, str, float, int]] = []

        # 1. Discover items on remote network share
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
                    elif ftype in (Gio.FileType.REGULAR, Gio.FileType.SYMBOLIC_LINK):
                        ext = os.path.splitext(info.get_name())[1].lower()
                        if ext in MetadataReader.SUPPORTED_EXTENSIONS:
                            dt = info.get_modification_date_time()
                            mtime = float(dt.to_unix()) if dt else 0.0
                            size = info.get_size()
                            audio_items.append((child, info, child.get_uri(), mtime, size))
            except Exception:
                pass

        recurse(root_gfile)

        total_files = len(audio_items)
        existing_paths: Set[str] = set()
        items_to_parse: List[Tuple[Any, str]] = []
        paths_to_adopt_source: List[str] = []
        unchanged_count = 0

        # 2. Ultra-fast in-memory differential check (microseconds per file)
        for child, info, uri, mtime, size in audio_items:
            existing_paths.add(uri)
            cached = cached_map.get(uri)
            if cached and abs(cached[0] - mtime) < 0.01 and cached[1] == size:
                unchanged_count += 1
                if cached[2] != source_id:
                    paths_to_adopt_source.append(uri)
            else:
                items_to_parse.append((child, info.get_name()))

        if paths_to_adopt_source:
            self.db.update_source_id_for_paths(paths_to_adopt_source, source_id)

        if on_progress:
            on_progress(unchanged_count, total_files, f"{unchanged_count} faixas já atualizadas no banco de dados.")

        updated_count = 0

        # 3. Parse only new or modified tracks concurrently
        if items_to_parse and not self._cancel_requested:
            num_workers = min(8, max(2, os.cpu_count() or 4))
            batch: List[Track] = []

            def worker_read_gio(item):
                gfile, _ = item
                return MetadataReader.read_track_from_gio(gfile, source_id=source_id)

            last_report_time = 0.0
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = {executor.submit(worker_read_gio, item): item[1] for item in items_to_parse}
                for future in as_completed(futures):
                    if self._cancel_requested:
                        executor.shutdown(wait=False, cancel_futures=True)
                        break
                    track = future.result()
                    if track:
                        batch.append(track)
                        updated_count += 1

                    if len(batch) >= 50:
                        self.db.upsert_tracks_batch(batch)
                        batch.clear()

                    now = time.time()
                    if on_progress and (now - last_report_time > 0.08 or updated_count == len(items_to_parse)):
                        last_report_time = now
                        curr = unchanged_count + updated_count
                        name = futures[future]
                        on_progress(curr, total_files, name)

            if batch:
                self.db.upsert_tracks_batch(batch)

        # 4. Prune missing files
        if not self._cancel_requested and source_id:
            self.db.prune_missing_files(existing_paths, source_id=source_id)

        return updated_count
