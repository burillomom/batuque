"""
Album artwork processing, resizing, and embedding for Batuque.
"""

import os
import io
from typing import Optional, Any, List
from PIL import Image, ImageFile
from mutagen.id3 import ID3, APIC, error as ID3Error
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover

from .metadata import MetadataReader
from .models import DeviceProfile

ImageFile.LOAD_TRUNCATED_IMAGES = False


class ArtworkProcessor:
    @classmethod
    def process_and_save_artwork(
        cls,
        source_audio_path: str,
        target_audio_path: str,
        profile: DeviceProfile,
        track: Optional[Any] = None,
        force: bool = False
    ) -> bool:
        """
        Extracts, resizes, and either embeds artwork or saves cover.jpg in album directory.
        Supports force overwriting or automatic healing of corrupted/missing covers.
        """
        raw_bytes = None
        if track and getattr(track, "display_artist", None) and getattr(track, "display_album", None):
            cached_path = AlbumArtworkCache.get_cached_cover_path(track.display_artist, track.display_album)
            if cached_path and os.path.isfile(cached_path) and not AlbumArtworkCache.is_corrupted_cover_file(cached_path):
                try:
                    with open(cached_path, "rb") as f:
                        raw_bytes = f.read()
                except Exception:
                    pass

        if not raw_bytes:
            raw_bytes = MetadataReader.extract_artwork_bytes(source_audio_path)

        if not raw_bytes:
            return False

        # Verify source artwork is not corrupted
        try:
            with Image.open(io.BytesIO(raw_bytes)) as check_im:
                check_im.load()
                if AlbumArtworkCache.is_corrupted_artwork(check_im):
                    return False
        except Exception:
            return False

        # Resize image if larger than max_size
        processed_bytes = cls._resize_image(raw_bytes, profile.artwork_max_size)

        target_dir = os.path.dirname(target_audio_path)
        os.makedirs(target_dir, exist_ok=True)

        # 1. Save cover file (e.g. cover.jpg)
        if profile.artwork_save_file:
            art_path = os.path.join(target_dir, profile.artwork_filename)
            should_write = False
            if force:
                should_write = True
            elif not os.path.exists(art_path):
                should_write = True
            elif AlbumArtworkCache.is_corrupted_cover_file(art_path):
                should_write = True
            elif os.path.getsize(art_path) == 0:
                should_write = True

            if should_write:
                try:
                    with open(art_path, "wb") as f:
                        f.write(processed_bytes)
                    AlbumArtworkCache._verified_clean.add(art_path)
                except OSError:
                    pass

        # 2. Embed into target file tags
        if profile.artwork_embed and os.path.isfile(target_audio_path):
            should_embed = force
            if not should_embed:
                existing_art = MetadataReader.extract_artwork_bytes(target_audio_path)
                if not existing_art:
                    should_embed = True
                else:
                    try:
                        with Image.open(io.BytesIO(existing_art)) as im:
                            if AlbumArtworkCache.is_corrupted_artwork(im):
                                should_embed = True
                    except Exception:
                        should_embed = True

            if should_embed:
                cls._embed_artwork(target_audio_path, processed_bytes)

        return True

    @classmethod
    def _resize_image(cls, data: bytes, max_dim: int) -> bytes:
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
            if AlbumArtworkCache.is_corrupted_artwork(image):
                return data
            if image.width > max_dim or image.height > max_dim:
                image.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            if image.mode in ("RGBA", "LA", "P"):
                if "A" in image.mode:
                    bg = Image.new("RGB", image.size, (255, 255, 255))
                    bg.paste(image, mask=image.split()[-1])
                    image = bg
                else:
                    image = image.convert("RGB")
            elif image.mode != "RGB":
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
    CACHE_DIR = os.path.expanduser("~/.cache/batuque/covers")
    LEGACY_CACHE_DIR = os.path.expanduser("~/.cache/soundbee/covers")
    _executor = None
    _lock = None
    _in_flight = None
    _texture_cache = {}
    _verified_clean = set()
    _clean_checked = False

    @classmethod
    def _ensure_init(cls):
        if not os.path.exists(cls.CACHE_DIR):
            parent = os.path.dirname(cls.CACHE_DIR)
            os.makedirs(parent, exist_ok=True)
            if os.path.exists(cls.LEGACY_CACHE_DIR):
                try:
                    os.symlink(cls.LEGACY_CACHE_DIR, cls.CACHE_DIR)
                except Exception:
                    os.makedirs(cls.CACHE_DIR, exist_ok=True)
            else:
                os.makedirs(cls.CACHE_DIR, exist_ok=True)
        else:
            os.makedirs(cls.CACHE_DIR, exist_ok=True)

        import threading
        from concurrent.futures import ThreadPoolExecutor
        if cls._lock is None:
            cls._lock = threading.Lock()
        if cls._in_flight is None:
            cls._in_flight = set()
        if cls._executor is None:
            cls._executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="batuque_art")
        if not cls._clean_checked:
            cls._clean_checked = True
            threading.Thread(target=cls.clean_corrupted_covers, daemon=True, name="art_cleaner").start()

    @staticmethod
    def _is_gray_filler_pixel(p: Any, tolerance: int = 8) -> bool:
        """Checks if a pixel is near neutral gray filler RGB(128, 128, 128)."""
        try:
            r, g, b = p[0], p[1], p[2]
            return (
                abs(r - 128) <= tolerance and
                abs(g - 128) <= tolerance and
                abs(b - 128) <= tolerance and
                abs(r - g) <= 4 and
                abs(g - b) <= 4
            )
        except Exception:
            return False

    @classmethod
    def is_corrupted_artwork(cls, im: Image.Image) -> bool:
        """
        Detects whether an image is corrupted by partial decoding (PIL gray filler RGB(128, 128, 128)
        or JPEG-quantized neutral gray filler around 128 at the bottom).
        """
        try:
            w, h = im.size
            if w < 10 or h < 10:
                return False
            im_rgb = im.convert("RGB")
            step = max(1, w // 20)
            bottom_pixels = [im_rgb.getpixel((x, h - 1)) for x in range(0, w, step)]
            gray_count = sum(1 for p in bottom_pixels if cls._is_gray_filler_pixel(p))
            if gray_count >= len(bottom_pixels) * 0.85:
                # Check several rows from the bottom
                check_rows = min(10, max(2, h // 4))
                consecutive_gray_rows = 0
                sample_step = max(1, w // 10)
                for y in range(h - check_rows, h):
                    row_samples = [im_rgb.getpixel((x, y)) for x in range(0, w, sample_step)]
                    row_gray = sum(1 for p in row_samples if cls._is_gray_filler_pixel(p))
                    if row_gray >= len(row_samples) * 0.85:
                        consecutive_gray_rows += 1
                if consecutive_gray_rows >= max(1, check_rows - 1):
                    return True
            return False
        except Exception:
            return True

    @classmethod
    def is_corrupted_cover_file(cls, path: str) -> bool:
        if path in cls._verified_clean:
            return False
        try:
            with Image.open(path) as im:
                im.load()
                if cls.is_corrupted_artwork(im):
                    return True
            cls._verified_clean.add(path)
            return False
        except Exception:
            return True

    @classmethod
    def clean_corrupted_covers(cls) -> List[str]:
        """
        Scans cache directories for any covers containing gray bars or truncated decode artifacts,
        and deletes them so they can be re-extracted cleanly.
        """
        cls._clean_checked = True
        removed = []
        dirs = [cls.CACHE_DIR]
        if os.path.exists(cls.LEGACY_CACHE_DIR):
            try:
                if not os.path.samefile(cls.CACHE_DIR, cls.LEGACY_CACHE_DIR):
                    dirs.append(cls.LEGACY_CACHE_DIR)
            except Exception:
                dirs.append(cls.LEGACY_CACHE_DIR)

        for d in dirs:
            if not os.path.isdir(d):
                continue
            for fname in os.listdir(d):
                if not fname.endswith(".jpg"):
                    continue
                fpath = os.path.join(d, fname)
                try:
                    if cls.is_corrupted_cover_file(fpath):
                        os.remove(fpath)
                        removed.append(fpath)
                        key = os.path.splitext(fname)[0]
                        cls._texture_cache.pop(key, None)
                        cls._verified_clean.discard(fpath)
                except Exception:
                    pass
        return removed

    @classmethod
    def get_cache_key(cls, artist: str, album: str) -> str:
        import hashlib
        raw = f"{artist.strip().lower()}:::{album.strip().lower()}".encode("utf-8")
        return hashlib.md5(raw).hexdigest()[:16]

    @classmethod
    def get_cached_cover_path(cls, artist: str, album: str) -> Optional[str]:
        cls._ensure_init()
        key = cls.get_cache_key(artist, album)
        p = os.path.join(cls.CACHE_DIR, f"{key}.jpg")
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            if cls.is_corrupted_cover_file(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
            else:
                return p
        if os.path.exists(cls.LEGACY_CACHE_DIR):
            try:
                if not os.path.samefile(cls.CACHE_DIR, cls.LEGACY_CACHE_DIR):
                    leg_p = os.path.join(cls.LEGACY_CACHE_DIR, f"{key}.jpg")
                    if os.path.isfile(leg_p) and os.path.getsize(leg_p) > 0:
                        if cls.is_corrupted_cover_file(leg_p):
                            try:
                                os.remove(leg_p)
                            except Exception:
                                pass
                        else:
                            return leg_p
            except Exception:
                pass
        return None

    @classmethod
    def get_cached_paintable(cls, artist: str, album: str) -> Optional[Any]:
        """Returns in-memory decoded Gdk.Texture if available, or decodes cached file."""
        cls._ensure_init()
        key = cls.get_cache_key(artist, album)
        if key in cls._texture_cache:
            return cls._texture_cache[key]
        p = cls.get_cached_cover_path(artist, album)
        if p:
            try:
                from gi.repository import Gdk
                tex = Gdk.Texture.new_from_filename(p)
                cls._texture_cache[key] = tex
                return tex
            except Exception:
                pass
        return None

    @classmethod
    def get_or_extract_cover_path(
        cls,
        artist: str,
        album: str,
        sample_path_or_paths: Any,
        max_dim: int = 300
    ) -> Optional[str]:
        cached = cls.get_cached_cover_path(artist, album)
        if cached:
            return cached

        paths = sample_path_or_paths if isinstance(sample_path_or_paths, (list, tuple)) else [sample_path_or_paths]
        raw_bytes = None
        for p in paths:
            if not p:
                continue
            raw_bytes = MetadataReader.extract_artwork_bytes(p)
            if raw_bytes:
                break

        if not raw_bytes:
            return None

        try:
            cls._ensure_init()
            image = Image.open(io.BytesIO(raw_bytes))
            image.load()
            if cls.is_corrupted_artwork(image):
                return None
            if image.width > max_dim or image.height > max_dim:
                image.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            if image.mode in ("RGBA", "LA", "P"):
                if "A" in image.mode:
                    bg = Image.new("RGB", image.size, (255, 255, 255))
                    bg.paste(image, mask=image.split()[-1])
                    image = bg
                else:
                    image = image.convert("RGB")
            elif image.mode != "RGB":
                image = image.convert("RGB")
            key = cls.get_cache_key(artist, album)
            target_path = os.path.join(cls.CACHE_DIR, f"{key}.jpg")
            image.save(target_path, format="JPEG", quality=88)
            cls._verified_clean.add(target_path)
            return target_path
        except Exception:
            return None

    @classmethod
    def load_cover_async(
        cls,
        artist: str,
        album: str,
        sample_path_or_paths: Any,
        on_loaded_callback,
        max_dim: int = 300
    ):
        cls._ensure_init()
        from gi.repository import GLib

        tex = cls.get_cached_paintable(artist, album)
        if tex:
            GLib.idle_add(lambda: on_loaded_callback(tex))
            return

        key = cls.get_cache_key(artist, album)
        with cls._lock:
            if key in cls._in_flight:
                return
            cls._in_flight.add(key)

        def worker():
            try:
                path = cls.get_or_extract_cover_path(artist, album, sample_path_or_paths, max_dim=max_dim)
                if path:
                    tex_obj = None
                    try:
                        from gi.repository import Gdk
                        tex_obj = Gdk.Texture.new_from_filename(path)
                        cls._texture_cache[key] = tex_obj
                    except Exception:
                        pass
                    paintable = tex_obj or path
                    GLib.idle_add(lambda: on_loaded_callback(paintable))
            finally:
                with cls._lock:
                    cls._in_flight.discard(key)

        cls._executor.submit(worker)

    @classmethod
    def preload_album_covers(cls, albums: list):
        """Preloads artwork cache in background thread for all albums without blocking UI."""
        cls._ensure_init()
        import threading
        from concurrent.futures import ThreadPoolExecutor

        def preloader():
            to_fetch = []
            for alb in albums:
                cached = cls.get_cached_cover_path(alb["artist"], alb["album"])
                if not cached:
                    paths = alb.get("sample_paths") or ([alb["sample_path"]] if alb.get("sample_path") else [])
                    if paths:
                        to_fetch.append((alb["artist"], alb["album"], paths))

            if not to_fetch:
                return

            def fetch_one(item):
                artist, album, paths = item
                cls.get_or_extract_cover_path(artist, album, paths, max_dim=300)

            try:
                with ThreadPoolExecutor(max_workers=4, thread_name_prefix="batuque_preload") as pool:
                    list(pool.map(fetch_one, to_fetch))
            except (RuntimeError, Exception):
                pass

        threading.Thread(target=preloader, daemon=True, name="art_preloader").start()

