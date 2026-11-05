"""
Hardware and Volume Detector for SoundBee using GIO VolumeMonitor.
Detects USB Drives, SD Cards, DAPs, MTP smartphones, and Virtual Devices.
"""

import os
import hashlib
from typing import List, Callable, Optional, Dict

try:
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio
    GIO_AVAILABLE = True
except Exception:
    GIO_AVAILABLE = False


class DetectedDevice:
    def __init__(self, device_id: str, name: str, root_path: str, device_type: str = "usb"):
        self.device_id = device_id
        self.name = name
        self.root_path = root_path
        self.device_type = device_type

    def __repr__(self):
        return f"<DetectedDevice {self.name} ({self.device_type}) at {self.root_path}>"


class DeviceDetector:
    def __init__(self):
        self._on_device_changed_callbacks: List[Callable[[], None]] = []
        self._monitor = None
        if GIO_AVAILABLE:
            try:
                self._monitor = Gio.VolumeMonitor.get()
                self._monitor.connect("mount-added", self._on_gio_event)
                self._monitor.connect("mount-removed", self._on_gio_event)
                self._monitor.connect("volume-added", self._on_gio_event)
                self._monitor.connect("volume-removed", self._on_gio_event)
            except Exception:
                pass

    def add_change_listener(self, callback: Callable[[], None]):
        self._on_device_changed_callbacks.append(callback)

    def _on_gio_event(self, *args):
        for cb in self._on_device_changed_callbacks:
            try:
                cb()
            except Exception:
                pass

    def list_connected_devices(self) -> List[DetectedDevice]:
        """
        Scans and returns all mounted removable storage devices and external players.
        """
        devices: List[DetectedDevice] = []
        seen_paths = set()

        if GIO_AVAILABLE and self._monitor:
            try:
                for mount in self._monitor.get_mounts():
                    root = mount.get_root()
                    path = root.get_path()
                    uri = root.get_uri() or ""
                    if not path or not os.path.isdir(path):
                        continue

                    # Ignore root filesystem and system mounts
                    if path in ("/", "/home", "/boot", "/etc", "/var"):
                        continue
                    if path.startswith("/snap") or path.startswith("/var/lib/flatpak"):
                        continue

                    name = mount.get_name() or os.path.basename(path)
                    vol = mount.get_volume()
                    uuid = vol.get_uuid() if vol else ""

                    device_type = "usb"
                    if uri.startswith("mtp://"):
                        device_type = "mtp"

                    # Generate stable device ID
                    if uuid:
                        dev_id = f"vol_{uuid}"
                    else:
                        dev_id = "path_" + hashlib.sha256(path.encode()).hexdigest()[:12]

                    if path not in seen_paths:
                        seen_paths.add(path)
                        devices.append(DetectedDevice(
                            device_id=dev_id,
                            name=name,
                            root_path=path,
                            device_type=device_type
                        ))
            except Exception:
                pass

        # Also inspect /run/media/<user>/ for mounted USB drives
        uid_name = os.environ.get("USER", "")
        if uid_name:
            media_dir = f"/run/media/{uid_name}"
            if os.path.isdir(media_dir):
                for entry in os.listdir(media_dir):
                    p = os.path.join(media_dir, entry)
                    if os.path.isdir(p) and p not in seen_paths:
                        seen_paths.add(p)
                        dev_id = "media_" + hashlib.sha256(p.encode()).hexdigest()[:12]
                        devices.append(DetectedDevice(
                            device_id=dev_id,
                            name=entry,
                            root_path=p,
                            device_type="usb"
                        ))

        return devices
