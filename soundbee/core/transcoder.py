"""
Audio Transcoding Engine for SoundBee using FFmpeg.
Implements MusicBee-style conversion policies (lossless-only, unsupported-only, volume leveling).
"""

import os
import subprocess
from typing import Tuple
from .models import Track, DeviceProfile


class AudioTranscoder:
    CODEC_MAP = {
        "mp3": ("libmp3lame", "mp3"),
        "aac": ("aac", "m4a"),
        "opus": ("libopus", "opus"),
        "ogg": ("libvorbis", "ogg"),
        "flac": ("flac", "flac")
    }

    LOSSLESS_FORMATS = {"flac", "wav", "alac", "aif", "aiff", "dsf", "dff", "ape", "wv"}

    @classmethod
    def should_transcode(cls, track: Track, profile: DeviceProfile) -> Tuple[bool, str]:
        """
        Determines whether a track must be transcoded based on device configuration.
        Returns: (transcode_needed: bool, target_extension: str)
        """
        source_ext = track.file_format.lower().lstrip(".")
        target_codec = profile.target_codec.lower()
        _, default_target_ext = cls.CODEC_MAP.get(target_codec, ("libmp3lame", "mp3"))

        if not profile.transcode_enabled:
            return False, source_ext

        # Rule 1: Convert unsupported formats only
        if profile.convert_unsupported_only:
            supported = {fmt.lower().lstrip(".") for fmt in profile.supported_formats}
            if source_ext in supported:
                return False, source_ext
            return True, default_target_ext

        # Rule 2: Convert lossless formats only (save device space, keep MP3s as-is)
        if profile.convert_lossless_only:
            if source_ext in cls.LOSSLESS_FORMATS:
                return True, default_target_ext
            # Original is already lossy; check bitrate threshold
            if track.bitrate_kbps and track.bitrate_kbps >= profile.convert_except_min_bitrate:
                return False, source_ext
            return False, source_ext

        # Default rule: Transcode to target codec
        if source_ext == default_target_ext:
            # If already in target format and bitrate is equal/lower, keep
            if track.bitrate_kbps and track.bitrate_kbps <= profile.target_bitrate:
                return False, source_ext

        return True, default_target_ext

    @classmethod
    def transcode_file(cls, source_path: str, dest_path: str, profile: DeviceProfile) -> bool:
        """
        Transcodes an audio file to destination path using FFmpeg.
        Writes atomically to a temp file and renames upon success.
        """
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        temp_dest = dest_path + ".soundbee_tmp"

        codec_info = cls.CODEC_MAP.get(profile.target_codec.lower(), ("libmp3lame", "mp3"))
        ffmpeg_codec = codec_info[0]

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", source_path,
            "-vn",  # Exclude video streams
            "-c:a", ffmpeg_codec,
            "-b:a", f"{profile.target_bitrate}k"
        ]

        # Force ID3v2.3 for MP3 (crucial for portable players/car audio compatibility)
        if profile.target_codec.lower() == "mp3":
            cmd.extend(["-id3v2_version", "3"])

        # Volume leveling (EBU R128)
        if profile.volume_leveling:
            cmd.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"])

        cmd.append(temp_dest)

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode == 0 and os.path.isfile(temp_dest) and os.path.getsize(temp_dest) > 0:
                os.replace(temp_dest, dest_path)
                return True
            else:
                if os.path.exists(temp_dest):
                    os.remove(temp_dest)
                return False
        except Exception:
            if os.path.exists(temp_dest):
                os.remove(temp_dest)
            return False
