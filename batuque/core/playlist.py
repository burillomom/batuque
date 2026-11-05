"""
Playlist generation and synchronization for Batuque.
Exports M3U / M3U8 with device-relative or custom prefix paths.
"""

import os
from typing import List
from .models import Track, DeviceProfile


class PlaylistExporter:
    @classmethod
    def export_playlist(
        cls,
        playlist_name: str,
        tracks_with_target_paths: List[tuple[Track, str]],  # (track, target_rel_path_from_device_root)
        profile: DeviceProfile
    ) -> str:
        """
        Generates and writes an M3U8 playlist file onto the device.
        tracks_with_target_paths: List of tuples (Track, relative_path_on_device)
        Returns the absolute destination path of the playlist.
        """
        playlist_dir = os.path.join(profile.root_path, profile.playlist_subfolder)
        os.makedirs(playlist_dir, exist_ok=True)

        ext = "m3u8" if profile.playlist_format == "m3u8" else "m3u"
        clean_name = playlist_name.strip().replace("/", "_").replace("\\", "_")
        target_playlist_file = os.path.join(playlist_dir, f"{clean_name}.{ext}")

        lines = ["#EXTM3U"]
        for track, rel_path in tracks_with_target_paths:
            duration = int(track.duration_seconds)
            artist = track.artist or "Unknown Artist"
            title = track.title or "Unknown Title"
            lines.append(f"#EXTINF:{duration},{artist} - {title}")

            if profile.playlist_path_prefix:
                # Custom device path prefix (e.g. Android /storage/emulated/0/Music/)
                prefix = profile.playlist_path_prefix.rstrip("/") + "/"
                lines.append(prefix + rel_path.replace("\\", "/"))
            elif profile.playlist_relative:
                # Calculate relative path from playlist folder to audio file
                target_full = os.path.join(profile.root_path, rel_path)
                rel_to_playlist = os.path.relpath(target_full, playlist_dir)
                lines.append(rel_to_playlist.replace("\\", "/"))
            else:
                # Absolute path from device root
                lines.append("/" + rel_path.lstrip("/").replace("\\", "/"))

        with open(target_playlist_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        return target_playlist_file
