"""
Differential Synchronization Engine for SoundBee.
Plans and executes library syncs to external devices (USB, DAPs, MTP, Virtual Folders).
"""

import os
import shutil
from typing import List, Callable, Optional, Set
from .models import Track, DeviceProfile, SyncItem, SyncPlan
from .organizer import FileOrganizer
from .transcoder import AudioTranscoder
from .artwork import ArtworkProcessor


class SyncEngine:
    def __init__(self):
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def create_sync_plan(
        self,
        profile: DeviceProfile,
        tracks_to_sync: List[Track],
        allow_delete: bool = True
    ) -> SyncPlan:
        """
        Analyzes differences between source library and target device.
        Produces an execution plan with actions: copy, transcode, skip, delete.
        """
        plan = SyncPlan(device_profile=profile)
        dest_root = os.path.abspath(profile.root_path)
        music_dest_dir = os.path.join(dest_root, profile.music_subfolder)
        os.makedirs(music_dest_dir, exist_ok=True)

        # Check free disk space on device
        try:
            stat = os.statvfs(dest_root)
            plan.device_free_space_bytes = stat.f_bavail * stat.f_frsize
        except OSError:
            plan.device_free_space_bytes = 0

        target_expected_full_paths: Set[str] = set()

        for track in tracks_to_sync:
            need_transcode, target_ext = AudioTranscoder.should_transcode(track, profile)

            if profile.preserve_filenames:
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
                # Bitrate kbps * duration / 8 = bytes
                bitrate = profile.target_bitrate
                duration = track.duration_seconds or 180
                est_size = int((bitrate * 1000 * duration) / 8)
            else:
                est_size = track.size_bytes

            # Check if file already exists at target
            if os.path.isfile(target_full):
                dest_size = os.path.getsize(target_full)
                if not need_transcode:
                    # Direct copy check: compare sizes
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
                    # Transcoded check: if exists and size > 0, assume complete
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
                    # Ignore cover art from deletion if saved in album folders
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
            try:
                if item.source_path.startswith(("smb://", "nfs://", "sftp://", "davs://", "ftp://")):
                    import subprocess
                    subprocess.run(["gio", "copy", item.source_path, item.target_full_path], check=True)
                else:
                    shutil.copy2(item.source_path, item.target_full_path)
                # Process artwork
                ArtworkProcessor.process_and_save_artwork(
                    item.source_path, item.target_full_path, plan.device_profile
                )
            except Exception:
                continue

        # Execute transcodings
        for item in plan.items_to_transcode:
            if self._is_cancelled:
                return False
            current += 1
            if on_progress:
                on_progress(current, total, f"Transcoding: {item.track.display_title}")

            success = AudioTranscoder.transcode_file(
                item.source_path, item.target_full_path, plan.device_profile
            )
            if success:
                ArtworkProcessor.process_and_save_artwork(
                    item.source_path, item.target_full_path, plan.device_profile
                )

        return not self._is_cancelled
