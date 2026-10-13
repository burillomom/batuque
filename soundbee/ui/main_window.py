"""
Main Application Window for SoundBee.
Adaptive Libadwaita layout with library browsing, NAS management, and device synchronization.
"""

import os
import threading
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib

from ..core.database import LibraryDatabase
from ..core.scanner import LibraryScanner
from ..core.sync_engine import SyncEngine
from ..core.models import Track, DeviceProfile, NASSource
from ..devices.detector import DeviceDetector, DetectedDevice
from ..devices.manager import DeviceManager
from .device_dialog import DeviceSettingsWindow
from .nas_dialog import NASConfigDialog


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.db = LibraryDatabase()
        self.dev_manager = DeviceManager()
        self.detector = DeviceDetector()
        self.sync_engine = SyncEngine()

        self.set_title("SoundBee")
        self.set_default_size(1050, 700)

        self.selected_device_profile: DeviceProfile = None

        self._build_ui()
        self._refresh_library()
        self._refresh_devices()
        self._refresh_nas_sources()

        # Listen for hotplugged USB drives / DAPs
        self.detector.add_change_listener(lambda: GLib.idle_add(self._refresh_devices))

    def _build_ui(self):
        # Main vertical box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # Header bar
        header = Adw.HeaderBar()
        main_box.append(header)

        # Search Entry in header
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Buscar por título, artista, álbum...")
        self.search_entry.connect("search-changed", self._on_search_changed)
        header.set_title_widget(self.search_entry)

        # Header buttons
        btn_add_nas = Gtk.Button(icon_name="network-server-symbolic")
        btn_add_nas.set_tooltip_text("Conectar servidor NAS")
        btn_add_nas.connect("clicked", self._on_add_nas_clicked)
        header.pack_end(btn_add_nas)

        btn_scan = Gtk.Button(icon_name="folder-open-symbolic")
        btn_scan.set_tooltip_text("Escanear pasta de músicas")
        btn_scan.connect("clicked", self._on_scan_folder_clicked)
        header.pack_end(btn_scan)

        # Paned layout (Sidebar + Main Content)
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(260)
        paned.set_vexpand(True)
        main_box.append(paned)

        # Sidebar
        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        paned.set_start_child(sidebar_scroll)

        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sidebar_box.set_margin_top(12)
        sidebar_box.set_margin_bottom(12)
        sidebar_box.set_margin_start(12)
        sidebar_box.set_margin_end(12)
        sidebar_scroll.set_child(sidebar_box)

        # Section: Library
        lbl_lib = Gtk.Label(label="BIBLIOTECA", xalign=0)
        lbl_lib.add_css_class("heading")
        sidebar_box.append(lbl_lib)

        self.btn_all_tracks = Gtk.Button(label="Todas as Músicas")
        self.btn_all_tracks.set_halign(Gtk.Align.FILL)
        self.btn_all_tracks.connect("clicked", lambda b: self._show_library_view())
        sidebar_box.append(self.btn_all_tracks)

        # Section: NAS Shares
        lbl_nas = Gtk.Label(label="SERVIDORES NAS", xalign=0)
        lbl_nas.set_margin_top(16)
        lbl_nas.add_css_class("heading")
        sidebar_box.append(lbl_nas)

        self.box_nas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        sidebar_box.append(self.box_nas)

        # Section: Devices
        lbl_dev = Gtk.Label(label="DISPOSITIVOS & PLAYERS", xalign=0)
        lbl_dev.set_margin_top(16)
        lbl_dev.add_css_class("heading")
        sidebar_box.append(lbl_dev)

        self.box_devices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        sidebar_box.append(self.box_devices)

        # Add Virtual Device button
        btn_virt = Gtk.Button(label="+ Nova Pasta de Destino")
        btn_virt.set_margin_top(8)
        btn_virt.connect("clicked", self._on_add_virtual_device_clicked)
        sidebar_box.append(btn_virt)

        # Content View (Stack: Library View vs Device View)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        paned.set_end_child(self.stack)

        # 1. Library Page
        lib_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack.add_named(lib_box, "library")

        # Track list view
        scroll_tracks = Gtk.ScrolledWindow()
        scroll_tracks.set_vexpand(True)
        lib_box.append(scroll_tracks)

        self.track_listbox = Gtk.ListBox()
        self.track_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        scroll_tracks.set_child(self.track_listbox)

        # Library status bar
        self.lbl_stats = Gtk.Label(label="Carregando biblioteca...", xalign=0)
        self.lbl_stats.set_margin_start(12)
        self.lbl_stats.set_margin_end(12)
        self.lbl_stats.set_margin_top(6)
        self.lbl_stats.set_margin_bottom(6)
        self.lbl_stats.add_css_class("dim-label")
        lib_box.append(self.lbl_stats)

        # 2. Device Page
        self.device_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.device_box.set_margin_start(24)
        self.device_box.set_margin_end(24)
        self.device_box.set_margin_top(20)
        self.device_box.set_margin_bottom(20)
        self.stack.add_named(self.device_box, "device")

        self.dev_title = Gtk.Label(label="Dispositivo", xalign=0)
        self.dev_title.add_css_class("title-1")
        self.device_box.append(self.dev_title)

        self.dev_info_group = Adw.PreferencesGroup()
        self.row_dev_path = Adw.ActionRow()
        self.row_dev_path.set_title("Ponto de Montagem")
        self.dev_info_group.add(self.row_dev_path)

        self.row_dev_codec = Adw.ActionRow()
        self.row_dev_codec.set_title("Configuração de Áudio")
        self.dev_info_group.add(self.row_dev_codec)

        self.row_dev_naming = Adw.ActionRow()
        self.row_dev_naming.set_title("Regra de Pastas")
        self.dev_info_group.add(self.row_dev_naming)

        self.device_box.append(self.dev_info_group)

        # Action Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.device_box.append(btn_box)

        btn_configure = Gtk.Button(label="Configurar Perfil deste Dispositivo")
        btn_configure.connect("clicked", self._on_configure_selected_device)
        btn_box.append(btn_configure)

        self.btn_sync_now = Gtk.Button(label="Sincronizar Agora")
        self.btn_sync_now.add_css_class("suggested-action")
        self.btn_sync_now.connect("clicked", self._on_start_sync)
        btn_box.append(self.btn_sync_now)

        # Sync Progress Area
        self.sync_progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.sync_progress_box.set_visible(False)
        self.device_box.append(self.sync_progress_box)

        self.lbl_sync_status = Gtk.Label(label="Preparando sincronização...", xalign=0)
        self.sync_progress_box.append(self.lbl_sync_status)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.sync_progress_box.append(self.progress_bar)

    def _show_library_view(self):
        self.stack.set_visible_child_name("library")

    def _refresh_library(self, search_query: str = ""):
        # Clear list
        while child := self.track_listbox.get_first_child():
            self.track_listbox.remove(child)

        tracks = self.db.search_tracks(search_query) if search_query else self.db.get_all_tracks()

        for t in tracks:
            row = Adw.ActionRow()
            title = t.display_title
            artist_album = f"{t.display_artist} — {t.display_album}"
            row.set_title(title)
            row.set_subtitle(artist_album)

            mins = int(t.duration_seconds // 60)
            secs = int(t.duration_seconds % 60)
            dur_str = f"{mins}:{secs:02d}"
            tech = f"{t.file_format.upper()} {t.bitrate_kbps}k  ({dur_str})"
            lbl_tech = Gtk.Label(label=tech)
            lbl_tech.add_css_class("dim-label")
            row.add_suffix(lbl_tech)

            self.track_listbox.append(row)

        stats = self.db.get_stats()
        gb = stats["size_bytes"] / (1024 ** 3)
        hours = stats["duration_seconds"] / 3600
        self.lbl_stats.set_text(
            f"{stats['tracks']} músicas • {stats['artists']} artistas • {stats['albums']} álbuns • {hours:.1f}h • {gb:.2f} GB"
        )

    def _refresh_devices(self):
        while child := self.box_devices.get_first_child():
            self.box_devices.remove(child)

        connected = self.detector.list_connected_devices()
        profiles = self.dev_manager.get_all_profiles()

        # Build list of devices
        for d in connected:
            prof = self.dev_manager.get_or_create_profile(d.device_id, d.name, d.root_path, d.device_type)
            btn = Gtk.Button(label=f"💾 {prof.name}")
            btn.set_halign(Gtk.Align.FILL)
            btn.connect("clicked", lambda b, p=prof: self._select_device(p))
            self.box_devices.append(btn)

        # Add saved virtual device profiles
        for p in profiles:
            if p.device_type == "virtual" and not any(c.device_id == p.device_id for c in connected):
                btn = Gtk.Button(label=f"📁 {p.name}")
                btn.set_halign(Gtk.Align.FILL)
                btn.connect("clicked", lambda b, prof=p: self._select_device(prof))
                self.box_devices.append(btn)

    def _refresh_nas_sources(self):
        while child := self.box_nas.get_first_child():
            self.box_nas.remove(child)

        sources = self.db.get_nas_sources()
        for s in sources:
            btn = Gtk.Button(label=f"🌐 {s.name}")
            btn.set_halign(Gtk.Align.FILL)
            btn.connect("clicked", lambda b, src=s: self._on_nas_clicked(src))
            self.box_nas.append(btn)

    def _select_device(self, profile: DeviceProfile):
        self.selected_device_profile = profile
        self.dev_title.set_text(profile.name)
        self.row_dev_path.set_subtitle(profile.root_path or "Desconectado")
        trans_txt = f"{profile.target_codec.upper()} {profile.target_bitrate} kbps" if profile.transcode_enabled else "Cópia direta (Sem conversão)"
        if profile.transcode_enabled and profile.convert_lossless_only:
            trans_txt += " (Apenas FLAC/WAV)"
        self.row_dev_codec.set_subtitle(trans_txt)
        self.row_dev_naming.set_subtitle(profile.naming_rule)

        self.sync_progress_box.set_visible(False)
        self.stack.set_visible_child_name("device")

    def _on_configure_selected_device(self, *args):
        if not self.selected_device_profile:
            return

        def on_saved(saved_prof):
            self.dev_manager.save_profile(saved_prof)
            self._select_device(saved_prof)

        dlg = DeviceSettingsWindow(self.selected_device_profile, on_save_callback=on_saved, transient_for=self)
        dlg.present()

    def _on_start_sync(self, *args):
        if not self.selected_device_profile:
            return

        prof = self.selected_device_profile
        tracks = self.db.get_all_tracks()
        if not tracks:
            return

        self.sync_progress_box.set_visible(True)
        self.btn_sync_now.set_sensitive(False)
        self.progress_bar.set_fraction(0.0)
        self.lbl_sync_status.set_text("Calculando diferenças e arquivos...")

        def run_thread():
            plan = self.sync_engine.create_sync_plan(prof, tracks)
            total_ops = len(plan.items_to_copy) + len(plan.items_to_transcode) + len(plan.items_to_delete)

            def on_prog(curr, tot, text):
                fraction = curr / max(tot, 1)
                GLib.idle_add(lambda: self._update_sync_ui(fraction, text))

            self.sync_engine.execute_plan(plan, on_progress=on_prog)
            GLib.idle_add(self._sync_finished)

        threading.Thread(target=run_thread, daemon=True).start()

    def _update_sync_ui(self, fraction, text):
        self.progress_bar.set_fraction(fraction)
        self.lbl_sync_status.set_text(text)

    def _sync_finished(self):
        self.progress_bar.set_fraction(1.0)
        self.lbl_sync_status.set_text("Sincronização concluída com sucesso!")
        self.btn_sync_now.set_sensitive(True)

    def _on_search_changed(self, entry):
        q = entry.get_text()
        self._refresh_library(search_query=q)

    @staticmethod
    def _is_accessible_path(p: Optional[str]) -> bool:
        if not p:
            return False
        return os.path.isdir(p) or p.startswith(("smb://", "nfs://", "sftp://", "davs://", "ftp://"))

    def _on_add_nas_clicked(self, *args):
        def on_saved(src: NASSource):
            self.db.upsert_nas_source(src)
            self._refresh_nas_sources()
            target_path = src.mount_path or src.uri
            if self._is_accessible_path(target_path):
                self._scan_in_background(target_path, source_id=src.id)

        dlg = NASConfigDialog(on_saved_callback=on_saved, transient_for=self)
        dlg.present()

    def _on_nas_clicked(self, src: NASSource):
        target_path = src.mount_path or src.uri
        if self._is_accessible_path(target_path):
            self._scan_in_background(target_path, source_id=src.id)
        else:
            self.lbl_stats.set_text(f"Conectando ao NAS '{src.name}'...")
            def bg_mount():
                ok, mpath, msg = NASManager.mount_share(src)
                if ok and self._is_accessible_path(mpath):
                    self._scan_in_background(mpath, source_id=src.id)
                else:
                    GLib.idle_add(lambda: self.lbl_stats.set_text(f"Falha ao conectar ao NAS: {msg}"))
            threading.Thread(target=bg_mount, daemon=True).start()

    def _on_scan_folder_clicked(self, *args):
        dlg = Gtk.FileDialog()
        dlg.set_title("Selecionar pasta de músicas")
        dlg.select_folder(self, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                path = folder.get_path()
                self._scan_in_background(path, source_id="local")
        except Exception:
            pass

    def _on_add_virtual_device_clicked(self, *args):
        dlg = Gtk.FileDialog()
        dlg.set_title("Selecionar pasta de destino para dispositivo virtual")
        dlg.select_folder(self, None, self._on_virtual_folder_selected)

    def _on_virtual_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                path = folder.get_path()
                name = os.path.basename(path) or "Pasta Virtual"
                dev_id = "virt_" + os.urandom(4).hex()
                prof = DeviceProfile(
                    device_id=dev_id,
                    name=name,
                    device_type="virtual",
                    root_path=path
                )
                self.dev_manager.save_profile(prof)
                self._refresh_devices()
                self._select_device(prof)
        except Exception:
            pass

    def _scan_in_background(self, path: str, source_id: str):
        self.lbl_stats.set_text(f"Indexando '{path}'...")
        scanner = LibraryScanner(self.db)

        def thread_target():
            def on_prog(curr, tot, name):
                GLib.idle_add(lambda: self.lbl_stats.set_text(f"Indexando [{curr}/{tot}]: {name}"))

            count = scanner.scan_directory(path, source_id=source_id, on_progress=on_prog)
            GLib.idle_add(lambda: self._refresh_library())

        threading.Thread(target=thread_target, daemon=True).start()
