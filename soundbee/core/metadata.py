"""
Audio metadata extraction and tag reading for SoundBee.
Uses Mutagen with FFprobe fallback.
"""

import os
from typing import Optional, Tuple, Any
import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from .models import Track


class MetadataReader:
    SUPPORTED_EXTENSIONS = {
        ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus",
        ".wav", ".aiff", ".aif", ".alac", ".wma", ".dsf", ".dff"
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
        import io
        try:
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio
        except Exception:
            return None

        gfile = uri_or_gfile if hasattr(uri_or_gfile, "query_info") else Gio.File.new_for_uri(str(uri_or_gfile))
        uri = gfile.get_uri()
        ext = os.path.splitext(gfile.get_basename())[1].lower()
        if ext not in cls.SUPPORTED_EXTENSIONS:
            return None

        try:
            info = gfile.query_info("standard::name,standard::size,time::modified", Gio.FileQueryInfoFlags.NONE, None)
            size = info.get_size()
            dt = info.get_modification_date_time()
            mtime = dt.to_unix() if dt else 0.0

            track = Track(
                file_path=uri,
                file_format=ext.lstrip("."),
                size_bytes=size,
                mtime=float(mtime),
                source_id=source_id
            )

            # Read stream
            stream = gfile.read(None)
            data = stream.read_bytes(1024 * 512, None).get_data()
            stream.close()

            audio = mutagen.File(io.BytesIO(data), filename=info.get_name())
            if audio:
                if hasattr(audio, "info") and audio.info is not None:
                    track.duration_seconds = getattr(audio.info, "length", 0.0)
                    bitrate = getattr(audio.info, "bitrate", 0)
                    track.bitrate_kbps = int(bitrate / 1000) if bitrate else 0
                    track.sample_rate = getattr(audio.info, "sample_rate", 44100)
                cls._extract_tags(audio, ext, track)
                track.has_artwork = cls._has_artwork(audio, ext, uri)
            else:
                track.title = os.path.splitext(info.get_name())[0]

            cls.infer_tags_from_path(track)
            return track
        except Exception:
            return None

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
        elif ext == ".m4a":
            if audio.tags and "covr" in audio.tags and audio.tags["covr"]:
                return True

        # Check adjacent artwork file (cover.jpg, folder.jpg)
        dir_path = os.path.dirname(file_path)
        for art_name in ["cover.jpg", "cover.png", "folder.jpg", "folder.png", "album.jpg"]:
            if os.path.isfile(os.path.join(dir_path, art_name)):
                return True

        return False

    @classmethod
    def extract_artwork_bytes(cls, file_path: str) -> Optional[bytes]:
        """Extract album artwork bytes from file tags or adjacent folder image (supports local and GIO URIs)."""
        if file_path.startswith(("smb://", "nfs://", "sftp://", "davs://", "ftp://")):
            try:
                import io
                import gi
                gi.require_version('Gio', '2.0')
                from gi.repository import Gio
                gfile = Gio.File.new_for_uri(file_path)

                # 1. Check parent directory for cover file
                parent = gfile.get_parent()
                if parent:
                    for art_name in ["cover.jpg", "cover.png", "folder.jpg", "folder.png", "album.jpg", "Cover.jpg", "Folder.jpg"]:
                        cand = parent.get_child(art_name)
                        if cand.query_exists(None):
                            try:
                                s = cand.read(None)
                                data = s.read_bytes(1024 * 1024 * 6, None).get_data()
                                s.close()
                                if data:
                                    return bytes(data)
                            except Exception:
                                pass

                # 2. Extract embedded tags from stream
                stream = gfile.read(None)
                data = stream.read_bytes(1024 * 1024 * 2, None).get_data()
                stream.close()
                if data:
                    audio = mutagen.File(io.BytesIO(data), filename=gfile.get_basename())
                    if audio and audio.tags:
                        ext = os.path.splitext(gfile.get_basename())[1].lower()
                        if ext == ".mp3":
                            for key, val in audio.tags.items():
                                if isinstance(val, APIC):
                                    return val.data
                        elif ext == ".flac" and hasattr(audio, "pictures"):
                            if audio.pictures:
                                return audio.pictures[0].data
                        elif ext in (".m4a", ".aac") and "covr" in audio.tags:
                            covr = audio.tags["covr"]
                            if covr and len(covr) > 0:
                                return bytes(covr[0])
            except Exception:
                pass
            return None

        ext = os.path.splitext(file_path)[1].lower()
        try:
            audio = mutagen.File(file_path)
            if audio and audio.tags:
                if ext == ".mp3":
                    for key, val in audio.tags.items():
                        if isinstance(val, APIC):
                            return val.data
                elif ext == ".flac" and hasattr(audio, "pictures"):
                    if audio.pictures:
                        return audio.pictures[0].data
                elif ext == ".m4a" and "covr" in audio.tags:
                    covr = audio.tags["covr"]
                    if covr and len(covr) > 0:
                        return bytes(covr[0])
        except Exception:
            pass

        # Check folder
        dir_path = os.path.dirname(file_path)
        for art_name in ["cover.jpg", "cover.png", "folder.jpg", "folder.png", "album.jpg", "Cover.jpg", "Folder.jpg"]:
            cand = os.path.join(dir_path, art_name)
            if os.path.isfile(cand):
                try:
                    with open(cand, "rb") as f:
                        return f.read()
                except OSError:
                    pass

        return None
