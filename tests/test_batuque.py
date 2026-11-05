"""
Automated Unit Tests for Batuque.
"""

import unittest
import os
import tempfile
import shutil
from batuque.core.models import Track, DeviceProfile, NASSource
from batuque.core.database import LibraryDatabase
from batuque.core.organizer import FileOrganizer
from batuque.core.transcoder import AudioTranscoder
from batuque.core.playlist import PlaylistExporter
from batuque.core.sync_engine import SyncEngine
from batuque.devices.manager import DeviceManager


class TestBatuque(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_library.db")
        self.db = LibraryDatabase(self.db_path)
        self.dev_manager = DeviceManager(os.path.join(self.temp_dir, "devices"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_database_operations(self):
        t1 = Track(
            file_path="/music/daft_punk_one_more_time.flac",
            title="One More Time",
            artist="Daft Punk",
            album="Discovery",
            genre="Electronic",
            year=2001,
            track_number=1,
            duration_seconds=320.5,
            bitrate_kbps=950,
            file_format="flac",
            size_bytes=35000000,
            source_id="local"
        )
        self.db.upsert_track(t1)

        # Retrieve
        fetched = self.db.get_track_by_path("/music/daft_punk_one_more_time.flac")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.title, "One More Time")
        self.assertEqual(fetched.artist, "Daft Punk")
        self.assertEqual(fetched.year, 2001)

        # Search
        results = self.db.search_tracks("Discovery")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].album, "Discovery")

        # Stats
        stats = self.db.get_stats()
        self.assertEqual(stats["tracks"], 1)
        self.assertEqual(stats["artists"], 1)
        self.assertEqual(stats["albums"], 1)

    def test_file_organizer_templates(self):
        t = Track(
            artist="Pink Floyd",
            album="The Dark Side of the Moon",
            title="Money",
            track_number=6,
            year=1973,
            file_format="flac"
        )

        # Python style format
        path1 = FileOrganizer.format_relative_path(t, "{Artist}/{Album}/{Track:02d} - {Title}.{ext}", "mp3")
        self.assertEqual(path1, "Pink Floyd/The Dark Side of the Moon/06 - Money.mp3")

        # MusicBee style format
        path2 = FileOrganizer.format_relative_path(t, "<Artist>/<Album>/<Track#> - <Title>.<ext>", "mp3")
        self.assertEqual(path2, "Pink Floyd/The Dark Side of the Moon/06 - Money.mp3")

        # FAT32 / filesystem invalid character sanitization
        t_dirty = Track(
            artist="AC/DC",
            album="Who Made Who?",
            title="Hells Bells: Live*",
            track_number=1,
            file_format="mp3"
        )
        sanitized = FileOrganizer.format_relative_path(t_dirty, "{Artist}/{Album}/{Title}.{ext}")
        self.assertNotIn(":", sanitized)
        self.assertNotIn("?", sanitized)
        self.assertNotIn("*", sanitized)

    def test_transcoder_decision_matrix(self):
        profile = DeviceProfile(
            name="Fiio M11",
            transcode_enabled=True,
            target_codec="mp3",
            target_bitrate=320,
            convert_lossless_only=True,
            convert_except_min_bitrate=160
        )

        # FLAC should be transcoded to MP3
        track_flac = Track(file_format="flac", bitrate_kbps=1000)
        need_transcode, ext = AudioTranscoder.should_transcode(track_flac, profile)
        self.assertTrue(need_transcode)
        self.assertEqual(ext, "mp3")

        # High-bitrate MP3 should NOT be transcoded (convert_lossless_only is True)
        track_mp3 = Track(file_format="mp3", bitrate_kbps=320)
        need_transcode, ext = AudioTranscoder.should_transcode(track_mp3, profile)
        self.assertFalse(need_transcode)
        self.assertEqual(ext, "mp3")

    def test_device_profile_persistence(self):
        prof = DeviceProfile(
            device_id="test_dap_01",
            name="Sony Walkman NW-A55",
            device_type="usb",
            root_path=self.temp_dir,
            target_codec="opus",
            target_bitrate=192,
            music_subfolder="MUSIC",
            naming_rule="{Artist}/{Album}/{Track:02d} {Title}.{ext}"
        )
        self.dev_manager.save_profile(prof)

        loaded = self.dev_manager.get_profile("test_dap_01")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "Sony Walkman NW-A55")
        self.assertEqual(loaded.target_codec, "opus")
        self.assertEqual(loaded.target_bitrate, 192)

    def test_sync_plan_generation(self):
        device_dir = os.path.join(self.temp_dir, "my_device")
        os.makedirs(device_dir, exist_ok=True)

        prof = DeviceProfile(
            device_id="test_usb",
            name="USB Stick",
            root_path=device_dir,
            music_subfolder="Music",
            transcode_enabled=False
        )

        dummy_audio = os.path.join(self.temp_dir, "song.mp3")
        with open(dummy_audio, "wb") as f:
            f.write(b"AUDIO_DATA_BYTES" * 100)

        t = Track(
            file_path=dummy_audio,
            title="Song 1",
            artist="Artist A",
            album="Album B",
            track_number=1,
            file_format="mp3",
            size_bytes=os.path.getsize(dummy_audio)
        )

        engine = SyncEngine()
        plan = engine.create_sync_plan(prof, [t])

        self.assertEqual(len(plan.items_to_copy), 1)
        self.assertEqual(len(plan.items_to_skip), 0)
        self.assertEqual(plan.items_to_copy[0].action, "copy")

        # Execute direct copy
        success = engine.execute_plan(plan)
        self.assertTrue(success)

        # Second sync should SKIP since file now exists with identical size
        plan2 = engine.create_sync_plan(prof, [t])
        self.assertEqual(len(plan2.items_to_copy), 0)
        self.assertEqual(len(plan2.items_to_skip), 1)
        self.assertEqual(plan2.items_to_skip[0].action, "skip")

    def test_playlist_export(self):
        device_dir = os.path.join(self.temp_dir, "my_dap")
        os.makedirs(device_dir, exist_ok=True)

        prof = DeviceProfile(
            device_id="dap_pl",
            name="DAP",
            root_path=device_dir,
            playlist_export=True,
            playlist_format="m3u8",
            playlist_subfolder="Playlists",
            playlist_relative=True
        )

        t = Track(title="Song 1", artist="Artist", duration_seconds=180)
        items = [(t, "Music/Artist/Album/01 - Song 1.mp3")]

        pl_file = PlaylistExporter.export_playlist("Best of 2026", items, prof)
        self.assertTrue(os.path.isfile(pl_file))

        with open(pl_file, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("#EXTM3U", content)
        self.assertIn("#EXTINF:180,Artist - Song 1", content)
        # Relative path from Playlists/ to Music/ is ../Music/...
        self.assertIn("../Music/Artist/Album/01 - Song 1.mp3", content)

    def test_parse_smb_uri(self):
        from batuque.core.nas import NASManager
        host, share, subfolder, base = NASManager.parse_smb_uri("smb://100.79.195.46/NAS-4TB/Musica")
        self.assertEqual(host, "100.79.195.46")
        self.assertEqual(share, "NAS-4TB")
        self.assertEqual(subfolder, "Musica")
        self.assertEqual(base, "smb://100.79.195.46/NAS-4TB")

    def test_device_exclusion_and_default(self):
        p1 = self.dev_manager.get_or_create_profile("dev1", "Game Drive", "/mnt/games")
        p2 = self.dev_manager.get_or_create_profile("dev2", "iPod Classic", "/mnt/ipod")

        # Set dev2 as default
        self.dev_manager.set_default_device("dev2")
        d = self.dev_manager.get_default_profile()
        self.assertIsNotNone(d)
        self.assertEqual(d.device_id, "dev2")
        self.assertTrue(d.is_default)
        self.assertFalse(self.dev_manager.get_profile("dev1").is_default)

        # Ignore dev1 (Game Drive)
        self.dev_manager.set_ignored_device("dev1", True)
        self.assertTrue(self.dev_manager.get_profile("dev1").ignored)

        # If ignored, it cannot be default
        self.dev_manager.set_ignored_device("dev2", True)
        self.assertIsNone(self.dev_manager.get_default_profile())

    def test_sync_force_overwrite(self):
        device_dir = os.path.join(self.temp_dir, "my_dap2")
        os.makedirs(device_dir, exist_ok=True)

        prof = DeviceProfile(
            device_id="dap_sync2",
            name="DAP",
            root_path=device_dir,
            transcode_enabled=False,
            preserve_filenames=True,
            music_subfolder="Music"
        )

        src_file = os.path.join(self.temp_dir, "song.mp3")
        with open(src_file, "wb") as f:
            f.write(b"AUDIO_DATA_FOR_TESTING")

        t = Track(file_path=src_file, title="Song", artist="Artist", size_bytes=len(b"AUDIO_DATA_FOR_TESTING"), file_format="mp3")

        engine = SyncEngine()
        plan1 = engine.create_sync_plan(prof, [t])
        engine.execute_plan(plan1)

        # Standard plan skips
        plan2 = engine.create_sync_plan(prof, [t], force_overwrite=False)
        self.assertEqual(len(plan2.items_to_skip), 1)
        self.assertEqual(len(plan2.items_to_copy), 0)

        # Force overwrite plan schedules copy
        plan_forced = engine.create_sync_plan(prof, [t], force_overwrite=True)
        self.assertEqual(len(plan_forced.items_to_skip), 0)
        self.assertEqual(len(plan_forced.items_to_copy), 1)

    def test_nas_crud(self):
        nas1 = NASSource(id="nas_1", name="NAS 1", uri="smb://192.168.1.10/Music")
        nas2 = NASSource(id="nas_2", name="NAS 2", uri="smb://192.168.1.20/Music")
        self.db.upsert_nas_source(nas1)
        self.db.upsert_nas_source(nas2)

        sources = self.db.get_nas_sources()
        self.assertEqual(len(sources), 2)

        # Update nas1
        nas1.name = "My Updated NAS"
        self.db.upsert_nas_source(nas1)
        sources = self.db.get_nas_sources()
        names = [s.name for s in sources]
        self.assertIn("My Updated NAS", names)

        # Delete nas2
        self.db.delete_nas_source("nas_2")
        sources = self.db.get_nas_sources()
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].id, "nas_1")

    def test_device_inspector_and_artwork_optimizer(self):
        from batuque.devices.inspector import DeviceInspector
        from PIL import Image

        device_dir = os.path.join(self.temp_dir, "test_player")
        album_dir = os.path.join(device_dir, "Music", "Daft Punk", "Discovery")
        os.makedirs(album_dir, exist_ok=True)

        # Create dummy track
        dummy_mp3 = os.path.join(album_dir, "01 - One More Time.mp3")
        with open(dummy_mp3, "wb") as f:
            f.write(b"AUDIO")

        # Create oversized cover image (1200x1200)
        cover_path = os.path.join(album_dir, "cover.jpg")
        img = Image.new("RGB", (1200, 1200), color="blue")
        img.save(cover_path, "JPEG", quality=95)

        prof = DeviceProfile(
            device_id="test_player",
            name="Test Player",
            root_path=device_dir,
            artwork_max_size=300
        )

        albums = DeviceInspector.scan_device_albums(prof)
        self.assertEqual(len(albums), 1)
        alb = albums[0]
        self.assertEqual(alb.album, "Discovery")
        self.assertEqual(alb.cover_width, 1200)
        self.assertTrue(alb.is_oversized)

        # Optimize
        ok = DeviceInspector.optimize_album_artwork(alb, max_dim=300)
        self.assertTrue(ok)
        self.assertLessEqual(alb.cover_width, 300)
        self.assertLessEqual(alb.cover_height, 300)
        self.assertFalse(alb.is_oversized)

        # Verify on disk
        with Image.open(cover_path) as saved_img:
            self.assertLessEqual(saved_img.width, 300)
            self.assertLessEqual(saved_img.height, 300)

    def test_database_batch_upsert_and_cache(self):
        tracks = [
            Track(file_path=f"/music/track_{i}.mp3", title=f"Track {i}", artist="Artist", album="Album", size_bytes=1000 + i, mtime=1700000.0 + i, source_id="src1")
            for i in range(10)
        ]
        count = self.db.upsert_tracks_batch(tracks)
        self.assertEqual(count, 10)

        cache = self.db.get_cached_paths_map("src1")
        self.assertEqual(len(cache), 10)
        self.assertIn("/music/track_0.mp3", cache)
        self.assertEqual(cache["/music/track_0.mp3"][1], 1000)

        # Test source ID update
        self.db.update_source_id_for_paths(["/music/track_0.mp3"], "src2")
        cache2 = self.db.get_cached_paths_map("src2")
        self.assertEqual(len(cache2), 1)
    def test_preserve_folder_structure_and_album_artist(self):
        # Test {Album Artist} formatting
        t = Track(
            artist="Daft Punk",
            album_artist="Various Artists",
            album="TRON: Legacy",
            title="Derezzed",
            track_number=13,
            file_format="flac"
        )
        res = FileOrganizer.format_relative_path(t, "{Album Artist}/{Album}/{Track:02d} - {Title}.{ext}", "mp3")
        self.assertEqual(res, "Various Artists/TRON_ Legacy/13 - Derezzed.mp3")

        # Test preserve_folder_structure
        roots = ["smb://192.168.1.100/Music", "/local/music"]
        p1 = FileOrganizer.get_preserved_relative_path(
            "smb://192.168.1.100/Music/Rock/Queen/A Night at the Opera/04 - You're My Best Friend.flac",
            source_roots=roots,
            target_extension="mp3"
        )
        self.assertEqual(p1, "Rock/Queen/A Night at the Opera/04 - You're My Best Friend.mp3")

        # Test sync plan with preserve_folder_structure enabled
        device_dir = os.path.join(self.temp_dir, "dap_preserve")
        os.makedirs(device_dir, exist_ok=True)
        prof = DeviceProfile(
            device_id="dap_preserve",
            name="DAP Preserve",
            root_path=device_dir,
            music_subfolder="Music",
            preserve_folder_structure=True,
            transcode_enabled=False
        )

        dummy_audio = os.path.join(self.temp_dir, "source_library", "Rock", "Band", "Album", "01.mp3")
        os.makedirs(os.path.dirname(dummy_audio), exist_ok=True)
        with open(dummy_audio, "wb") as f:
            f.write(b"AUDIO")

        t2 = Track(
            file_path=dummy_audio,
            title="01",
            artist="Band",
            album="Album",
            size_bytes=5
        )

        engine = SyncEngine()
        plan = engine.create_sync_plan(
            prof, [t2],
            source_roots=[os.path.join(self.temp_dir, "source_library")]
        )
        self.assertEqual(len(plan.items_to_copy), 1)
        self.assertEqual(plan.items_to_copy[0].target_rel_path, "Music/Rock/Band/Album/01.mp3")

        ok = engine.execute_plan(plan)
        self.assertTrue(ok)
        self.assertEqual(plan.successful_items, 1)
        self.assertEqual(plan.failed_items, 0)
        self.assertTrue(os.path.isfile(os.path.join(device_dir, "Music", "Rock", "Band", "Album", "01.mp3")))

    def test_artwork_corruption_detection_and_clean(self):
        from PIL import Image, ImageFile
        from batuque.core.artwork import AlbumArtworkCache

        # Truncated images flag must be False
        self.assertFalse(ImageFile.LOAD_TRUNCATED_IMAGES)

        # 1. Valid image
        good_img = Image.new("RGB", (100, 100), color=(200, 50, 50))
        self.assertFalse(AlbumArtworkCache.is_corrupted_artwork(good_img))

        # 2. Corrupted image with PIL gray filler (128, 128, 128) in bottom scanlines
        bad_img = Image.new("RGB", (100, 100), color=(200, 50, 50))
        for y in range(70, 100):
            for x in range(100):
                bad_img.putpixel((x, y), (128, 128, 128))
        self.assertTrue(AlbumArtworkCache.is_corrupted_artwork(bad_img))

        # 3. Cache directory cleanup
        test_cache = os.path.join(self.temp_dir, "test_covers")
        os.makedirs(test_cache, exist_ok=True)
        old_cache_dir = AlbumArtworkCache.CACHE_DIR
        old_legacy = AlbumArtworkCache.LEGACY_CACHE_DIR
        try:
            AlbumArtworkCache.CACHE_DIR = test_cache
            AlbumArtworkCache.LEGACY_CACHE_DIR = os.path.join(self.temp_dir, "nonexistent")
            AlbumArtworkCache._verified_clean.clear()

            good_path = os.path.join(test_cache, "good.jpg")
            bad_path = os.path.join(test_cache, "bad.jpg")
            good_img.save(good_path, format="JPEG")
            bad_img.save(bad_path, format="JPEG")

            self.assertTrue(os.path.exists(good_path))
            self.assertTrue(os.path.exists(bad_path))

            removed = AlbumArtworkCache.clean_corrupted_covers()
            self.assertIn(bad_path, removed)
            self.assertNotIn(good_path, removed)
            self.assertFalse(os.path.exists(bad_path))
            self.assertTrue(os.path.exists(good_path))
        finally:
            AlbumArtworkCache.CACHE_DIR = old_cache_dir
            AlbumArtworkCache.LEGACY_CACHE_DIR = old_legacy
            AlbumArtworkCache._verified_clean.clear()

    def test_device_artwork_repair_and_sync_healing(self):
        from PIL import Image
        from batuque.devices.inspector import DeviceInspector
        from batuque.core.artwork import AlbumArtworkCache

        orig_cache = AlbumArtworkCache.CACHE_DIR
        orig_legacy = AlbumArtworkCache.LEGACY_CACHE_DIR
        test_cache = os.path.join(self.temp_dir, "isolated_cache")
        os.makedirs(test_cache, exist_ok=True)

        try:
            AlbumArtworkCache.CACHE_DIR = test_cache
            AlbumArtworkCache.LEGACY_CACHE_DIR = os.path.join(self.temp_dir, "isolated_legacy")
            AlbumArtworkCache._verified_clean.clear()

            device_dir = os.path.join(self.temp_dir, "test_device_art")
            album_dir = os.path.join(device_dir, "Music", "TestBandArt", "TestAlbumArt")
            os.makedirs(album_dir, exist_ok=True)

            prof = DeviceProfile(
                device_id="dev_test_art",
                name="Test iPod",
                root_path=device_dir,
                artwork_save_file=True,
                artwork_filename="cover.jpg",
                artwork_max_size=300
            )

            # 1. Create a dummy audio file on device
            track_file = os.path.join(album_dir, "01 - Love Letter.mp3")
            with open(track_file, "wb") as f:
                f.write(b"ID3\x03\x00\x00\x00\x00\x00\x00Dummy audio data payload")

            # 2. Create a corrupted cover.jpg on device with gray filler at the bottom
            corrupted_cover = Image.new("RGB", (300, 300), color=(180, 40, 40))
            for y in range(200, 300):
                for x in range(300):
                    corrupted_cover.putpixel((x, y), (128, 128, 128))
            corrupt_path = os.path.join(album_dir, "cover.jpg")
            corrupted_cover.save(corrupt_path, format="JPEG", quality=85)

            # 3. Scan device albums and assert corruption detected
            albums = DeviceInspector.scan_device_albums(prof)
            self.assertEqual(len(albums), 1)
            self.assertTrue(albums[0].has_corrupted_cover)
            self.assertIn("Corrompida", albums[0].display_cover_info)

            # 4. Populate clean cover in isolated test cache
            clean_cover = Image.new("RGB", (300, 300), color=(50, 150, 220))
            cache_key = AlbumArtworkCache.get_cache_key(albums[0].artist, albums[0].album)
            clean_cache_path = os.path.join(AlbumArtworkCache.CACHE_DIR, f"{cache_key}.jpg")
            clean_cover.save(clean_cache_path, format="JPEG")
            AlbumArtworkCache._verified_clean.add(clean_cache_path)

            # 5. Repair artwork on device
            res = DeviceInspector.repair_all_device_artwork(prof)
            self.assertEqual(res["corrupted_found"], 1)
            self.assertEqual(res["repaired_count"], 1)

            # 6. Verify cover.jpg on device is now clean
            with Image.open(corrupt_path) as im:
                im.load()
                self.assertFalse(AlbumArtworkCache.is_corrupted_artwork(im))
                r, g, b = im.getpixel((150, 150))
                self.assertTrue(b > 150)

            # 7. Rescan device albums: should be clean
            albums_after = DeviceInspector.scan_device_albums(prof)
            self.assertFalse(albums_after[0].has_corrupted_cover)

            # 8. Test SyncEngine auto-healing for skipped tracks
            # Re-corrupt the cover on device
            corrupted_cover.save(corrupt_path, format="JPEG", quality=85)
            AlbumArtworkCache._verified_clean.discard(corrupt_path)

            # Set up a source track in library
            source_dir = os.path.join(self.temp_dir, "source_art")
            os.makedirs(source_dir, exist_ok=True)
            source_track_path = os.path.join(source_dir, "01.mp3")
            shutil.copy2(track_file, source_track_path)

            t = Track(
                file_path=source_track_path,
                artist="TestBandArt",
                album="TestAlbumArt",
                title="Love Letter",
                track_number=1,
                size_bytes=os.path.getsize(source_track_path),
                file_format="mp3"
            )

            engine = SyncEngine()
            plan = engine.create_sync_plan(prof, [t])
            self.assertEqual(len(plan.items_to_skip), 1)

            # Run execute_plan: it should detect corrupted cover.jpg on device and auto-heal it
            ok = engine.execute_plan(plan)
            self.assertTrue(ok)

            # Verify cover.jpg on device was healed by SyncEngine
            with Image.open(corrupt_path) as im:
                im.load()
                self.assertFalse(AlbumArtworkCache.is_corrupted_artwork(im))
        finally:
            AlbumArtworkCache.CACHE_DIR = orig_cache
            AlbumArtworkCache.LEGACY_CACHE_DIR = orig_legacy
            AlbumArtworkCache._verified_clean.clear()

    def test_ui_settings_persistence(self):
        import json
        config_dir = os.path.join(self.temp_dir, "config_batuque")
        settings_file = os.path.join(config_dir, "ui_settings.json")
        os.makedirs(config_dir, exist_ok=True)

        data = {"grid_cover_size": 160, "grid_fixed_columns": 6}
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        with open(settings_file, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["grid_cover_size"], 160)
        self.assertEqual(loaded["grid_fixed_columns"], 6)

    def test_shift_click_range_selection_and_deselection(self):
        """Tests that Shift+Click selects ranges, trims errors, and deselects."""
        from batuque.ui.main_window import MainWindow
        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('Adw', '1')
        from gi.repository import Gtk

        class MockWidget:
            def __init__(self):
                self._classes = set()
            def add_css_class(self, c):
                self._classes.add(c)
            def remove_css_class(self, c):
                self._classes.discard(c)

        class MockCheck:
            def __init__(self):
                self._active = False
            def get_active(self):
                return self._active
            def set_active(self, v):
                self._active = v
            def set_inconsistent(self, v):
                pass

        # Mock window instance
        class MockWin:
            _updating_checks = False
            _last_clicked_album_idx = None
            _last_shift_anchor = None
            _last_shift_state = True
            _last_shift_range = None
            def _update_selection_bar(self):
                pass

        win = MockWin()
        win.selected_track_ids = set()
        win._grid_album_items = []
        for i in range(10):
            win._grid_album_items.append({
                "album": {"album": f"Album {i}"},
                "track_ids": [i * 10 + 1, i * 10 + 2],
                "check": MockCheck(),
                "tile": MockWidget()
            })

        # Bind method
        win._handle_album_grid_click = MainWindow._handle_album_grid_click.__get__(win, MockWin)

        # 1. Click Item 2 without Shift -> should be selected
        win._handle_album_grid_click(2, is_shift=False)
        self.assertTrue(win._grid_album_items[2]["check"].get_active())
        self.assertIn(21, win.selected_track_ids)
        self.assertFalse(win._grid_album_items[3]["check"].get_active())

        # 2. Shift+Click Item 6 -> Items 2..6 must all be selected
        win._handle_album_grid_click(6, is_shift=True)
        for i in range(2, 7):
            self.assertTrue(win._grid_album_items[i]["check"].get_active(), f"Item {i} should be active")
            self.assertIn(i * 10 + 1, win.selected_track_ids)
        self.assertFalse(win._grid_album_items[7]["check"].get_active())

        # 3. User realizes they clicked wrong ("cliquei errado, era só até o 4!")
        # Shift+Click Item 4 -> Items 2..4 remain selected, Items 5 and 6 must be UNSELECTED!
        win._handle_album_grid_click(4, is_shift=True)
        for i in range(2, 5):
            self.assertTrue(win._grid_album_items[i]["check"].get_active(), f"Item {i} should be active")
            self.assertIn(i * 10 + 1, win.selected_track_ids)
        for i in (5, 6):
            self.assertFalse(win._grid_album_items[i]["check"].get_active(), f"Item {i} should be deselected")
            self.assertNotIn(i * 10 + 1, win.selected_track_ids)

        # 4. User unchecks Item 4 with single click
        win._handle_album_grid_click(4, is_shift=False)
        self.assertFalse(win._grid_album_items[4]["check"].get_active())
        self.assertNotIn(41, win.selected_track_ids)

        # 5. Shift+Click on Item 2 with anchor at 4 (which was deselected) -> 2..4 should all be DESELECTED!
        win._handle_album_grid_click(2, is_shift=True)
        for i in range(2, 5):
            self.assertFalse(win._grid_album_items[i]["check"].get_active(), f"Item {i} should be deselected")
            self.assertNotIn(i * 10 + 1, win.selected_track_ids)


if __name__ == "__main__":
    unittest.main()
