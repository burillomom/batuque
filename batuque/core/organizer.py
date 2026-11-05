"""
File organization and tag-based naming rule engine for Batuque.
Mirrors MusicBee's Naming Rules while sanitizing for FAT32 / exFAT / Linux filesystems.
"""

import os
import re
from typing import Optional
from .models import Track


class FileOrganizer:
    # Characters invalid in FAT32, exFAT, NTFS, and portable player filesystems
    INVALID_CHARS_REGEX = re.compile(r'[\x00-\x1f<>:"/\\|?*]')

    @classmethod
    def sanitize_filename(cls, text: str, replace_with: str = "_") -> str:
        """Sanitizes filename segment to be safe for all filesystems."""
        if not text:
            return "Unknown"
        clean = cls.INVALID_CHARS_REGEX.sub(replace_with, text)
        # Avoid trailing dots/spaces which break FAT32/Windows
        clean = clean.strip(" .")
        return clean or "Unknown"

    @classmethod
    def format_relative_path(cls, track: Track, rule_template: str, target_extension: Optional[str] = None) -> str:
        """
        Formats a target relative path based on track metadata and template.
        Supports both {Tag} and <Tag> styles:
        Examples:
          "{Artist}/{Album}/{Track:02d} - {Title}.{ext}"
          "<Artist>/<Album>/<Track#> - <Title>.<ext>"
        """
        ext = target_extension or track.file_format or "mp3"
        ext = ext.lstrip(".")

        artist = cls.sanitize_filename(track.artist or "Unknown Artist")
        album_artist = cls.sanitize_filename(track.album_artist or track.artist or "Unknown Artist")
        album = cls.sanitize_filename(track.album or "Unknown Album")
        title = cls.sanitize_filename(track.title or "Unknown Title")
        genre = cls.sanitize_filename(track.genre or "Unknown Genre")
        year_str = str(track.year) if track.year else "0000"
        track_no = track.track_number or 1
        disc_no = track.disc_number or 1

        # Translate MusicBee style <Tag> to {Tag} and normalize spacing variants
        template = rule_template.replace("<Artist>", "{Artist}") \
                                .replace("<Album Artist>", "{AlbumArtist}") \
                                .replace("{Album Artist}", "{AlbumArtist}") \
                                .replace("<Album>", "{Album}") \
                                .replace("<Title>", "{Title}") \
                                .replace("<Year>", "{Year}") \
                                .replace("<Genre>", "{Genre}") \
                                .replace("<Track#>", "{Track:02d}") \
                                .replace("<Disc#>", "{Disc:02d}") \
                                .replace("<ext>", "{ext}")

        # Perform replacement safely with case and spacing variants
        context = {
            "Artist": artist,
            "artist": artist,
            "AlbumArtist": album_artist,
            "Album Artist": album_artist,
            "album_artist": album_artist,
            "albumartist": album_artist,
            "Album": album,
            "album": album,
            "Title": title,
            "title": title,
            "Year": year_str,
            "year": year_str,
            "Genre": genre,
            "genre": genre,
            "Track": track_no,
            "track": track_no,
            "Disc": disc_no,
            "disc": disc_no,
            "ext": ext
        }

        try:
            formatted = template.format(**context)
        except Exception:
            # Fallback safe template
            formatted = f"{artist}/{album}/{track_no:02d} - {title}.{ext}"

        # Normalize directory separators
        parts = formatted.replace("\\", "/").split("/")
        sanitized_parts = [cls.sanitize_filename(p) for p in parts if p]
        
        return os.path.join(*sanitized_parts) if sanitized_parts else f"Unknown/{title}.{ext}"

    @classmethod
    def get_preserved_relative_path(
        cls,
        track_path: str,
        source_roots: Optional[list] = None,
        target_extension: Optional[str] = None
    ) -> str:
        """
        Determines the original relative path of a track with respect to its library / NAS source root.
        Sanitizes directory segments and filenames for portable FAT32/exFAT filesystems.
        """
        import urllib.parse
        unquoted = urllib.parse.unquote(track_path)
        rel = ""

        # Check against known source roots
        if source_roots:
            for s_root in source_roots:
                if not s_root:
                    continue
                unq_root = urllib.parse.unquote(s_root).rstrip("/\\")
                norm_track = unquoted.replace("\\", "/")
                norm_root = unq_root.replace("\\", "/")
                if norm_track.lower().startswith(norm_root.lower() + "/"):
                    rel = norm_track[len(norm_root):].lstrip("/")
                    break
                elif norm_track.lower().startswith(norm_root.lower()):
                    rel = norm_track[len(norm_root):].lstrip("/")
                    break

        if not rel:
            # Fallback for SMB URIs: smb://host/share/subfolder/...
            if unquoted.startswith(("smb://", "nfs://", "sftp://", "davs://", "ftp://")):
                parsed = urllib.parse.urlparse(unquoted)
                path_parts = [p for p in parsed.path.split("/") if p]
                # If path has at least: share / [subfolder] / artist / album / track
                if len(path_parts) > 2:
                    # Strip share name
                    rel = "/".join(path_parts[1:])
                elif path_parts:
                    rel = "/".join(path_parts)
            else:
                # Local path: use parent2/parent1/filename if possible
                norm = unquoted.replace("\\", "/")
                parts = [p for p in norm.split("/") if p]
                if len(parts) >= 3:
                    rel = "/".join(parts[-3:])
                elif len(parts) >= 2:
                    rel = "/".join(parts[-2:])
                else:
                    rel = parts[-1] if parts else "song.mp3"

        # Sanitize segments for portable filesystems (FAT32, exFAT, etc.)
        segments = [cls.sanitize_filename(p) for p in rel.replace("\\", "/").split("/") if p]
        if not segments:
            segments = ["Unknown", f"song.{target_extension or 'mp3'}"]

        # Adjust target extension if needed
        if target_extension:
            target_ext = target_extension.lstrip(".")
            stem = os.path.splitext(segments[-1])[0]
            segments[-1] = f"{stem}.{target_ext}"

        return os.path.join(*segments)
