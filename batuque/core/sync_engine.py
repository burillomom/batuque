"""
Differential Synchronization Engine for Batuque.
Plans and executes library syncs to external devices (USB, DAPs, MTP, Virtual Folders).
"""

import os
import shutil
import io
from typing import List, Callable, Optional, Set, Dict
from PIL import Image

from .models import Track, DeviceProfile, SyncItem, SyncPlan
from .organizer import FileOrganizer
from .transcoder import AudioTranscoder
from .artwork import ArtworkProcessor, AlbumArtworkCache
from .metadata import MetadataReader


class SyncEngine:
    def __init__(self):
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def create_sync_plan(
        self,
        profile: DeviceProfile,
        tracks_to_sync: List[Track],
        allow_delete: bool = True,
        force_overwrite: bool = False,
        force_artwork: bool = False,
        source_roots: Optional[List[str]] = None
    ) -> SyncPlan:
        """
        Analyzes differences between source library and target device.
        Produces an execution plan with actions: copy, transcode, skip, delete.
        """
        plan = SyncPlan(device_profile=profile)
        plan.force_artwork = force_artwork
        dest_root = os.path.abspath(profile.root_path)
        music_dest_dir = os.path.join(dest_root, profile.music_subfolder)
        os.makedirs(music_dest_dir, exist_ok=True)

        if source_roots is None:
            try:
                from .database import LibraryDatabase
                db = LibraryDatabase()
                source_roots = [s.uri for s in db.get_nas_sources()] + [s.mount_path for s in db.get_nas_sources() if s.mount_path]
            except Exception:
                source_roots = []

        # Check free disk space on device
        try:
            stat = os.statvfs(dest_root)
            plan.device_free_space_bytes = stat.f_bavail * stat.f_frsize
        except OSError:
            plan.device_free_space_bytes = 0

        target_expected_full_paths: Set[str] = set()

        for track in tracks_to_sync:
            need_transcode, target_ext = AudioTranscoder.should_transcode(track, profile)

            if profile.preserve_folder_structure:
                formatted_rel = FileOrganizer.get_preserved_relative_path(
                    track.file_path,
                    source_roots=source_roots,
                    target_extension=target_ext
                )
                target_rel = os.path.join(profile.music_subfolder, formatted_rel)
            elif profile.preserve_filenames:
                base_name = os.path.splitext(os.path.basename(track.file_path))[0]
                target_rel = os.path.join(profile.music_subfolder, f"{base_name}.{target_ext}")
            else:
                formatted_rel = FileOrganizer.format_relative_path(
                    track, profile.naming_rule, target_extension=target_ext
                )
                target_rel = os.path.join(profile.music_subfolder, formatted_rel)

            target_full = os.path.join(dest_root, target_rel)
            target_expected_full_paths.add(os.path.abspath(target_full))

            # Estimate target file size
            if need_transcode:
                bitrate = profile.target_bitrate
                duration = track.duration_seconds or 180
                est_size = int((bitrate * 1000 * duration) / 8)
            else:
                est_size = track.size_bytes

            # Check if file already exists at target (unless force_overwrite)
            if not force_overwrite and os.path.isfile(target_full):
                dest_size = os.path.getsize(target_full)
                if not need_transcode:
                    if dest_size == track.size_bytes:
                        item = SyncItem(
                            track=track,
                            source_path=track.file_path,
                            target_rel_path=target_rel,
                            target_full_path=target_full,
                            action="skip",
                            reason="Already up-to-date",
                            estimated_size_bytes=dest_size
                        )
                        plan.items_to_skip.append(item)
                        continue
                else:
                    if dest_size > 0:
                        item = SyncItem(
                            track=track,
                            source_path=track.file_path,
                            target_rel_path=target_rel,
                            target_full_path=target_full,
                            action="skip",
                            reason="Already transcoded on device",
                            estimated_size_bytes=dest_size
                        )
                        plan.items_to_skip.append(item)
                        continue

            if need_transcode:
                item = SyncItem(
                    track=track,
                    source_path=track.file_path,
                    target_rel_path=target_rel,
                    target_full_path=target_full,
                    action="transcode",
                    reason=f"Transcode to {profile.target_codec.upper()} {profile.target_bitrate}k",
                    estimated_size_bytes=est_size
                )
                plan.items_to_transcode.append(item)
                plan.total_transfer_bytes += est_size
            else:
                item = SyncItem(
                    track=track,
                    source_path=track.file_path,
                    target_rel_path=target_rel,
                    target_full_path=target_full,
                    action="copy",
                    reason="Direct transfer",
                    estimated_size_bytes=est_size
                )
                plan.items_to_copy.append(item)
                plan.total_transfer_bytes += est_size

        # Find orphaned files to delete if prune is enabled
        if allow_delete and profile.delete_unmatched_files and os.path.isdir(music_dest_dir):
            for root, _, files in os.walk(music_dest_dir):
                for f in files:
                    full_p = os.path.abspath(os.path.join(root, f))
                    if f.lower() in ("cover.jpg", "folder.jpg", "cover.png", "folder.png"):
                        continue
                    if full_p not in target_expected_full_paths:
                        plan.items_to_delete.append(SyncItem(
                            track=Track(file_path=full_p, title=f),
                            source_path="",
                            target_rel_path=os.path.relpath(full_p, dest_root),
                            target_full_path=full_p,
                            action="delete",
                            reason="Orphaned file not in sync selection"
                        ))

        plan.estimated_target_bytes = plan.total_transfer_bytes
        return plan

    def _ensure_nas_mounted_for_sources(self, source_paths: Set[str]):
        """Proactively mounts NAS sources matching any of the given URIs."""
        try:
            from .nas import NASManager
            from .database import LibraryDatabase
            db = LibraryDatabase()
            nas_sources = db.get_nas_sources()
            for src in nas_sources:
                NASManager.mount_share(src)
        except Exception:
            pass

    def _cleanup_empty_dirs(self, root_dir: str):
        """Removes empty directories left behind by failed transfers."""
        try:
            for root, dirs, files in os.walk(root_dir, topdown=False):
                if root == root_dir or os.path.basename(root).startswith("."):
                    continue
                try:
                    if not os.listdir(root):
                        os.rmdir(root)
                except OSError:
                    pass
        except Exception:
            pass

    def execute_plan(
        self,
        plan: SyncPlan,
        on_progress: Optional[Callable[[int, int, str], None]] = None
    ) -> bool:
        """
        Executes the planned synchronization actions.
        """
        self._is_cancelled = False
        all_actions = plan.items_to_copy + plan.items_to_transcode + plan.items_to_delete
        total = len(all_actions)
        current = 0

        # Pre-mount NAS if needed
        smb_sources = {item.source_path for item in plan.items_to_copy + plan.items_to_transcode if item.source_path.startswith("smb://")}
        if smb_sources:
            self._ensure_nas_mounted_for_sources(smb_sources)

        # Execute deletions first to free space
        for item in plan.items_to_delete:
            if self._is_cancelled:
                return False
            current += 1
            if on_progress:
                on_progress(current, total, f"Deleting: {item.target_rel_path}")
            try:
                if os.path.isfile(item.target_full_path):
                    os.remove(item.target_full_path)
            except OSError:
                pass

        # Execute direct copies
        for item in plan.items_to_copy:
            if self._is_cancelled:
                return False
            current += 1
            if on_progress:
                on_progress(current, total, f"Copying: {item.track.display_title}")

            dest_dir = os.path.dirname(item.target_full_path)
            os.makedirs(dest_dir, exist_ok=True)
            copied = False
            try:
                if item.source_path.startswith(("smb://", "nfs://", "sftp://", "davs://", "ftp://")):
                    import gi
                    gi.require_version('Gio', '2.0')
                    from gi.repository import Gio
                    src_file = Gio.File.new_for_uri(item.source_path)
                    dst_file = Gio.File.new_for_path(item.target_full_path)
                    try:
                        src_file.copy(dst_file, Gio.FileCopyFlags.OVERWRITE, None, None, None)
                        copied = True
                    except Exception as ge:
                        err_str = str(ge).lower()
                        if "não está montada" in err_str or "not mounted" in err_str:
                            self._ensure_nas_mounted_for_sources({item.source_path})
                            src_file.copy(dst_file, Gio.FileCopyFlags.OVERWRITE, None, None, None)
                            copied = True
                        else:
                            raise ge
                else:
                    shutil.copy2(item.source_path, item.target_full_path)
                    copied = True

                if copied and os.path.isfile(item.target_full_path) and os.path.getsize(item.target_full_path) > 0:
                    plan.successful_items += 1
                    ArtworkProcessor.process_and_save_artwork(
                        item.source_path, item.target_full_path, plan.device_profile, track=item.track, force=plan.force_artwork
                    )
                else:
                    plan.failed_items += 1
                    plan.error_messages.append(f"Arquivo vazio ou inexistente: {item.track.display_title}")
            except Exception as e:
                plan.failed_items += 1
                plan.error_messages.append(f"Erro ao copiar {item.track.display_title}: {e}")
                if os.path.exists(item.target_full_path) and os.path.getsize(item.target_full_path) == 0:
                    try:
                        os.remove(item.target_full_path)
                    except OSError:
                        pass
                continue

        # Execute transcodings
        for item in plan.items_to_transcode:
            if self._is_cancelled:
                return False
            current += 1
            if on_progress:
                on_progress(current, total, f"Transcoding: {item.track.display_title}")

            dest_dir = os.path.dirname(item.target_full_path)
            os.makedirs(dest_dir, exist_ok=True)
            success = AudioTranscoder.transcode_file(
                item.source_path, item.target_full_path, plan.device_profile
            )
            if not success and item.source_path.startswith("smb://"):
                self._ensure_nas_mounted_for_sources({item.source_path})
                success = AudioTranscoder.transcode_file(
                    item.source_path, item.target_full_path, plan.device_profile
                )

            if success and os.path.isfile(item.target_full_path) and os.path.getsize(item.target_full_path) > 0:
                plan.successful_items += 1
                ArtworkProcessor.process_and_save_artwork(
                    item.source_path, item.target_full_path, plan.device_profile, track=item.track, force=plan.force_artwork
                )
            else:
                plan.failed_items += 1
                plan.error_messages.append(f"Falha na conversão: {item.track.display_title}")

        # Artwork healing and verification for existing/skipped tracks on device
        if (plan.device_profile.artwork_save_file or plan.device_profile.artwork_embed) and not self._is_cancelled:
            album_dirs: Dict[str, SyncItem] = {}
            for item in plan.items_to_skip:
                if item.target_full_path and item.source_path and os.path.exists(item.target_full_path):
                    d = os.path.dirname(item.target_full_path)
                    if d not in album_dirs:
                        album_dirs[d] = item

            for d, item in album_dirs.items():
                if self._is_cancelled:
                    return False
                art_file = os.path.join(d, plan.device_profile.artwork_filename or "cover.jpg")
                needs_healing = plan.force_artwork
                if not needs_healing and plan.device_profile.artwork_save_file:
                    if not os.path.exists(art_file) or os.path.getsize(art_file) == 0:
                        needs_healing = True
                    elif AlbumArtworkCache.is_corrupted_cover_file(art_file):
                        needs_healing = True

                if not needs_healing and plan.device_profile.artwork_embed and os.path.isfile(item.target_full_path):
                    embedded = MetadataReader.extract_artwork_bytes(item.target_full_path)
                    if embedded:
                        try:
                            with Image.open(io.BytesIO(embedded)) as im:
                                if AlbumArtworkCache.is_corrupted_artwork(im):
                                    needs_healing = True
                        except Exception:
                            needs_healing = True

                if needs_healing:
                    ArtworkProcessor.process_and_save_artwork(
                        item.source_path, item.target_full_path, plan.device_profile, track=item.track, force=True
                    )

        if plan.failed_items > 0:
            self._cleanup_empty_dirs(dest_root)

        if self._is_cancelled:
            return False
        return plan.failed_items == 0
