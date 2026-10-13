"""
NAS & Network Share Integration for SoundBee on Linux.
Supports:
- Local filesystem mounts (/mnt/nas, /media/nas)
- GIO / GVFS network shares (smb://, nfs://, sftp://, davs://)
- Auto-mount, share/subfolder separation, & path resolution to /run/user/<UID>/gvfs/...
"""

import os
import subprocess
import urllib.parse
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
    def parse_smb_uri(cls, uri: str) -> Tuple[str, str, str, str]:
        """
        Parses an SMB URI into (host, share, subfolder, base_share_uri).
        E.g.: smb://100.79.195.46/NAS-4TB/Musica -> ('100.79.195.46', 'NAS-4TB', 'Musica', 'smb://100.79.195.46/NAS-4TB')
        """
        parsed = urllib.parse.urlparse(uri)
        host = parsed.netloc or ""
        path = parsed.path.lstrip("/")
        parts = path.split("/", 1)
        share = parts[0] if parts else ""
        subfolder = parts[1] if len(parts) > 1 else ""

        # Reconstruct base share URI (without subfolder, because GIO only mounts share roots)
        base_share_uri = f"smb://{host}/{share}".rstrip("/")
        return host, share, subfolder, base_share_uri

    @classmethod
    def is_gvfs_smb_installed(cls) -> bool:
        """Checks if gvfs-smb package / backend is present on the system."""
        # Check rpm package on Fedora/RHEL
        try:
            res = subprocess.run(["rpm", "-q", "gvfs-smb"], capture_output=True, text=True)
            if res.returncode == 0:
                return True
        except Exception:
            pass

        # Check for gvfsd-smb binary
        for p in ["/usr/libexec/gvfsd-smb", "/usr/lib/gvfs/gvfsd-smb", "/usr/lib64/gvfs/gvfsd-smb"]:
            if os.path.isfile(p):
                return True

        return False

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
                return True, source.mount_path, "Diretório local acessível."
            return False, "", f"Diretório '{target}' não encontrado ou não montado."

        # Case 2: Windows UNC style path (\\server\share\...)
        if target.startswith("\\\\"):
            parts = target.lstrip("\\").replace("\\", "/").split("/", 1)
            if len(parts) >= 2:
                target = f"smb://{parts[0]}/{parts[1]}"
            else:
                target = f"smb://{parts[0]}"
            source.uri = target

        # Case 3: Network URI (smb://, nfs://, etc.)
        if not GIO_AVAILABLE:
            return False, "", "Biblioteca GIO não disponível neste sistema."

        # Verify gvfs-smb availability for SMB shares
        if target.startswith("smb://") and not cls.is_gvfs_smb_installed():
            return False, "", (
                "O pacote 'gvfs-smb' não está instalado no Fedora.\n"
                "Para ativar o suporte a SMB/Samba no GIO/GNOME, instale via terminal:\n"
                "  sudo dnf install -y gvfs-smb"
            )

        # Separate share root from subfolder
        subfolder = ""
        mount_uri = target
        if target.startswith("smb://"):
            host, share, subfolder, base_share_uri = cls.parse_smb_uri(target)
            mount_uri = base_share_uri

        # Check if already mounted in GVFS
        resolved_share_path = cls._find_gvfs_mount_for_uri(mount_uri)
        if resolved_share_path and os.path.isdir(resolved_share_path):
            full_local_path = os.path.join(resolved_share_path, subfolder) if subfolder else resolved_share_path
            source.mount_path = full_local_path
            return True, full_local_path, "Compartilhamento já montado no GVFS."

        # Build gio mount command
        cmd = ["gio", "mount", mount_uri]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            stderr = result.stderr.strip()

            if result.returncode == 0 or "already mounted" in stderr.lower() or "já montado" in stderr.lower():
                resolved_share_path = cls._find_gvfs_mount_for_uri(mount_uri)
                if resolved_share_path and os.path.isdir(resolved_share_path):
                    full_local_path = os.path.join(resolved_share_path, subfolder) if subfolder else resolved_share_path
                    source.mount_path = full_local_path
                    return True, full_local_path, "Montado com sucesso via GVFS."
                return True, mount_uri, "Montado no sistema."
            
            if "volume no implementa montagem" in stderr.lower() or "volume não implementa montagem" in stderr.lower():
                return False, "", (
                    "O GIO não possui o driver para este protocolo.\n"
                    "Instale o backend executando: sudo dnf install -y gvfs-smb"
                )

            return False, "", f"Erro no GIO mount: {stderr}"
        except subprocess.TimeoutExpired:
            return False, "", "Tempo limite esgotado aguardando resposta do servidor NAS."
        except Exception as e:
            return False, "", f"Erro ao montar compartilhamento: {str(e)}"

    @classmethod
    def _find_gvfs_mount_for_uri(cls, uri: str) -> Optional[str]:
        """Look through Gio.VolumeMonitor mounts or /run/user/<UID>/gvfs for matching URI."""
        if not GIO_AVAILABLE:
            return None

        # Clean URI for matching
        clean_uri = uri.rstrip("/").lower()

        try:
            vm = Gio.VolumeMonitor.get()
            for mount in vm.get_mounts():
                root = mount.get_root()
                mount_uri = (root.get_uri() or "").rstrip("/").lower()
                if mount_uri and (mount_uri == clean_uri or mount_uri.startswith(clean_uri) or clean_uri.startswith(mount_uri)):
                    return root.get_path()
        except Exception:
            pass

        # Check GVFS folder directly in /run/user/<uid>/gvfs
        uid = os.getuid()
        gvfs_base = f"/run/user/{uid}/gvfs"
        if os.path.isdir(gvfs_base):
            for entry in os.listdir(gvfs_base):
                full_path = os.path.join(gvfs_base, entry)
                if os.path.isdir(full_path):
                    # smb-share:server=100.79.195.46,share=nas-4tb
                    entry_lower = entry.lower()
                    if "smb-share" in entry_lower:
                        # Extract host/share
                        parts = clean_uri.replace("smb://", "").split("/")
                        if len(parts) >= 2:
                            host_part = parts[0].lower()
                            share_part = parts[1].lower()
                            if host_part in entry_lower and share_part in entry_lower:
                                return full_path
                        return full_path

        return None
