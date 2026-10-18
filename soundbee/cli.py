"""
Command Line Interface for SoundBee.
Allows headless synchronization, NAS scanning, and device profile inspection.
"""

import sys
import argparse
import os
import uuid
from .core.database import LibraryDatabase
from .core.scanner import LibraryScanner
from .core.nas import NASManager
from .core.models import NASSource, DeviceProfile
from .core.sync_engine import SyncEngine
from .devices.detector import DeviceDetector
from .devices.manager import DeviceManager


def main():
    parser = argparse.ArgumentParser(
        prog="soundbee",
        description="SoundBee - Music Manager & Device Synchronizer for Linux"
    )
    parser.add_argument("-v", "--version", action="version", version="SoundBee 0.1.0")
    subparsers = parser.add_subparsers(dest="command")

    # Command: stats
    subparsers.add_parser("stats", help="Display music library statistics")

    # Command: scan
    scan_p = subparsers.add_parser("scan", help="Scan a directory for music tracks")
    scan_p.add_argument("path", help="Local directory or mount path to scan")
    scan_p.add_argument("--source", default="local", help="Source ID identifier (default: local)")

    # Command: nas
    nas_p = subparsers.add_parser("nas", help="Manage NAS and network shares")
    nas_sub = nas_p.add_subparsers(dest="nas_command")
    nas_sub.add_parser("list", help="List registered NAS sources")
    nas_add = nas_sub.add_parser("add", help="Add a NAS source")
    nas_add.add_argument("name", help="Friendly name")
    nas_add.add_argument("uri", help="Network URI (smb://...) or local mount path (/mnt/nas)")
    nas_scan = nas_sub.add_parser("scan", help="Scan a registered NAS source")
    nas_scan.add_argument("source_id", help="ID of the NAS source to scan")

    # Command: devices
    dev_p = subparsers.add_parser("devices", help="Manage device profiles and connected hardware")
    dev_sub = dev_p.add_subparsers(dest="dev_command")
    dev_sub.add_parser("list", help="List connected devices and saved profiles")
    dev_add = dev_sub.add_parser("add-virtual", help="Create a virtual device profile from folder")
    dev_add.add_argument("name", help="Device name")
    dev_add.add_argument("path", help="Destination folder path")

    # Command: sync
    sync_p = subparsers.add_parser("sync", help="Synchronize music to a device")
    sync_p.add_argument("device_id", help="Device ID to synchronize")
    sync_p.add_argument("--dry-run", action="store_true", help="Simulate sync without copying/transcoding")

    args = parser.parse_args()

    db = LibraryDatabase()
    dev_manager = DeviceManager()

    if args.command == "stats":
        stats = db.get_stats()
        gb = stats["size_bytes"] / (1024 ** 3)
        hours = stats["duration_seconds"] / 3600
        print(f"--- SoundBee Library ---")
        print(f"Total Tracks : {stats['tracks']}")
        print(f"Artists      : {stats['artists']}")
        print(f"Albums       : {stats['albums']}")
        print(f"Total Time   : {hours:.1f} hours")
        print(f"Total Size   : {gb:.2f} GB")

    elif args.command == "scan":
        path = os.path.abspath(args.path)
        print(f"Scanning '{path}' (source: {args.source})...")
        scanner = LibraryScanner(db)

        def on_prog(curr, tot, f):
            if curr % 50 == 0 or curr == tot:
                print(f"[{curr}/{tot}] {os.path.basename(f)}")

        count = scanner.scan_directory(path, source_id=args.source, on_progress=on_prog)
        print(f"Scan complete. {count} tracks added/updated.")

    elif args.command == "nas":
        if args.nas_command == "list":
            sources = db.get_nas_sources()
            if not sources:
                print("No NAS sources configured.")
                return
            for s in sources:
                print(f"[{s.id}] {s.name} - URI: {s.uri} (Mount: {s.mount_path or 'unmounted'})")

        elif args.nas_command == "add":
            sid = "nas_" + uuid.uuid4().hex[:8]
            src = NASSource(id=sid, name=args.name, uri=args.uri)
            ok, mount_path, msg = NASManager.mount_share(src)
            db.upsert_nas_source(src)
            print(f"Added NAS source [{sid}] '{args.name}'. Status: {msg}")

        elif args.nas_command == "scan":
            sources = {s.id: s for s in db.get_nas_sources()}
            if args.source_id not in sources:
                print(f"NAS source '{args.source_id}' not found.")
                return
            src = sources[args.source_id]
            ok, mount_path, msg = NASManager.mount_share(src)
            if not ok:
                print(f"Failed to mount NAS: {msg}")
                return
            print(f"Mounted NAS at '{mount_path}'. Starting scan...")
            scanner = LibraryScanner(db)
            scanner.scan_directory(mount_path, source_id=src.id)
            print("NAS scan completed.")

    elif args.command == "devices":
        if args.dev_command == "list":
            detector = DeviceDetector()
            connected = detector.list_connected_devices()
            profiles = dev_manager.get_all_profiles()
            print("--- Connected Hardware Devices ---")
            for d in connected:
                prof = dev_manager.get_profile(d.device_id)
                status = "Configured" if prof else "Not configured"
                print(f" - [{d.device_id}] {d.name} ({d.device_type}) at {d.root_path} [{status}]")

            print("\n--- Saved Device Profiles ---")
            for p in profiles:
                print(f" - [{p.device_id}] {p.name} -> {p.root_path} (Codec: {p.target_codec} {p.target_bitrate}k)")

        elif args.dev_command == "add-virtual":
            target_path = os.path.abspath(args.path)
            os.makedirs(target_path, exist_ok=True)
            did = "virt_" + uuid.uuid4().hex[:8]
            prof = DeviceProfile(
                device_id=did,
                name=args.name,
                device_type="virtual",
                root_path=target_path
            )
            dev_manager.save_profile(prof)
            print(f"Created virtual device profile [{did}] '{args.name}' -> {target_path}")

    elif args.command == "sync":
        prof = dev_manager.get_profile(args.device_id)
        if not prof:
            print(f"Error: Profile for device '{args.device_id}' not found.")
            return

        if not os.path.isdir(prof.root_path):
            print(f"Error: Device root path '{prof.root_path}' does not exist or is not mounted.")
            return

        tracks = db.get_all_tracks()
        if not tracks:
            print("Library is empty. Scan some music tracks first.")
            return

        engine = SyncEngine()
        print(f"Planning sync for '{prof.name}' ({len(tracks)} tracks in library)...")
        plan = engine.create_sync_plan(prof, tracks)

        print("\n--- Sync Plan Summary ---")
        print(f"Files to copy      : {len(plan.items_to_copy)}")
        print(f"Files to transcode : {len(plan.items_to_transcode)}")
        print(f"Files to delete    : {len(plan.items_to_delete)}")
        print(f"Files to skip      : {len(plan.items_to_skip)}")
        print(f"Total transfer size: {plan.total_transfer_bytes / (1024**2):.1f} MB")
        if plan.device_free_space_bytes:
            print(f"Device free space  : {plan.device_free_space_bytes / (1024**2):.1f} MB")

        if args.dry_run:
            print("\nDry-run mode active. No changes written.")
            return

        print("\nStarting synchronization...")
        def on_prog(curr, tot, desc):
            print(f"[{curr}/{tot}] {desc}")

        success = engine.execute_plan(plan, on_progress=on_prog)
        if success:
            print("\nSynchronization completed successfully!")
        else:
            print("\nSynchronization was cancelled or encountered errors.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
