"""
NAS & Network Share Integration for SoundBee on Linux.
Supports:
- Local filesystem mounts (/mnt/nas, /media/nas)
- GIO / GVFS network shares (smb://, nfs://, sftp://, davs://)
- Auto-mount & path resolution to /run/user/<UID>/gvfs/...
"""

import os
import subprocess
from typing import Optional, Tuple
from .models import NASSource

try:
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio
    GIO_AVAILABLE = True
except Exception:
    GIO_AVAILABLE = False


class NASManager:
    @staticmethod
    def is_local_path(uri_or_path: str) -> bool:
        return uri_or_path.startswith("/") and not uri_or_path.startswith("//")

    @classmethod
    def mount_share(cls, source: NASSource) -> Tuple[bool, str, str]:
        """
        Attempts to mount or resolve a NAS share.
        Returns: (success: bool, local_path: str, message: str)
        """
        target = source.uri.strip()

        # Case 1: Direct local path (e.g. NFS / CIFS mounted in /mnt or /media)
        if cls.is_local_path(target):
            if os.path.isdir(target):
                source.mount_path = os.path.abspath(target)
                return True, source.mount_path, "Local mount directory accessible."
            return False, "", f"Directory '{target}' not found or not mounted."

        # Case 2: Windows UNC style path (\\server\share\...)
        if target.startswith("\\\\"):
            # Convert \\server\share to smb://server/share
            parts = target.lstrip("\\").replace("\\", "/").split("/", 1)
            if len(parts) >= 2:
                target = f"smb://{parts[0]}/{parts[1]}"
            else:
                target = f"smb://{parts[0]}"
            source.uri = target

        # Case 3: Network URI (smb://, nfs://, etc.)
        if not GIO_AVAILABLE:
            return False, "", "GIO library not available on this system."

        # Check if already mounted in GVFS
        resolved_path = cls._find_gvfs_mount_for_uri(target)
        if resolved_path and os.path.isdir(resolved_path):
            source.mount_path = resolved_path
            return True, resolved_path, "Share already mounted in GVFS."

        # Try mounting via gio mount
        try:
            cmd = ["gio", "mount", target]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0 or "already mounted" in result.stderr.lower():
                # Re-check gvfs directory
                resolved_path = cls._find_gvfs_mount_for_uri(target)
                if resolved_path and os.path.isdir(resolved_path):
                    source.mount_path = resolved_path
                    return True, resolved_path, "Mounted successfully via GVFS."
                return True, target, "Mounted, but awaiting GVFS path resolution."
            return False, "", f"GIO mount failed: {result.stderr.strip()}"
        except Exception as e:
            return False, "", f"Error mounting share: {str(e)}"

    @classmethod
    def _find_gvfs_mount_for_uri(cls, uri: str) -> Optional[str]:
        """Look through Gio.VolumeMonitor mounts or /run/user/<UID>/gvfs for matching URI."""
        if not GIO_AVAILABLE:
            return None

        try:
            vm = Gio.VolumeMonitor.get()
            for mount in vm.get_mounts():
                root = mount.get_root()
                mount_uri = root.get_uri()
                if mount_uri and (mount_uri.startswith(uri) or uri.startswith(mount_uri)):
                    return root.get_path()
        except Exception:
            pass

        # Check GVFS folder directly in /run/user/<uid>/gvfs
        uid = os.getuid()
        gvfs_base = f"/run/user/{uid}/gvfs"
        if os.path.isdir(gvfs_base):
            for entry in os.listdir(gvfs_base):
                full_path = os.path.join(gvfs_base, entry)
                # match smb-share:server=... or similar
                if os.path.isdir(full_path):
                    return full_path

        return None
