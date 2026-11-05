"""
Audio metadata extraction and tag reading for Batuque.
Uses Mutagen with FFprobe fallback.
"""

import os
import io
from typing import Optional, Tuple, Any
import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from .models import Track


class GioFileStream(io.RawIOBase):
    """
    Adapter that wraps a Gio.FileInputStream into a standard Python seekable RawIOBase.
    Allows Mutagen to stream and seek tags directly from SMB/network shares without
    downloading the entire audio file or failing on large embedded artwork blocks.
    """
    def __init__(self, gfile: Any):
        self.stream = gfile.read(None)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return hasattr(self.stream, "can_seek") and self.stream.can_seek()

    def readinto(self, b) -> int:
        res = self.stream.read_bytes(len(b), None)
        data = res.get_data()
        if not data:
            return 0
        b[:len(data)] = data
        return len(data)

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        from gi.repository import GLib
        type_map = {
            io.SEEK_SET: GLib.SeekType.SET,
            io.SEEK_CUR: GLib.SeekType.CUR,
            io.SEEK_END: GLib.SeekType.END,
        }
        self.stream.seek(offset, type_map.get(whence, GLib.SeekType.SET), None)
        return self.stream.tell()

    def tell(self) -> int:
        return self.stream.tell()

    def close(self):
        try:
            self.stream.close(None)
        except Exception:
            pass
        super().close()


