"""
Device Content Inspector and Artwork Optimizer for Batuque.
Allows inspecting tracks/albums present on connected DAPs/iPods/USBs,
detecting oversized covers, and optimizing them in-place.
"""

import os
import io
import glob
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Callable
from PIL import Image

from ..core.models import DeviceProfile
from ..core.metadata import MetadataReader
from ..core.artwork import ArtworkProcessor, AlbumArtworkCache


AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma", ".alac", ".aiff"}
IMAGE_NAMES = ("cover.jpg", "folder.jpg", "cover.png", "folder.png", "albumart.jpg", "front.jpg")


@dataclass
class DeviceAlbum:
    artist: str
    album: str
    folder_path: str
    track_count: int
    total_size_bytes: int
    cover_path: Optional[str] = None
    cover_width: int = 0
    cover_height: int = 0
    cover_size_bytes: int = 0
    has_embedded_cover: bool = False
    is_oversized: bool = False
    has_corrupted_cover: bool = False
    tracks: List[str] = field(default_factory=list)

    @property
    def display_cover_info(self) -> str:
        if self.has_corrupted_cover:
            if self.cover_width > 0 and self.cover_height > 0:
                return f"⚠️ Capa Corrompida ({self.cover_width}x{self.cover_height})"
            return "⚠️ Capa Corrompida"
        if self.cover_width > 0 and self.cover_height > 0:
            kb = self.cover_size_bytes / 1024
            size_str = f"{kb:.1f} KB" if kb < 1024 else f"{kb/1024:.2f} MB"
            return f"{self.cover_width}x{self.cover_height} ({size_str})"
        elif self.has_embedded_cover:
            return "Embutida nas faixas"
        return "Sem capa"


