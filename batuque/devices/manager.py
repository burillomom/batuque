"""
Device Profile Manager for Batuque.
Persists per-device configuration profiles in ~/.config/soundbee/devices/
"""

import os
import glob
from typing import List, Optional
from ..core.models import DeviceProfile


class DeviceManager:
    def __init__(self, config_dir: Optional[str] = None):
        if config_dir is None:
            config_dir = os.path.expanduser("~/.config/batuque/devices")
            old_dir = os.path.expanduser("~/.config/soundbee/devices")
            if not os.path.exists(config_dir):
                if os.path.exists(old_dir):
                    try:
                        os.makedirs(os.path.dirname(config_dir), exist_ok=True)
                        os.symlink(old_dir, config_dir)
                    except Exception:
                        config_dir = old_dir
                else:
                    os.makedirs(config_dir, exist_ok=True)
        self.config_dir = os.path.abspath(config_dir)
        os.makedirs(self.config_dir, exist_ok=True)

    def _get_profile_path(self, device_id: str) -> str:
        # Sanitize filename
        safe_id = "".join(c for c in device_id if c.isalnum() or c in ("-", "_"))
        return os.path.join(self.config_dir, f"{safe_id}.json")

    def get_all_profiles(self) -> List[DeviceProfile]:
        profiles = []
        pattern = os.path.join(self.config_dir, "*.json")
        for fpath in glob.glob(pattern):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    profiles.append(DeviceProfile.from_json(f.read()))
            except Exception:
                continue
        return profiles

    def get_profile(self, device_id: str) -> Optional[DeviceProfile]:
        path = self._get_profile_path(device_id)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return DeviceProfile.from_json(f.read())
            except Exception:
                pass
        return None

    def save_profile(self, profile: DeviceProfile):
        if not profile.device_id:
            raise ValueError("Device profile must have a valid device_id")
        path = self._get_profile_path(profile.device_id)
        with open(path, "w", encoding="utf-8") as f:
            f.write(profile.to_json())

    def delete_profile(self, device_id: str):
        path = self._get_profile_path(device_id)
        if os.path.isfile(path):
            os.remove(path)

    def get_or_create_profile(
        self,
        device_id: str,
        name: str,
        root_path: str,
        device_type: str = "usb"
    ) -> DeviceProfile:
        existing = self.get_profile(device_id)
        if existing:
            # Update path if mount changed
            if existing.root_path != root_path:
                existing.root_path = root_path
                self.save_profile(existing)
            return existing

        # Create fresh profile
        new_prof = DeviceProfile(
            device_id=device_id,
            name=name,
            root_path=root_path,
            device_type=device_type
        )
        self.save_profile(new_prof)
        return new_prof

    def set_default_device(self, device_id: str):
        """Sets a profile as default and unsets is_default on all others."""
        for prof in self.get_all_profiles():
            if prof.device_id == device_id:
                prof.is_default = True
                prof.ignored = False
            else:
                prof.is_default = False
            self.save_profile(prof)

    def set_ignored_device(self, device_id: str, ignored: bool = True):
        """Sets or unsets ignored flag on a device profile."""
        prof = self.get_profile(device_id)
        if prof:
            prof.ignored = ignored
            if ignored:
                prof.is_default = False
            self.save_profile(prof)

    def get_default_profile(self) -> Optional[DeviceProfile]:
        """Returns the current default profile, if any and not ignored."""
        for prof in self.get_all_profiles():
            if prof.is_default and not prof.ignored:
                return prof
        return None

    @staticmethod
    def repair_filesystem(mount_path: str) -> tuple[bool, str]:
        """
        Uses UDisks2 to unmount, check & repair filesystem errors (such as FAT32 dirty
        or corrupted directories caused by disconnecting without unmounting), then remounts as read-write.
        """
        if not mount_path or not os.path.exists(mount_path):
            return False, f"Ponto de montagem inválido: {mount_path}"

        try:
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio, GLib

            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            om = Gio.DBusObjectManagerClient.new_sync(
                bus,
                Gio.DBusObjectManagerClientFlags.NONE,
                'org.freedesktop.UDisks2',
                '/org/freedesktop/UDisks2',
                None,
                None,
                None
            )
            block_obj_path = None
            for obj in om.get_objects():
                fs = obj.get_interface('org.freedesktop.UDisks2.Filesystem')
                if fs:
                    mps = fs.get_cached_property('MountPoints')
                    if mps:
                        for b_mp in mps.unpack():
                            p_str = bytes(b_mp).decode('utf-8', errors='ignore').rstrip('\x00/').rstrip('/')
                            if p_str == mount_path.rstrip('/'):
                                block_obj_path = obj.get_object_path()
                                break
                if block_obj_path:
                    break

            if not block_obj_path:
                return False, f"Dispositivo de bloco para '{mount_path}' não encontrado no UDisks2."

            fs_proxy = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.NONE,
                None,
                'org.freedesktop.UDisks2',
                block_obj_path,
                'org.freedesktop.UDisks2.Filesystem',
                None
            )

            # 1. Unmount
            try:
                fs_proxy.call_sync('Unmount', GLib.Variant('(a{sv})', [{}]), Gio.DBusCallFlags.NONE, 15000, None)
            except Exception:
                pass

            # 2. Repair
            repair_log = ""
            try:
                fs_proxy.call_sync('Repair', GLib.Variant('(a{sv})', [{}]), Gio.DBusCallFlags.NONE, 60000, None)
            except Exception as re:
                repair_log = str(re)

            # 3. Mount back
            mount_res = fs_proxy.call_sync('Mount', GLib.Variant('(a{sv})', [{}]), Gio.DBusCallFlags.NONE, 15000, None)
            new_path = mount_res[0] if mount_res else mount_path

            if os.access(new_path, os.W_OK):
                return True, "Sistema de arquivos reparado e remontado com permissão de escrita (RW) com sucesso."
            else:
                return False, f"Dispositivo remontado, mas ainda está em modo somente leitura: {repair_log}"
        except Exception as e:
            return False, f"Falha ao executar reparo no UDisks2: {e}"
