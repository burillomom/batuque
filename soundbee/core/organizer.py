"""
File organization and tag-based naming rule engine for SoundBee.
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

        # Translate MusicBee style <Tag> to {Tag}
        template = rule_template.replace("<Artist>", "{Artist}") \
                                .replace("<Album Artist>", "{AlbumArtist}") \
                                .replace("<Album>", "{Album}") \
                                .replace("<Title>", "{Title}") \
                                .replace("<Year>", "{Year}") \
                                .replace("<Genre>", "{Genre}") \
                                .replace("<Track#>", "{Track:02d}") \
                                .replace("<Disc#>", "{Disc:02d}") \
                                .replace("<ext>", "{ext}")

        # Perform replacement safely
        context = {
            "Artist": artist,
            "AlbumArtist": album_artist,
            "Album": album,
            "Title": title,
            "Year": year_str,
            "Genre": genre,
            "Track": track_no,
            "Disc": disc_no,
            "ext": ext
        }

        try:
            formatted = template.format(**context)
        except Exception:
            # Fallback safe template
            formatted = f"{artist}/{album}/{track_no:02d} - {title}.{ext}"

        # Normalize directory separators
        parts = formatted.replace("\\", "/").split("/")
        sanitized_parts = [cls.sanitize_filename(p) if i == len(parts) - 1 else cls.sanitize_filename(p) for i, p in enumerate(parts) if p]
        
        return os.path.join(*sanitized_parts) if sanitized_parts else f"Unknown/{title}.{ext}"