class MetadataReader:
    SUPPORTED_EXTENSIONS = {
        ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus",
        ".wav", ".aiff", ".aif", ".alac", ".wma", ".dsf", ".dff",
        ".ape", ".wv", ".m4b"
    }

    @classmethod
    def is_supported(cls, file_path: str) -> bool:
        ext = os.path.splitext(file_path)[1].lower()
        return ext in cls.SUPPORTED_EXTENSIONS

    @classmethod
    def read_track(cls, file_path: str, source_id: str = "local") -> Optional[Track]:
        if not os.path.isfile(file_path) or not cls.is_supported(file_path):
            return None

        stat = os.stat(file_path)
        ext = os.path.splitext(file_path)[1].lower()
        track = Track(
            file_path=os.path.abspath(file_path),
            file_format=ext.lstrip("."),
            size_bytes=stat.st_size,
            mtime=stat.st_mtime,
            source_id=source_id
        )

        try:
            audio = mutagen.File(file_path)
            if audio is None:
                return track

            # Extract audio technical stream properties
            if hasattr(audio, "info") and audio.info is not None:
                track.duration_seconds = getattr(audio.info, "length", 0.0)
                bitrate = getattr(audio.info, "bitrate", 0)
                track.bitrate_kbps = int(bitrate / 1000) if bitrate else 0
                track.sample_rate = getattr(audio.info, "sample_rate", 44100)

            # Extract tag metadata
            cls._extract_tags(audio, ext, track)
            track.has_artwork = cls._has_artwork(audio, ext, file_path)

        except Exception:
            # If tags fail to parse, fallback to filename guessing
            base = os.path.splitext(os.path.basename(file_path))[0]
            track.title = base

        cls.infer_tags_from_path(track)
        return track

    @classmethod
    def infer_tags_from_path(cls, track: Track):
        """Infers artist, album, year, track number from directory/file structure if missing."""
        import urllib.parse
        import re

        path = urllib.parse.unquote(track.file_path)
        parts = [p for p in path.replace("\\", "/").rstrip("/").split("/") if p]
        if not parts:
            return

        filename = parts[-1]
        album_folder = parts[-2] if len(parts) >= 2 else ""
        artist_folder = parts[-3] if len(parts) >= 3 else ""

        # 1. Infer Track number and Title from filename
        name_no_ext = os.path.splitext(filename)[0]
        if not track.track_number or not track.title or track.title == name_no_ext:
            m = re.match(r"^(\d{1,3})\s*[-_.\s]\s*(.+)$", name_no_ext)
            if m:
                if not track.track_number:
                    try:
                        track.track_number = int(m.group(1))
                    except ValueError:
                        pass
                if not track.title or track.title == name_no_ext:
                    track.title = m.group(2).strip()

        # 2. Infer Year and Album from album folder
        if not track.album and album_folder:
            m = re.match(r"^[\(\[]?(\d{4})[\)\]]?\s*[-_.\s]\s*(.+)$", album_folder)
            if m:
                if not track.year:
                    track.year = int(m.group(1))
                track.album = m.group(2).strip()
            else:
                track.album = album_folder

        # 3. Infer Artist from artist folder
        if not track.artist and artist_folder:
            if artist_folder.lower() not in ("musica", "music", "songs", "audio", "nas-4tb", "nas", "share"):
                track.artist = artist_folder

    @classmethod
    def read_track_from_gio(cls, uri_or_gfile: Any, source_id: str = "nas") -> Optional[Track]:
        """Reads audio track metadata from a GIO file or URI (e.g. smb://)."""
        try:
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio
        except Exception:
            return None

        gfile = uri_or_gfile if hasattr(uri_or_gfile, "query_info") else Gio.File.new_for_uri(str(uri_or_gfile))
        uri = gfile.get_uri()
        basename = gfile.get_basename()
        ext = os.path.splitext(basename)[1].lower()
        if ext not in cls.SUPPORTED_EXTENSIONS:
            return None

        try:
            info = gfile.query_info("standard::name,standard::size,time::modified", Gio.FileQueryInfoFlags.NONE, None)
            size = info.get_size()
            dt = info.get_modification_date_time()
            mtime = dt.to_unix() if dt else 0.0
        except Exception:
            size = 0
            mtime = 0.0

        track = Track(
            file_path=uri,
            file_format=ext.lstrip("."),
            size_bytes=size,
            mtime=float(mtime),
            source_id=source_id
        )

        f = None
        try:
            f = io.BufferedReader(GioFileStream(gfile))
            audio = mutagen.File(f, filename=basename)
            if audio:
                if hasattr(audio, "info") and audio.info is not None:
                    track.duration_seconds = getattr(audio.info, "length", 0.0)
                    bitrate = getattr(audio.info, "bitrate", 0)
                    track.bitrate_kbps = int(bitrate / 1000) if bitrate else 0
                    track.sample_rate = getattr(audio.info, "sample_rate", 44100)
                cls._extract_tags(audio, ext, track)
                track.has_artwork = cls._has_artwork(audio, ext, uri)
            else:
                track.title = os.path.splitext(basename)[0]
        except Exception:
            # Fallback gracefully to filename and path deduction so NO track is ever dropped
            track.title = os.path.splitext(basename)[0]
        finally:
            if f is not None:
                try:
                    f.close()
                except Exception:
                    pass

        cls.infer_tags_from_path(track)
        return track

    @classmethod
    def _extract_tags(cls, audio: Any, ext: str, track: Track):
        tags = audio.tags
        if not tags:
            base = os.path.splitext(os.path.basename(track.file_path))[0]
            track.title = base
            return

        def get_val(keys: list, default: str = "") -> str:
            for k in keys:
                if k in tags and tags[k]:
                    val = tags[k]
                    if isinstance(val, list) and val:
                        return str(val[0])
                    return str(val)
            return default

        track.title = get_val(["title", "TIT2", "\xa9nam"]) or os.path.splitext(os.path.basename(track.file_path))[0]
        track.artist = get_val(["artist", "TPE1", "\xa9ART"])
        track.album_artist = get_val(["albumartist", "album artist", "TPE2", "aART"])
        track.album = get_val(["album", "TALB", "\xa9alb"])
        track.genre = get_val(["genre", "TCON", "\xa9gen"])

        # Year / Date
        date_str = get_val(["date", "year", "TDRC", "TYER", "\xa9day"])
        if date_str:
            try:
                # Take first 4 digits
                digits = "".join(filter(str.isdigit, date_str[:4]))
                if len(digits) == 4:
                    track.year = int(digits)
            except ValueError:
                pass

        # Track number and Disc number
        track_raw = get_val(["tracknumber", "TRCK", "trkn"])
        if track_raw:
            try:
                if isinstance(track_raw, tuple):
                    track.track_number = int(track_raw[0])
                else:
                    track.track_number = int(str(track_raw).split("/")[0])
            except (ValueError, IndexError):
                pass

        disc_raw = get_val(["discnumber", "TPOS", "disk"])
        if disc_raw:
            try:
                if isinstance(disc_raw, tuple):
                    track.disc_number = int(disc_raw[0])
                else:
                    track.disc_number = int(str(disc_raw).split("/")[0])
            except (ValueError, IndexError):
                pass

    @classmethod
    def _is_image_file(cls, filename: str) -> bool:
        ext = os.path.splitext(filename)[1].lower()
        return ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp")

    @classmethod
    def _is_cover_image_name(cls, filename: str) -> bool:
        stem = os.path.splitext(filename)[0].lower()
        if not cls._is_image_file(filename):
            return False
        return (
            stem in ("cover", "folder", "front", "album", "artwork", "front_cover", "sleeve")
            or "cover" in stem
            or "front" in stem
            or stem.startswith("00")
        )

    @classmethod
    def _has_artwork(cls, audio: Any, ext: str, file_path: str) -> bool:
        # Check embedded artwork
        if ext == ".mp3":
            if audio.tags:
                for key in audio.tags.keys():
                    if key.startswith("APIC"):
                        return True
        elif ext == ".flac":
            if hasattr(audio, "pictures") and len(audio.pictures) > 0:
                return True
        elif ext in (".m4a", ".aac"):
            if audio.tags and "covr" in audio.tags and audio.tags["covr"]:
                return True

        # Check adjacent artwork file for local paths
        if not file_path.startswith(("smb://", "nfs://", "sftp://", "davs://", "ftp://")):
            try:
                dir_path = os.path.dirname(file_path)
                if os.path.isdir(dir_path):
                    for name in os.listdir(dir_path):
                        if cls._is_cover_image_name(name):
                            return True
            except Exception:
                pass

        return False

    @classmethod
    def extract_artwork_bytes(cls, file_path: str) -> Optional[bytes]:
        """Extract album artwork bytes from file tags or adjacent folder image (supports local and GIO URIs)."""
        if file_path.startswith(("smb://", "nfs://", "sftp://", "davs://", "ftp://")):
            return cls._extract_artwork_gio(file_path)
        return cls._extract_artwork_local(file_path)

    @classmethod
    def _extract_artwork_gio(cls, file_path: str) -> Optional[bytes]:
        try:
            import io
            import base64
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio
            gfile = Gio.File.new_for_uri(file_path)

            # 1. Check parent directory (and grandparent for multi-disc folders) for cover image
            candidate_dirs = []
            parent = gfile.get_parent()
            if parent:
                candidate_dirs.append(parent)
                pname = parent.get_basename().lower()
                if any(pname.startswith(p) for p in ("cd", "disc", "disk", "side", "vol")):
                    gp = parent.get_parent()
                    if gp:
                        candidate_dirs.append(gp)

            for folder in candidate_dirs:
                try:
                    enumerator = folder.enumerate_children(
                        "standard::name,standard::type",
                        Gio.FileQueryInfoFlags.NONE,
                        None
                    )
                    image_files = []
                    priority_cover = None
                    for info in enumerator:
                        name = info.get_name()
                        if info.get_file_type() == Gio.FileType.REGULAR and cls._is_image_file(name):
                            if cls._is_cover_image_name(name):
                                priority_cover = name
                                break
                            image_files.append(name)

                    selected_name = priority_cover or (image_files[0] if len(image_files) == 1 else None)
                    if selected_name:
                        cand = folder.get_child(selected_name)
                        glib_bytes, _ = cand.load_bytes(None)
                        if glib_bytes:
                            data = glib_bytes.get_data()
                            if data:
                                return bytes(data)
                except Exception:
                    pass

            # 2. Extract embedded tags from stream via GioFileStream without truncation
            f = io.BufferedReader(GioFileStream(gfile))
            try:
                audio = mutagen.File(f, filename=gfile.get_basename())
                if audio:
                    if hasattr(audio, "pictures") and audio.pictures:
                        return bytes(audio.pictures[0].data)
                    if hasattr(audio, "tags") and audio.tags:
                        if "covr" in audio.tags and audio.tags["covr"]:
                            return bytes(audio.tags["covr"][0])
                        if "metadata_block_picture" in audio.tags:
                            try:
                                pic_data = base64.b64decode(audio.tags["metadata_block_picture"][0])
                                return bytes(Picture(pic_data).data)
                            except Exception:
                                pass
                        for val in audio.tags.values():
                            if isinstance(val, APIC):
                                return bytes(val.data)
            except Exception:
                pass
            finally:
                f.close()

        except Exception:
            pass
        return None

    @classmethod
    def _extract_artwork_local(cls, file_path: str) -> Optional[bytes]:
        ext = os.path.splitext(file_path)[1].lower()

        # 1. Check parent folders for cover image
        try:
            parent_dir = os.path.dirname(file_path)
            candidate_dirs = [parent_dir]
            pname = os.path.basename(parent_dir).lower()
            if any(pname.startswith(p) for p in ("cd", "disc", "disk", "side", "vol")):
                candidate_dirs.append(os.path.dirname(parent_dir))

            for d in candidate_dirs:
                if not os.path.isdir(d):
                    continue
                entries = os.listdir(d)
                priority_cover = None
                img_entries = []
                for name in entries:
                    if cls._is_cover_image_name(name):
                        priority_cover = name
                        break
                    elif cls._is_image_file(name):
                        img_entries.append(name)

                chosen = priority_cover or (img_entries[0] if len(img_entries) == 1 else None)
                if chosen:
                    img_path = os.path.join(d, chosen)
                    try:
                        with open(img_path, "rb") as f:
                            data = f.read()
                            if data:
                                return data
                    except OSError:
                        pass
        except Exception:
            pass

        # 2. Check embedded audio tags
        try:
            if ext in (".mp3", ".aiff", ".aif"):
                try:
                    id3 = ID3(file_path)
                    for val in id3.values():
                        if isinstance(val, APIC):
                            return val.data
                except Exception:
                    pass

            if ext == ".flac":
                try:
                    flac_obj = FLAC(file_path)
                    if hasattr(flac_obj, "pictures") and flac_obj.pictures:
                        return flac_obj.pictures[0].data
                except Exception:
                    pass

            audio = mutagen.File(file_path)
            if audio:
                if hasattr(audio, "pictures") and audio.pictures:
                    return audio.pictures[0].data
                if hasattr(audio, "tags") and audio.tags:
                    if "covr" in audio.tags and audio.tags["covr"]:
                        return bytes(audio.tags["covr"][0])
                    if "metadata_block_picture" in audio.tags:
                        try:
                            import base64
                            pic_data = base64.b64decode(audio.tags["metadata_block_picture"][0])
                            return bytes(Picture(pic_data).data)
                        except Exception:
                            pass
                    for val in audio.tags.values():
                        if isinstance(val, APIC):
                            return bytes(val.data)
        except Exception:
            pass

        return None
