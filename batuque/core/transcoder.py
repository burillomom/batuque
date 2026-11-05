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
        dest_dir = os.path.dirname(dest_path)
        os.makedirs(dest_dir, exist_ok=True)
        base_name = os.path.basename(dest_path)
        temp_dest = os.path.join(dest_dir, f".tmp_{base_name}")

        codec_info = cls.CODEC_MAP.get(profile.target_codec.lower(), ("libmp3lame", "mp3"))
        ffmpeg_codec = codec_info[0]
        ext = codec_info[1]

        format_map = {
            "mp3": "mp3",
            "m4a": "ipod",
            "aac": "adts",
            "opus": "opus",
            "ogg": "ogg",
            "flac": "flac"
        }
        target_fmt = format_map.get(ext)

        is_uri = source_path.startswith(("smb://", "nfs://", "sftp://", "davs://", "ftp://"))
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", "pipe:0" if is_uri else source_path,
            "-vn",  # Exclude video streams
            "-c:a", ffmpeg_codec,
            "-b:a", f"{profile.target_bitrate}k"
        ]

        if target_fmt:
            cmd.extend(["-f", target_fmt])

        # Force ID3v2.3 for MP3 (crucial for portable players/car audio compatibility)
        if profile.target_codec.lower() == "mp3":
            cmd.extend(["-id3v2_version", "3"])

        # Volume leveling (EBU R128)
        if profile.volume_leveling:
            cmd.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"])

        cmd.append(temp_dest)

        try:
            if is_uri:
                cat_proc = subprocess.Popen(["gio", "cat", source_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                ff_proc = subprocess.Popen(cmd, stdin=cat_proc.stdout, stderr=subprocess.PIPE)
                cat_proc.stdout.close()
                _, ff_err = ff_proc.communicate(timeout=180)
                try:
                    cat_proc.wait(timeout=10)
                except Exception:
                    pass
                success = (ff_proc.returncode == 0 and cat_proc.returncode == 0)
            else:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                success = (res.returncode == 0)

            if success and os.path.isfile(temp_dest) and os.path.getsize(temp_dest) > 0:
                os.replace(temp_dest, dest_path)
                return True
            else:
                if os.path.exists(temp_dest):
                    try:
                        os.remove(temp_dest)
                    except OSError:
                        pass
                return False
        except Exception:
            if os.path.exists(temp_dest):
                try:
                    os.remove(temp_dest)
                except OSError:
                    pass
            return False
