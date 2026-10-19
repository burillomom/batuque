"""
Device Profile Manager for SoundBee.
Persists per-device configuration profiles in ~/.config/soundbee/devices/
"""

import os
import glob
from typing import List, Optional
from ..core.models import DeviceProfile


class DeviceManager:
    def __init__(self, config_dir: Optional[str] = None):
        if config_dir is None:
            config_dir = os.path.expanduser("~/.config/soundbee/devices")
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
