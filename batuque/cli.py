"""
Command Line Interface for Batuque.
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
from .devices.inspector import DeviceInspector


def main():
    parser = argparse.ArgumentParser(
        prog="batuque",
        description="Batuque - Gerenciador de Música e Sincronizador de Dispositivos (DAPs) para Linux"
    )
    parser.add_argument("-v", "--version", action="version", version="Batuque 0.1.0")
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
    dev_insp = dev_sub.add_parser("inspect", help="Inspect albums and covers on a connected device")
    dev_insp.add_argument("device_id", help="Device ID to inspect")
    dev_rep = dev_sub.add_parser("repair-artwork", help="Scan and repair corrupted covers on device")
    dev_rep.add_argument("device_id", help="Device ID")
    dev_rep.add_argument("--all", action="store_true", help="Force repair of all covers, not only detected corruptions")
    dev_opt = dev_sub.add_parser("optimize-artwork", help="Optimize oversized covers on device")
    dev_opt.add_argument("device_id", help="Device ID")

    # Command: sync
    sync_p = subparsers.add_parser("sync", help="Synchronize music to a device")
    sync_p.add_argument("device_id", help="Device ID to synchronize")
    sync_p.add_argument("--dry-run", action="store_true", help="Simulate sync without copying/transcoding")
    sync_p.add_argument("--force-artwork", action="store_true", help="Force overwrite/refresh of all album artwork during sync")

    args = parser.parse_args()

    db = LibraryDatabase()
    dev_manager = DeviceManager()

    if args.command == "stats":
        stats = db.get_stats()
        gb = stats["size_bytes"] / (1024 ** 3)
        hours = stats["duration_seconds"] / 3600
        print(f"--- Biblioteca Batuque ---")
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

        elif args.dev_command == "inspect":
            prof = dev_manager.get_profile(args.device_id)
            if not prof:
                print(f"Error: Profile for device '{args.device_id}' not found.")
                return
            if not os.path.isdir(prof.root_path):
                print(f"Error: Device root path '{prof.root_path}' does not exist or is not mounted.")
                return
            print(f"Inspecting albums on '{prof.name}' ({prof.root_path})...\n")
            albums = DeviceInspector.scan_device_albums(prof)
            if not albums:
                print("No music albums found on device.")
                return
            corrupted_count = sum(1 for a in albums if a.has_corrupted_cover)
            oversized_count = sum(1 for a in albums if a.is_oversized)
            total_tracks = sum(a.track_count for a in albums)
            for a in albums:
                status = ""
                if a.has_corrupted_cover:
                    status = " [⚠️ CORRUPTED / GRAY BAR]"
                elif a.is_oversized:
                    status = " [⚠️ OVERSIZED]"
                print(f" • {a.artist} - {a.album} ({a.track_count} tracks) | Cover: {a.display_cover_info}{status}")
            print(f"\nTotal: {len(albums)} albums ({total_tracks} tracks)")
            if corrupted_count:
                print(f"⚠️  {corrupted_count} album(s) have corrupted covers! Run 'batuque devices repair-artwork {args.device_id}' to fix.")
            if oversized_count:
                print(f"⚠️  {oversized_count} album(s) have oversized covers! Run 'batuque devices optimize-artwork {args.device_id}' to optimize.")

        elif args.dev_command == "repair-artwork":
            prof = dev_manager.get_profile(args.device_id)
            if not prof:
                print(f"Error: Profile for device '{args.device_id}' not found.")
                return
            if not os.path.isdir(prof.root_path):
                print(f"Error: Device root path '{prof.root_path}' does not exist or is not mounted.")
                return
            if not DeviceInspector.is_writable(prof):
                print(f"Error: Device '{prof.root_path}' is mounted read-only.")
                return
            print(f"Scanning and repairing artwork on '{prof.name}'...")
            def on_prog(curr, tot, name):
                print(f"[{curr}/{tot}] Repairing: {name}")
            res = DeviceInspector.repair_all_device_artwork(prof, force_all=args.all, on_progress=on_prog)
            print(f"\nRepair complete:")
            print(f" - Total albums checked: {res['total_albums']}")
            print(f" - Corrupted detected  : {res['corrupted_found']}")
            print(f" - Successfully repaired: {res['repaired_count']}")
            if res.get("failed_count", 0) > 0:
                print(f" - Failed to repair   : {res['failed_count']}")

        elif args.dev_command == "optimize-artwork":
            prof = dev_manager.get_profile(args.device_id)
            if not prof:
                print(f"Error: Profile for device '{args.device_id}' not found.")
                return
            if not os.path.isdir(prof.root_path):
                print(f"Error: Device root path '{prof.root_path}' does not exist or is not mounted.")
                return
            if not DeviceInspector.is_writable(prof):
                print(f"Error: Device '{prof.root_path}' is mounted read-only.")
                return
            print(f"Optimizing artwork on '{prof.name}'...")
            def on_prog(curr, tot, name):
                print(f"[{curr}/{tot}] Optimizing: {name}")
            res = DeviceInspector.optimize_all_device_artwork(prof, on_progress=on_prog)
            print(f"\nOptimization complete: {res['optimized_count']} cover(s) optimized.")

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
        plan = engine.create_sync_plan(prof, tracks, force_artwork=args.force_artwork)

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
