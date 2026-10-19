"""
Album artwork processing, resizing, and embedding for SoundBee.
"""

import os
import io
from typing import Optional
from PIL import Image
from mutagen.id3 import ID3, APIC, error as ID3Error
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover

from .metadata import MetadataReader
from .models import DeviceProfile


class ArtworkProcessor:
    @classmethod
    def process_and_save_artwork(
        cls,
        source_audio_path: str,
        target_audio_path: str,
        profile: DeviceProfile
    ) -> bool:
        """
        Extracts, resizes, and either embeds artwork or saves cover.jpg in album directory.
        """
        raw_bytes = MetadataReader.extract_artwork_bytes(source_audio_path)
        if not raw_bytes:
            return False

        # Resize image if larger than max_size
        processed_bytes = cls._resize_image(raw_bytes, profile.artwork_max_size)

        target_dir = os.path.dirname(target_audio_path)
        os.makedirs(target_dir, exist_ok=True)

        # 1. Save cover file (e.g. cover.jpg)
        if profile.artwork_save_file:
            art_path = os.path.join(target_dir, profile.artwork_filename)
            if not os.path.exists(art_path):
                try:
                    with open(art_path, "wb") as f:
                        f.write(processed_bytes)
                except OSError:
                    pass

        # 2. Embed into target file tags
        if profile.artwork_embed and os.path.isfile(target_audio_path):
            cls._embed_artwork(target_audio_path, processed_bytes)

        return True

    @classmethod
    def _resize_image(cls, data: bytes, max_dim: int) -> bytes:
        try:
            image = Image.open(io.BytesIO(data))
            if image.width > max_dim or image.height > max_dim:
                image.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                # Convert to RGB if needed (JPEG doesn't support RGBA)
                if image.mode in ("RGBA", "P"):
                    image = image.convert("RGB")
                out_buf = io.BytesIO()
                image.save(out_buf, format="JPEG", quality=85)
                return out_buf.getvalue()
        except Exception:
            pass
        return data

    @classmethod
    def _embed_artwork(cls, file_path: str, data: bytes):
        ext = os.path.splitext(file_path)[1].lower()
        try:
            if ext == ".mp3":
                try:
                    tags = ID3(file_path)
                except ID3Error:
                    tags = ID3()
                tags.delall("APIC")
                tags.add(APIC(
                    encoding=3,
                    mime="image/jpeg",
                    type=3,  # Cover front
                    desc="Cover",
                    data=data
                ))
                tags.save(file_path, v2_version=3)
            elif ext == ".flac":
                audio = FLAC(file_path)
                pic = Picture()
                pic.data = data
                pic.type = 3
                pic.mime = "image/jpeg"
                pic.desc = "Cover"
                audio.clear_pictures()
                audio.add_picture(pic)
                audio.save()
            elif ext in (".m4a", ".aac"):
                audio = MP4(file_path)
                audio["covr"] = [MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG)]
                audio.save()
        except Exception:
            pass


class AlbumArtworkCache:
    """
    Local disk cache for album artwork to allow instantaneous UI rendering
    without continuous network requests to NAS or repeated file tag decoding.
    """
    CACHE_DIR = os.path.expanduser("~/.cache/soundbee/covers")

    @classmethod
    def _ensure_dir(cls):
        os.makedirs(cls.CACHE_DIR, exist_ok=True)

    @classmethod
    def get_cache_key(cls, artist: str, album: str) -> str:
        import hashlib
        raw = f"{artist.strip().lower()}:::{album.strip().lower()}".encode("utf-8")
        return hashlib.md5(raw).hexdigest()[:16]

    @classmethod
    def get_cached_cover_path(cls, artist: str, album: str) -> Optional[str]:
        cls._ensure_dir()
        key = cls.get_cache_key(artist, album)
        p = os.path.join(cls.CACHE_DIR, f"{key}.jpg")
        return p if os.path.isfile(p) and os.path.getsize(p) > 0 else None

    @classmethod
    def get_or_extract_cover_path(
        cls,
        artist: str,
        album: str,
        sample_path: str,
        max_dim: int = 300
    ) -> Optional[str]:
        cached = cls.get_cached_cover_path(artist, album)
        if cached:
            return cached

        raw_bytes = MetadataReader.extract_artwork_bytes(sample_path)
        if not raw_bytes:
            return None

        try:
            cls._ensure_dir()
            image = Image.open(io.BytesIO(raw_bytes))
            if image.width > max_dim or image.height > max_dim:
                image.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            if image.mode in ("RGBA", "P"):
                image = image.convert("RGB")
            key = cls.get_cache_key(artist, album)
            target_path = os.path.join(cls.CACHE_DIR, f"{key}.jpg")
            image.save(target_path, format="JPEG", quality=88)
            return target_path
        except Exception:
            return None

    @classmethod
    def load_cover_async(
        cls,
        artist: str,
        album: str,
        sample_path: str,
        on_loaded_callback,
        max_dim: int = 300
    ):
        import threading
        from gi.repository import GLib

        def worker():
            path = cls.get_or_extract_cover_path(artist, album, sample_path, max_dim=max_dim)
            if path:
                GLib.idle_add(lambda: on_loaded_callback(path))

        threading.Thread(target=worker, daemon=True).start()

