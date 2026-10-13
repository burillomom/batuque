"""
NAS & Network Share Integration for SoundBee on Linux.
Supports:
- Local filesystem mounts (/mnt/nas, /media/nas)
- GIO / GVFS network shares (smb://, nfs://, sftp://, davs://)
- Non-blocking asynchronous mounting with credential support
- Share/subfolder separation & path resolution to /run/user/<UID>/gvfs/...
"""

import os
import subprocess
import urllib.parse
from typing import Optional, Tuple
from .models import NASSource

try:
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
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
        try:
            res = subprocess.run(["rpm", "-q", "gvfs-smb"], capture_output=True, text=True)
            if res.returncode == 0:
                return True
        except Exception:
            pass

        for p in ["/usr/libexec/gvfsd-smb", "/usr/lib/gvfs/gvfsd-smb", "/usr/lib64/gvfs/gvfsd-smb"]:
            if os.path.isfile(p):
                return True

        return False

    @classmethod
    def mount_share(cls, source: NASSource, timeout_seconds: int = 8) -> Tuple[bool, str, str]:
        """
        Attempts to mount or resolve a NAS share.
        Executes with bounded retries and credentials support without hanging.
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

        # Perform native GIO mount with Gio.MountOperation
        loop = GLib.MainLoop()
        mount_result = {"success": False, "error": "", "timed_out": False}

        def mount_callback(source_object, res, user_data):
            try:
                success = source_object.mount_enclosing_volume_finish(res)
                mount_result["success"] = True
            except Exception as e:
                err_msg = str(e)
                if "cancelado" in err_msg.lower() or "aborted" in err_msg.lower():
                    if source.username or source.password:
                        mount_result["error"] = "Usuário ou senha rejeitados pelo servidor NAS."
                    else:
                        mount_result["error"] = "Este compartilhamento exige autenticação (usuário e senha obrigatórios)."
                elif "already mounted" in err_msg.lower() or "já montado" in err_msg.lower():
                    mount_result["success"] = True
                else:
                    mount_result["error"] = err_msg
            finally:
                loop.quit()

        gfile = Gio.File.new_for_uri(mount_uri)
        op = Gio.MountOperation()

        auth_attempts = 0

        def on_ask_password(operation, message, default_user, default_domain, flags):
            nonlocal auth_attempts
            auth_attempts += 1
            if auth_attempts > 1:
                # Reject further retry loops immediately
                operation.reply(Gio.MountOperationResult.ABORTED)
                return

            if source.username or source.password:
                if source.username:
                    operation.set_username(source.username)
                if source.password:
                    operation.set_password(source.password)
                if source.domain:
                    operation.set_domain(source.domain)
                operation.reply(Gio.MountOperationResult.HANDLED)
            else:
                # No credentials provided; abort immediately instead of hanging
                operation.reply(Gio.MountOperationResult.ABORTED)

        op.connect("ask-password", on_ask_password)
        op.connect("ask-question", lambda o, m, c: o.reply(Gio.MountOperationResult.ABORTED))

        # Timeout handler
        def on_timeout():
            mount_result["timed_out"] = True
            mount_result["error"] = "Tempo limite esgotado aguardando resposta do servidor."
            loop.quit()
            return False

        timeout_id = GLib.timeout_add_seconds(timeout_seconds, on_timeout)

        # Start mount
        gfile.mount_enclosing_volume(0, op, None, mount_callback, None)
        loop.run()
        GLib.source_remove(timeout_id)

        if mount_result["success"]:
            # Check for resolved path in GVFS
            resolved_share_path = cls._find_gvfs_mount_for_uri(mount_uri)
            if resolved_share_path and os.path.isdir(resolved_share_path):
                full_local_path = os.path.join(resolved_share_path, subfolder) if subfolder else resolved_share_path
                source.mount_path = full_local_path
                return True, full_local_path, "Montado com sucesso via GVFS."
            return True, mount_uri, "Compartilhamento montado com sucesso."

        return False, "", mount_result["error"] or "Falha ao conectar ao compartilhamento."

    @classmethod
    def _find_gvfs_mount_for_uri(cls, uri: str) -> Optional[str]:
        """Look through Gio.VolumeMonitor mounts or /run/user/<UID>/gvfs for matching URI."""
        if not GIO_AVAILABLE:
            return None

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

        uid = os.getuid()
        gvfs_base = f"/run/user/{uid}/gvfs"
        if os.path.isdir(gvfs_base):
            for entry in os.listdir(gvfs_base):
                full_path = os.path.join(gvfs_base, entry)
                if os.path.isdir(full_path):
                    entry_lower = entry.lower()
                    if "smb-share" in entry_lower:
                        parts = clean_uri.replace("smb://", "").split("/")
                        if len(parts) >= 2:
                            host_part = parts[0].lower()
                            share_part = parts[1].lower()
                            if host_part in entry_lower and share_part in entry_lower:
                                return full_path
                        return full_path

        return None