class DeviceInspector:
    @staticmethod
    def is_writable(profile: DeviceProfile) -> bool:
        if not profile.root_path or not os.path.exists(profile.root_path):
            return False
        return os.access(profile.root_path, os.W_OK)

    @classmethod
    def scan_device_albums(cls, profile: DeviceProfile) -> List[DeviceAlbum]:
        """
        Scans music directories on the device and returns discovered albums with artwork info.
        Gracefully handles file system I/O errors or read-only mounts.
        """
        if not profile.root_path or not os.path.isdir(profile.root_path):
            return []

        # Target search paths: subfolder first, or fallback to root folders
        dest_subfolder = os.path.join(profile.root_path, profile.music_subfolder)
        search_dirs = []
        if os.path.isdir(dest_subfolder):
            search_dirs.append(dest_subfolder)
        else:
            search_dirs.append(profile.root_path)

        folder_to_tracks: Dict[str, List[str]] = {}

        for base_dir in search_dirs:
            try:
                for root, dirs, files in os.walk(base_dir, onerror=lambda e: None):
                    # Skip common non-music system folders
                    base_name = os.path.basename(root).lower()
                    if base_name in (".rockbox", ".trash-1000", "system volume information", "lost+found", "ipod_control/device"):
                        continue

                    audio_files = []
                    for f in files:
                        ext = os.path.splitext(f)[1].lower()
                        if ext in AUDIO_EXTENSIONS:
                            audio_files.append(os.path.join(root, f))

                    if audio_files:
                        folder_to_tracks[root] = sorted(audio_files)
            except (OSError, PermissionError):
                continue

        albums: List[DeviceAlbum] = []
        max_dim = profile.artwork_max_size or 500

        for folder, track_paths in folder_to_tracks.items():
            try:
                total_size = sum(os.path.getsize(p) for p in track_paths if os.path.isfile(p))
            except OSError:
                total_size = 0

            # Read metadata of first track for artist & album tags
            first_meta = None
            if track_paths:
                try:
                    first_meta = MetadataReader.read_track(track_paths[0])
                except Exception:
                    pass

            if first_meta and (first_meta.album or first_meta.artist):
                album_name = first_meta.album or os.path.basename(folder)
                artist_name = first_meta.album_artist or first_meta.artist or "Artista Desconhecido"
            else:
                album_name = os.path.basename(folder)
                parent_dir = os.path.dirname(folder)
                artist_name = os.path.basename(parent_dir) if parent_dir != profile.root_path else "Vários Artistas"

            # Check for folder cover image
            cover_file = None
            for name in IMAGE_NAMES:
                candidate = os.path.join(folder, name)
                if os.path.isfile(candidate):
                    cover_file = candidate
                    break

            cover_w, cover_h, cover_bytes = 0, 0, 0
            has_embedded = False
            is_oversized = False
            has_corrupted = False

            if cover_file:
                try:
                    cover_bytes = os.path.getsize(cover_file)
                    if cover_bytes == 0 or AlbumArtworkCache.is_corrupted_cover_file(cover_file):
                        has_corrupted = True
                    with Image.open(cover_file) as img:
                        cover_w, cover_h = img.size
                        if AlbumArtworkCache.is_corrupted_artwork(img):
                            has_corrupted = True
                    if cover_w > max_dim or cover_h > max_dim or cover_bytes > 200 * 1024:
                        is_oversized = True
                except Exception:
                    has_corrupted = True

            # Also check embedded artwork in tracks
            if track_paths:
                raw_art = MetadataReader.extract_artwork_bytes(track_paths[0])
                if raw_art:
                    has_embedded = True
                    if not cover_file:
                        cover_bytes = len(raw_art)
                    try:
                        with Image.open(io.BytesIO(raw_art)) as img:
                            if not cover_file:
                                cover_w, cover_h = img.size
                            if AlbumArtworkCache.is_corrupted_artwork(img):
                                has_corrupted = True
                            if not cover_file and (cover_w > max_dim or cover_h > max_dim or cover_bytes > 200 * 1024):
                                is_oversized = True
                    except Exception:
                        has_corrupted = True

            albums.append(DeviceAlbum(
                artist=artist_name,
                album=album_name,
                folder_path=folder,
                track_count=len(track_paths),
                total_size_bytes=total_size,
                cover_path=cover_file,
                cover_width=cover_w,
                cover_height=cover_h,
                cover_size_bytes=cover_bytes,
                has_embedded_cover=has_embedded,
                is_oversized=is_oversized,
                has_corrupted_cover=has_corrupted,
                tracks=track_paths
            ))

        albums.sort(key=lambda a: (a.artist.lower(), a.album.lower()))
        return albums

    @classmethod
    def optimize_album_artwork(cls, album: DeviceAlbum, max_dim: int = 300) -> bool:
        """
        Reduces cover image dimensions and file size in place for an album on the device.
        """
        changed = False

        # 1. Resize folder cover file if present
        if album.cover_path and os.path.isfile(album.cover_path):
            try:
                orig_size = os.path.getsize(album.cover_path)
                with Image.open(album.cover_path) as img:
                    if img.width > max_dim or img.height > max_dim or orig_size > 150 * 1024:
                        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                        if img.mode != "RGB":
                            img = img.convert("RGB")
                        out_buf = io.BytesIO()
                        img.save(out_buf, format="JPEG", quality=85)
                        new_data = out_buf.getvalue()
                        with open(album.cover_path, "wb") as f:
                            f.write(new_data)
                        album.cover_width = img.width
                        album.cover_height = img.height
                        album.cover_size_bytes = len(new_data)
                        album.is_oversized = False
                        changed = True
            except Exception:
                pass

        # 2. Re-embed in audio files if embedded
        for track_path in album.tracks:
            try:
                raw_bytes = MetadataReader.extract_artwork_bytes(track_path)
                if not raw_bytes:
                    continue
                with Image.open(io.BytesIO(raw_bytes)) as img:
                    if img.width > max_dim or img.height > max_dim or len(raw_bytes) > 150 * 1024:
                        resized = ArtworkProcessor._resize_image(raw_bytes, max_dim)
                        ArtworkProcessor._embed_artwork(track_path, resized)
                        changed = True
            except Exception:
                continue

        return changed

    @classmethod
    def optimize_all_device_artwork(
        cls,
        profile: DeviceProfile,
        on_progress: Optional[Callable[[int, int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Optimizes all oversized covers on the device.
        """
        albums = cls.scan_device_albums(profile)
        oversized = [a for a in albums if a.is_oversized]
        total = len(oversized)
        optimized_count = 0
        max_dim = profile.artwork_max_size or 300

        for i, album in enumerate(oversized):
            if on_progress:
                on_progress(i + 1, total, f"{album.artist} - {album.album}")
            if cls.optimize_album_artwork(album, max_dim=max_dim):
                optimized_count += 1

        return {
            "total_albums": len(albums),
            "oversized_found": total,
            "optimized_count": optimized_count
        }

    @classmethod
    def repair_album_artwork(cls, album: DeviceAlbum, profile: DeviceProfile, force: bool = False) -> bool:
        """
        Repairs corrupted or missing album artwork for an album on the device.
        Retrieves clean artwork from local cache or library database, resizes it according
        to device profile specs, overwrites cover.jpg and re-embeds clean tags into tracks.
        """
        clean_bytes = None

        # 1a. Try AlbumArtworkCache
        cached_path = AlbumArtworkCache.get_cached_cover_path(album.artist, album.album)
        if cached_path and os.path.isfile(cached_path) and not AlbumArtworkCache.is_corrupted_cover_file(cached_path):
            try:
                with open(cached_path, "rb") as f:
                    clean_bytes = f.read()
            except Exception:
                pass

        # 1b. Try LibraryDatabase to find source tracks
        if not clean_bytes:
            try:
                from ..core.database import LibraryDatabase
                from ..core.nas import NASManager
                db = LibraryDatabase()
                lib_tracks = db.get_tracks_sorted(filter_artist=album.artist, filter_album=album.album)
                if not lib_tracks:
                    lib_tracks = db.get_tracks_sorted(filter_album=album.album)
                for lt in lib_tracks:
                    if lt.file_path.startswith("smb://"):
                        try:
                            nas_sources = db.get_nas_sources()
                            for src in nas_sources:
                                NASManager.mount_share(src)
                        except Exception:
                            pass
                    candidate_bytes = MetadataReader.extract_artwork_bytes(lt.file_path)
                    if candidate_bytes:
                        try:
                            with Image.open(io.BytesIO(candidate_bytes)) as im:
                                im.load()
                                if not AlbumArtworkCache.is_corrupted_artwork(im):
                                    clean_bytes = candidate_bytes
                                    AlbumArtworkCache.get_or_extract_cover_path(album.artist, album.album, lt.file_path)
                                    break
                        except Exception:
                            continue
            except Exception:
                pass

        # 1c. Try device audio tracks if embedded is clean
        if not clean_bytes and album.tracks:
            for tp in album.tracks:
                dev_bytes = MetadataReader.extract_artwork_bytes(tp)
                if dev_bytes:
                    try:
                        with Image.open(io.BytesIO(dev_bytes)) as im:
                            im.load()
                            if not AlbumArtworkCache.is_corrupted_artwork(im):
                                clean_bytes = dev_bytes
                                break
                    except Exception:
                        continue

        if not clean_bytes:
            return False

        max_dim = profile.artwork_max_size or 500
        resized_bytes = ArtworkProcessor._resize_image(clean_bytes, max_dim)

        # 2. Overwrite / create cover.jpg on device
        art_filename = profile.artwork_filename or "cover.jpg"
        target_art_path = os.path.join(album.folder_path, art_filename)
        try:
            with open(target_art_path, "wb") as f:
                f.write(resized_bytes)
            album.cover_path = target_art_path
            album.cover_size_bytes = len(resized_bytes)
            with Image.open(target_art_path) as im:
                album.cover_width, album.cover_height = im.size
            AlbumArtworkCache._verified_clean.add(target_art_path)
        except Exception:
            pass

        # Also replace any other corrupted cover images in the folder
        for img_name in IMAGE_NAMES:
            other_img = os.path.join(album.folder_path, img_name)
            if os.path.isfile(other_img) and other_img != target_art_path:
                if AlbumArtworkCache.is_corrupted_cover_file(other_img):
                    try:
                        with open(other_img, "wb") as f:
                            f.write(resized_bytes)
                        AlbumArtworkCache._verified_clean.add(other_img)
                    except Exception:
                        pass

        # 3. Re-embed in all tracks if artwork_embed is enabled
        if profile.artwork_embed:
            for tp in album.tracks:
                try:
                    ArtworkProcessor._embed_artwork(tp, resized_bytes)
                except Exception:
                    continue

        album.has_corrupted_cover = False
        album.is_oversized = False
        return True

    @classmethod
    def repair_all_device_artwork(
        cls,
        profile: DeviceProfile,
        force_all: bool = False,
        on_progress: Optional[Callable[[int, int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Scans device albums and repairs all corrupted covers (or all covers if force_all=True).
        """
        albums = cls.scan_device_albums(profile)
        to_repair = albums if force_all else [a for a in albums if a.has_corrupted_cover]
        total = len(to_repair)
        repaired_count = 0
        failed_count = 0

        for i, album in enumerate(to_repair):
            if on_progress:
                on_progress(i + 1, total, f"{album.artist} - {album.album}")
            success = cls.repair_album_artwork(album, profile, force=force_all)
            if success:
                repaired_count += 1
            else:
                failed_count += 1

        return {
            "total_albums": len(albums),
            "corrupted_found": total if not force_all else sum(1 for a in albums if a.has_corrupted_cover),
            "repaired_count": repaired_count,
            "failed_count": failed_count
        }

