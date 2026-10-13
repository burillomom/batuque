"""
Automated Unit Tests for SoundBee.
"""

import unittest
import os
import tempfile
import shutil
from soundbee.core.models import Track, DeviceProfile, NASSource
from soundbee.core.database import LibraryDatabase
from soundbee.core.organizer import FileOrganizer
from soundbee.core.transcoder import AudioTranscoder
from soundbee.core.playlist import PlaylistExporter
from soundbee.core.sync_engine import SyncEngine
from soundbee.devices.manager import DeviceManager


class TestSoundBee(unittest.TestCase):
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
        from soundbee.core.nas import NASManager
        host, share, subfolder, base = NASManager.parse_smb_uri("smb://100.79.195.46/NAS-4TB/Musica")
        self.assertEqual(host, "100.79.195.46")
        self.assertEqual(share, "NAS-4TB")
        self.assertEqual(subfolder, "Musica")
        self.assertEqual(base, "smb://100.79.195.46/NAS-4TB")


if __name__ == "__main__":
    unittest.main()
