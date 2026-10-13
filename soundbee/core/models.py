"""
Data models for SoundBee.
Includes Track, Album, DeviceProfile, and Sync items.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
import json


@dataclass
class Track:
    id: Optional[int] = None
    file_path: str = ""
    title: str = ""
    artist: str = ""
    album_artist: str = ""
    album: str = ""
    genre: str = ""
    year: Optional[int] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = 1
    duration_seconds: float = 0.0
    bitrate_kbps: int = 0
    sample_rate: int = 44100
    file_format: str = ""
    size_bytes: int = 0
    mtime: float = 0.0
    has_artwork: bool = False
    source_id: str = "local"  # "local", "nas", etc.

    @property
    def display_artist(self) -> str:
        return self.album_artist or self.artist or "Unknown Artist"

    @property
    def display_title(self) -> str:
        return self.title or "Unknown Title"

    @property
    def display_album(self) -> str:
        return self.album or "Unknown Album"


@dataclass
class NASSource:
    id: str = ""
    name: str = "My NAS"
    uri: str = ""  # e.g., "smb://192.168.1.50/music", "/mnt/nas/music", "nfs://nas/music"
    mount_path: str = ""  # resolved local path (e.g. /run/user/1000/gvfs/... or /mnt/nas/...)
    username: Optional[str] = None
    password: Optional[str] = None
    auto_connect: bool = True
    last_scanned: Optional[float] = None
    total_tracks: int = 0


@dataclass
class DeviceProfile:
    """
    Per-device configuration profile mirroring MusicBee's DeviceSettings.
    Allows distinct configuration for every DAP, phone, USB drive, or virtual device.
    """
    device_id: str = ""  # UUID, mount identifier, or custom ID
    name: str = "Portable Device"
    device_type: str = "usb"  # 'usb', 'mtp', 'virtual', 'folder'
    root_path: str = ""  # Mount point or directory path
    enabled: bool = True
    auto_sync: bool = False

    # Synchronization scope
    sync_all_library: bool = True
    selected_playlists: List[str] = field(default_factory=list)
    selected_folders: List[str] = field(default_factory=list)
    music_filter: str = ""  # Optional query filter

    # Destination paths & file organization
    music_subfolder: str = "Music"
    naming_rule: str = "{Artist}/{Album}/{Track:02d} - {Title}.{ext}"
    preserve_filenames: bool = False

    # Audio format conversion & transcoding (FFMPEG)
    transcode_enabled: bool = True
    target_codec: str = "mp3"  # "mp3", "aac", "opus", "flac"
    target_bitrate: int = 320  # kbps (e.g. 320, 256, 192, 128)
    convert_lossless_only: bool = True  # Transcode FLAC/WAV to target, keep existing MP3
    convert_unsupported_only: bool = False  # Transcode only if format not in supported_formats
    supported_formats: List[str] = field(default_factory=lambda: ["mp3", "flac", "aac", "m4a", "ogg", "opus", "wav"])
    convert_except_min_bitrate: int = 160  # Skip transcode if original lossy bitrate >= min_bitrate
    volume_leveling: bool = False  # EBU R128 normalization

    # Album artwork management
    artwork_embed: bool = True
    artwork_save_file: bool = True
    artwork_filename: str = "cover.jpg"
    artwork_max_size: int = 600  # Max width/height in pixels

    # Playlist synchronization
    playlist_export: bool = True
    playlist_format: str = "m3u8"  # "m3u8", "m3u"
    playlist_relative: bool = True
    playlist_subfolder: str = "Playlists"
    playlist_path_prefix: str = ""  # e.g. "/storage/emulated/0/Music/" for Android or "../Music/"

    # Pruning & deletion rules
    delete_unmatched_files: bool = False
    confirm_deletion: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeviceProfile":
        # Filter unknown keys to allow graceful upgrades
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> "DeviceProfile":
        return cls.from_dict(json.loads(json_str))


@dataclass
class SyncItem:
    track: Track
    source_path: str
    target_rel_path: str
    target_full_path: str
    action: str  # "copy", "transcode", "skip", "delete"
    reason: str = ""
    estimated_size_bytes: int = 0


@dataclass
class SyncPlan:
    device_profile: DeviceProfile
    items_to_copy: List[SyncItem] = field(default_factory=list)
    items_to_transcode: List[SyncItem] = field(default_factory=list)
    items_to_delete: List[SyncItem] = field(default_factory=list)
    items_to_skip: List[SyncItem] = field(default_factory=list)
    total_transfer_bytes: int = 0
    estimated_target_bytes: int = 0
    device_free_space_bytes: int = 0
