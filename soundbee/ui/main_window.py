"""
Main Application Window for SoundBee.
Adaptive Libadwaita layout with library browsing, NAS management, and device synchronization.
Features rich library sorting and grouping by Albums, Artists, Years, Genres, and Tracks.
"""

import os
import threading
from typing import Optional, Dict, Any, List, Set, Tuple
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib

from ..core.database import LibraryDatabase
from ..core.scanner import LibraryScanner
from ..core.sync_engine import SyncEngine
from ..core.nas import NASManager
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
        self.set_default_size(1100, 750)

        self.selected_device_profile: Optional[DeviceProfile] = None
        self.current_view_mode = "albums"  # "albums", "artists", "years", "genres", "tracks"
        self.search_query = ""
        self.sort_criterion = "artist"
        self.sort_descending = False

        self.filter_artist: Optional[str] = None
        self.filter_genre: Optional[str] = None
        self.filter_year: Optional[int] = None

        # Selection and quick-send management
        self.selected_track_ids: Set[int] = set()
        self.available_devices: List[DeviceProfile] = []
        self.active_target_device: Optional[DeviceProfile] = None
        self._updating_checks: bool = False

        self._build_ui()
        self._refresh_library()
        self._refresh_devices()
        self._refresh_nas_sources()

        # Listen for hotplugged USB drives / DAPs
        self.detector.add_change_listener(lambda: GLib.idle_add(self._refresh_devices))

    def _build_ui(self):
        # Toast overlay for floating feedback notifications
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        # Main vertical box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(main_box)

        # Header bar
        header = Adw.HeaderBar()
        main_box.append(header)

        # Quick selection buttons in header
        btn_sel_all = Gtk.Button(icon_name="edit-select-all-symbolic")
        btn_sel_all.set_tooltip_text("Selecionar todas as músicas visíveis")
        btn_sel_all.connect("clicked", lambda b: self._select_visible_tracks())
        header.pack_start(btn_sel_all)

        btn_clear_sel = Gtk.Button(icon_name="edit-clear-all-symbolic")
        btn_clear_sel.set_tooltip_text("Limpar seleção de músicas")
        btn_clear_sel.connect("clicked", lambda b: self._clear_selection())
        header.pack_start(btn_clear_sel)

        # Search Entry in header
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Buscar por título, artista, álbum, ano...")
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

        self.btn_nav_albums = Gtk.Button(label="💿 Álbuns")
        self.btn_nav_albums.set_halign(Gtk.Align.FILL)
        self.btn_nav_albums.connect("clicked", lambda b: self._switch_view("albums"))
        sidebar_box.append(self.btn_nav_albums)

        self.btn_nav_artists = Gtk.Button(label="🎤 Artistas")
        self.btn_nav_artists.set_halign(Gtk.Align.FILL)
        self.btn_nav_artists.connect("clicked", lambda b: self._switch_view("artists"))
        sidebar_box.append(self.btn_nav_artists)

        self.btn_nav_years = Gtk.Button(label="📅 Anos")
        self.btn_nav_years.set_halign(Gtk.Align.FILL)
        self.btn_nav_years.connect("clicked", lambda b: self._switch_view("years"))
        sidebar_box.append(self.btn_nav_years)

        self.btn_nav_genres = Gtk.Button(label="🎸 Gêneros")
        self.btn_nav_genres.set_halign(Gtk.Align.FILL)
        self.btn_nav_genres.connect("clicked", lambda b: self._switch_view("genres"))
        sidebar_box.append(self.btn_nav_genres)

        self.btn_nav_tracks = Gtk.Button(label="🎵 Todas as Músicas")
        self.btn_nav_tracks.set_halign(Gtk.Align.FILL)
        self.btn_nav_tracks.connect("clicked", lambda b: self._switch_view("tracks"))
        sidebar_box.append(self.btn_nav_tracks)

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

        # --- 1. Library Page ---
        lib_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack.add_named(lib_box, "library")

        # Top Toolbar (View Switcher + Sort Menu)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_top(10)
        toolbar.set_margin_bottom(10)
        toolbar.set_margin_start(16)
        toolbar.set_margin_end(16)
        lib_box.append(toolbar)

        # Linked Toggle Buttons for View Modes
        view_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        view_box.add_css_class("linked")
        toolbar.append(view_box)

        self.btn_view_albums = Gtk.ToggleButton(label="Álbuns")
        self.btn_view_albums.set_active(True)
        self.btn_view_albums.connect("toggled", lambda b: b.get_active() and self._switch_view("albums"))
        view_box.append(self.btn_view_albums)

        self.btn_view_artists = Gtk.ToggleButton(label="Artistas")
        self.btn_view_artists.connect("toggled", lambda b: b.get_active() and self._switch_view("artists"))
        view_box.append(self.btn_view_artists)

        self.btn_view_years = Gtk.ToggleButton(label="Anos")
        self.btn_view_years.connect("toggled", lambda b: b.get_active() and self._switch_view("years"))
        view_box.append(self.btn_view_years)

        self.btn_view_genres = Gtk.ToggleButton(label="Gêneros")
        self.btn_view_genres.connect("toggled", lambda b: b.get_active() and self._switch_view("genres"))
        view_box.append(self.btn_view_genres)

        self.btn_view_tracks = Gtk.ToggleButton(label="Faixas")
        self.btn_view_tracks.connect("toggled", lambda b: b.get_active() and self._switch_view("tracks"))
        view_box.append(self.btn_view_tracks)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        toolbar.append(spacer)

        # Sort Dropdown menu
        lbl_sort = Gtk.Label(label="Ordenar:")
        lbl_sort.add_css_class("dim-label")
        toolbar.append(lbl_sort)

        self.sort_dropdown = Gtk.DropDown.new_from_strings([
            "Artista (A-Z)",
            "Artista (Z-A)",
            "Álbum (A-Z)",
            "Álbum (Z-A)",
            "Ano (Mais recente)",
            "Ano (Mais antigo)",
            "Título da Música (A-Z)",
            "Duração (Mais longa)"
        ])
        self.sort_dropdown.set_selected(0)
        self.sort_dropdown.connect("notify::selected", self._on_sort_changed)
        toolbar.append(self.sort_dropdown)

        # Inner Library Stack for different viewing styles
        self.lib_stack = Gtk.Stack()
        self.lib_stack.set_vexpand(True)
        self.lib_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        lib_box.append(self.lib_stack)

        # View A: Albums (Album & Tracks Grouped View)
        scroll_albums = Gtk.ScrolledWindow()
        scroll_albums.set_vexpand(True)
        self.albums_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.albums_container.set_margin_start(16)
        self.albums_container.set_margin_end(16)
        self.albums_container.set_margin_top(12)
        self.albums_container.set_margin_bottom(16)
        scroll_albums.set_child(self.albums_container)
        self.lib_stack.add_named(scroll_albums, "albums")

        # View B: Artists Master-Detail Paned
        self.paned_artists = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned_artists.set_position(240)
        scroll_art_list = Gtk.ScrolledWindow()
        self.artists_listbox = Gtk.ListBox()
        self.artists_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.artists_listbox.connect("row-selected", self._on_artist_selected)
        scroll_art_list.set_child(self.artists_listbox)
        self.paned_artists.set_start_child(scroll_art_list)

        scroll_art_detail = Gtk.ScrolledWindow()
        self.artist_detail_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.artist_detail_container.set_margin_start(16)
        self.artist_detail_container.set_margin_end(16)
        self.artist_detail_container.set_margin_top(12)
        self.artist_detail_container.set_margin_bottom(16)
        scroll_art_detail.set_child(self.artist_detail_container)
        self.paned_artists.set_end_child(scroll_art_detail)
        self.lib_stack.add_named(self.paned_artists, "artists")

        # View C: Years Timeline View
        scroll_years = Gtk.ScrolledWindow()
        scroll_years.set_vexpand(True)
        self.years_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.years_container.set_margin_start(16)
        self.years_container.set_margin_end(16)
        self.years_container.set_margin_top(12)
        self.years_container.set_margin_bottom(16)
        scroll_years.set_child(self.years_container)
        self.lib_stack.add_named(scroll_years, "years")

        # View D: Genres Master-Detail Paned
        self.paned_genres = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned_genres.set_position(200)
        scroll_gen_list = Gtk.ScrolledWindow()
        self.genres_listbox = Gtk.ListBox()
        self.genres_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.genres_listbox.connect("row-selected", self._on_genre_selected)
        scroll_gen_list.set_child(self.genres_listbox)
        self.paned_genres.set_start_child(scroll_gen_list)

        scroll_gen_detail = Gtk.ScrolledWindow()
        self.genre_detail_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.genre_detail_container.set_margin_start(16)
        self.genre_detail_container.set_margin_end(16)
        self.genre_detail_container.set_margin_top(12)
        self.genre_detail_container.set_margin_bottom(16)
        scroll_gen_detail.set_child(self.genre_detail_container)
        self.paned_genres.set_end_child(scroll_gen_detail)
        self.lib_stack.add_named(self.paned_genres, "genres")

        # View E: Tracks Flat Table List
        scroll_tracks = Gtk.ScrolledWindow()
        scroll_tracks.set_vexpand(True)
        self.track_listbox = Gtk.ListBox()
        self.track_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        scroll_tracks.set_child(self.track_listbox)
        self.lib_stack.add_named(scroll_tracks, "tracks")

        # Selection Action Bar (Sticky at bottom of library)
        self.selection_bar = Gtk.ActionBar()
        self.selection_bar.set_revealed(False)
        lib_box.append(self.selection_bar)

        # Start box: Selection count & Clear button
        sel_start_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.lbl_selection = Gtk.Label(label="0 faixas selecionadas")
        self.lbl_selection.add_css_class("heading")
        sel_start_box.append(self.lbl_selection)

        btn_clear = Gtk.Button(label="Limpar")
        btn_clear.add_css_class("flat")
        btn_clear.connect("clicked", lambda b: self._clear_selection())
        sel_start_box.append(btn_clear)
        self.selection_bar.pack_start(sel_start_box)

        # Center box: Progress bar during live transfer
        self.sel_progress_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.sel_progress_bar = Gtk.ProgressBar()
        self.sel_progress_bar.set_size_request(220, -1)
        self.sel_progress_bar.set_show_text(True)
        self.sel_progress_label = Gtk.Label(label="")
        self.sel_progress_label.add_css_class("dim-label")
        self.sel_progress_box.append(self.sel_progress_bar)
        self.sel_progress_box.append(self.sel_progress_label)
        self.sel_progress_box.set_visible(False)
        self.selection_bar.set_center_widget(self.sel_progress_box)

        # End box: Target device, Save to profile, and Send Selected
        sel_end_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl_target = Gtk.Label(label="Destino:")
        lbl_target.add_css_class("dim-label")
        sel_end_box.append(lbl_target)

        self.combo_target_device = Gtk.DropDown()
        self.combo_target_device.connect("notify::selected", self._on_target_device_changed)
        sel_end_box.append(self.combo_target_device)

        btn_save_to_device = Gtk.Button(label="💾 Salvar no Perfil")
        btn_save_to_device.set_tooltip_text("Salva as faixas selecionadas no perfil do dispositivo para sincronizações futuras")
        btn_save_to_device.connect("clicked", self._on_save_selection_to_device)
        sel_end_box.append(btn_save_to_device)

        self.btn_send_selected = Gtk.Button(label="🚀 Enviar Selecionados")
        self.btn_send_selected.add_css_class("suggested-action")
        self.btn_send_selected.set_tooltip_text("Copia ou converte as faixas selecionadas diretamente para o dispositivo")
        self.btn_send_selected.connect("clicked", self._on_send_selected_clicked)
        sel_end_box.append(self.btn_send_selected)

        self.selection_bar.pack_end(sel_end_box)

        # Library status bar
        self.lbl_stats = Gtk.Label(label="Carregando biblioteca...", xalign=0)
        self.lbl_stats.set_margin_start(16)
        self.lbl_stats.set_margin_end(16)
        self.lbl_stats.set_margin_top(6)
        self.lbl_stats.set_margin_bottom(6)
        self.lbl_stats.add_css_class("dim-label")
        lib_box.append(self.lbl_stats)

        # --- 2. Device Page ---
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

        self.row_dev_scope = Adw.ActionRow()
        self.row_dev_scope.set_title("Modo de Sincronização")
        self.dev_info_group.add(self.row_dev_scope)

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

    def _switch_view(self, mode: str):
        self.current_view_mode = mode
        self.btn_view_albums.set_active(mode == "albums")
        self.btn_view_artists.set_active(mode == "artists")
        self.btn_view_years.set_active(mode == "years")
        self.btn_view_genres.set_active(mode == "genres")
        self.btn_view_tracks.set_active(mode == "tracks")

        self.lib_stack.set_visible_child_name(mode)
        self.stack.set_visible_child_name("library")
        self._refresh_library()

    def _on_sort_changed(self, dropdown, *args):
        idx = dropdown.get_selected()
        sort_map = [
            ("artist", False),
            ("artist", True),
            ("album", False),
            ("album", True),
            ("year", True),
            ("year", False),
            ("title", False),
            ("duration", True)
        ]
        if idx < len(sort_map):
            self.sort_criterion, self.sort_descending = sort_map[idx]
            self._refresh_library()

    def _on_search_changed(self, entry):
        self.search_query = entry.get_text()
        self._refresh_library()

    def _refresh_library(self):
        """Refreshes the currently active view mode."""
        if self.current_view_mode == "albums":
            self._render_albums_view()
        elif self.current_view_mode == "artists":
            self._render_artists_view()
        elif self.current_view_mode == "years":
            self._render_years_view()
        elif self.current_view_mode == "genres":
            self._render_genres_view()
        elif self.current_view_mode == "tracks":
            self._render_tracks_view()

        # Update stats
        stats = self.db.get_stats()
        gb = stats["size_bytes"] / (1024 ** 3)
        hours = stats["duration_seconds"] / 3600
        self.lbl_stats.set_text(
            f"{stats['tracks']} músicas • {stats['artists']} artistas • {stats['albums']} álbuns • {hours:.1f}h • {gb:.2f} GB"
        )

    def _update_album_check_state(self, check_album: Gtk.CheckButton, album_track_ids: List[int]):
        selected = [tid for tid in album_track_ids if tid in self.selected_track_ids]
        self._updating_checks = True
        if len(selected) == len(album_track_ids) and len(album_track_ids) > 0:
            check_album.set_inconsistent(False)
            check_album.set_active(True)
        elif len(selected) > 0:
            check_album.set_inconsistent(True)
            check_album.set_active(False)
        else:
            check_album.set_inconsistent(False)
            check_album.set_active(False)
        self._updating_checks = False

    def _create_album_card(self, album: Dict[str, Any]) -> Gtk.Widget:
        """Constructs an elegant Album Card with header, selection checkboxes, quick send, and ordered tracklist."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("card")
        card.set_margin_bottom(8)

        album_tracks: List[Track] = album["tracks"]
        album_track_ids = [t.id for t in album_tracks if t.id is not None]

        # Header Box
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header_box.set_margin_start(12)
        header_box.set_margin_end(12)
        header_box.set_margin_top(12)
        header_box.set_margin_bottom(8)
        card.append(header_box)

        # Album Selection Checkbox
        check_album = Gtk.CheckButton()
        check_album.set_tooltip_text("Selecionar todas as faixas deste álbum")
        selected_in_album = [tid for tid in album_track_ids if tid in self.selected_track_ids]
        if selected_in_album and len(selected_in_album) == len(album_track_ids):
            check_album.set_active(True)
        elif selected_in_album:
            check_album.set_inconsistent(True)
        header_box.append(check_album)

        # Album icon / cover thumbnail
        img = Gtk.Image.new_from_icon_name("media-optical-symbolic")
        img.set_pixel_size(44)
        header_box.append(img)

        # Title & Artist Column
        text_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_col.set_hexpand(True)
        header_box.append(text_col)

        lbl_album = Gtk.Label(label=album["album"], xalign=0)
        lbl_album.add_css_class("title-3")
        text_col.append(lbl_album)

        year_str = f" • {album['year']}" if album["year"] else ""
        mins_total = int(album["duration_seconds"] // 60)
        sub_str = f"{album['artist']}{year_str} • {album['track_count']} faixas ({mins_total} min)"
        lbl_sub = Gtk.Label(label=sub_str, xalign=0)
        lbl_sub.add_css_class("dim-label")
        text_col.append(lbl_sub)

        # Genre pill badge if present
        if album.get("genre"):
            genre_lbl = Gtk.Label(label=album["genre"].split("\x00")[0])
            genre_lbl.add_css_class("dim-label")
            header_box.append(genre_lbl)

        # Quick Send Album Button
        btn_send_album = Gtk.Button()
        btn_send_album.set_tooltip_text(f"Enviar álbum '{album['album']}' para o dispositivo de destino")
        btn_send_album.add_css_class("flat")
        box_send_alb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        img_send = Gtk.Image.new_from_icon_name("document-send-symbolic")
        lbl_send = Gtk.Label(label="Enviar Álbum")
        box_send_alb.append(img_send)
        box_send_alb.append(lbl_send)
        btn_send_album.set_child(box_send_alb)
        btn_send_album.connect(
            "clicked",
            lambda b, al=album: self._send_tracks_to_device(al["tracks"], title=f"Álbum '{al['album']}'")
        )
        header_box.append(btn_send_album)

        # Tracklist
        track_list = Gtk.ListBox()
        track_list.add_css_class("boxed-list")
        track_list.set_margin_start(12)
        track_list.set_margin_end(12)
        track_list.set_margin_bottom(12)
        card.append(track_list)

        track_cboxes: List[Tuple[Track, Gtk.CheckButton]] = []

        for t in album["tracks"]:
            row = Adw.ActionRow()
            track_num = f"{t.track_number:02d}" if t.track_number else "•"
            row.set_title(t.display_title)

            # Checkbox for this track
            check_t = Gtk.CheckButton()
            check_t.set_active(t.id in self.selected_track_ids)
            track_cboxes.append((t, check_t))

            def on_track_toggled(btn, trk=t, ca=check_album, t_ids=album_track_ids):
                if self._updating_checks:
                    return
                if btn.get_active():
                    self.selected_track_ids.add(trk.id)
                else:
                    self.selected_track_ids.discard(trk.id)
                self._update_album_check_state(ca, t_ids)
                self._update_selection_bar()

            check_t.connect("toggled", on_track_toggled)
            row.add_prefix(check_t)

            lbl_num = Gtk.Label(label=track_num)
            lbl_num.add_css_class("dim-label")
            row.add_prefix(lbl_num)

            mins = int(t.duration_seconds // 60)
            secs = int(t.duration_seconds % 60)
            dur_str = f"{mins}:{secs:02d}"
            lbl_dur = Gtk.Label(label=f"{t.file_format.upper()} {dur_str}")
            lbl_dur.add_css_class("dim-label")
            row.add_suffix(lbl_dur)

            # Quick send button for single track
            btn_send_t = Gtk.Button.new_from_icon_name("document-send-symbolic")
            btn_send_t.set_tooltip_text(f"Enviar '{t.display_title}' para o dispositivo")
            btn_send_t.add_css_class("flat")
            btn_send_t.connect(
                "clicked",
                lambda b, trk=t: self._send_tracks_to_device([trk], title=f"'{trk.display_title}'")
            )
            row.add_suffix(btn_send_t)

            track_list.append(row)

        def on_album_toggled(btn, cboxes=track_cboxes):
            if self._updating_checks:
                return
            self._updating_checks = True
            is_active = btn.get_active()
            btn.set_inconsistent(False)
            for trk, cb in cboxes:
                cb.set_active(is_active)
                if is_active:
                    self.selected_track_ids.add(trk.id)
                else:
                    self.selected_track_ids.discard(trk.id)
            self._updating_checks = False
            self._update_selection_bar()

        check_album.connect("toggled", on_album_toggled)

        return card

    def _render_albums_view(self):
        while child := self.albums_container.get_first_child():
            self.albums_container.remove(child)

        albums = self.db.get_albums_grouped(
            order_by=self.sort_criterion,
            order_desc=self.sort_descending,
            search_query=self.search_query
        )

        for alb in albums:
            card = self._create_album_card(alb)
            self.albums_container.append(card)

    def _render_artists_view(self):
        while child := self.artists_listbox.get_first_child():
            self.artists_listbox.remove(child)

        artists = self.db.get_artists_summary(search_query=self.search_query)
        for art in artists:
            row = Adw.ActionRow()
            row.set_title(art["artist_name"])
            row.set_subtitle(f"{art['album_count']} álbuns • {art['track_count']} músicas")
            row.artist_name = art["artist_name"]
            self.artists_listbox.append(row)

        # Select first artist if none selected
        first_row = self.artists_listbox.get_row_at_index(0)
        if first_row:
            self.artists_listbox.select_row(first_row)
            self._render_artist_detail(first_row.artist_name)

    def _on_artist_selected(self, listbox, row):
        if row and hasattr(row, "artist_name"):
            self._render_artist_detail(row.artist_name)

    def _render_artist_detail(self, artist_name: str):
        while child := self.artist_detail_container.get_first_child():
            self.artist_detail_container.remove(child)

        albums = self.db.get_albums_grouped(
            order_by="year",
            order_desc=True,
            filter_artist=artist_name,
            search_query=self.search_query
        )

        header = Gtk.Label(label=artist_name, xalign=0)
        header.add_css_class("title-1")
        self.artist_detail_container.append(header)

        for alb in albums:
            card = self._create_album_card(alb)
            self.artist_detail_container.append(card)

    def _render_years_view(self):
        while child := self.years_container.get_first_child():
            self.years_container.remove(child)

        years_summary = self.db.get_years_summary(search_query=self.search_query)
        all_albums = self.db.get_albums_grouped(order_by="album", search_query=self.search_query)
        albums_by_year: Dict[int, List[Dict[str, Any]]] = {}
        for alb in all_albums:
            y = alb["year"]
            albums_by_year.setdefault(y, []).append(alb)

        for y_item in years_summary:
            year_val = y_item["year_val"]
            year_label = str(year_val) if year_val else "Ano Desconhecido"

            group_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            group_box.set_margin_bottom(12)

            lbl_header = Gtk.Label(label=f"📅 {year_label} ({y_item['album_count']} álbuns)", xalign=0)
            lbl_header.add_css_class("title-2")
            group_box.append(lbl_header)

            matching_albums = albums_by_year.get(year_val, [])
            for alb in matching_albums:
                card = self._create_album_card(alb)
                group_box.append(card)

            self.years_container.append(group_box)

    def _render_genres_view(self):
        while child := self.genres_listbox.get_first_child():
            self.genres_listbox.remove(child)

        genres = self.db.get_genres_summary(search_query=self.search_query)
        for gen in genres:
            row = Adw.ActionRow()
            row.set_title(gen["genre_name"])
            row.set_subtitle(f"{gen['track_count']} músicas")
            row.genre_name = gen["genre_name"]
            self.genres_listbox.append(row)

        first_row = self.genres_listbox.get_row_at_index(0)
        if first_row:
            self.genres_listbox.select_row(first_row)
            self._render_genre_detail(first_row.genre_name)

    def _on_genre_selected(self, listbox, row):
        if row and hasattr(row, "genre_name"):
            self._render_genre_detail(row.genre_name)

    def _render_genre_detail(self, genre_name: str):
        while child := self.genre_detail_container.get_first_child():
            self.genre_detail_container.remove(child)

        header = Gtk.Label(label=f"🎸 {genre_name}", xalign=0)
        header.add_css_class("title-1")
        self.genre_detail_container.append(header)

        filter_gen = genre_name if genre_name != "Sem Gênero" else ""
        albums = self.db.get_albums_grouped(
            order_by="album",
            filter_genre=filter_gen,
            search_query=self.search_query
        )

        for alb in albums:
            card = self._create_album_card(alb)
            self.genre_detail_container.append(card)

    def _render_tracks_view(self):
        while child := self.track_listbox.get_first_child():
            self.track_listbox.remove(child)

        tracks = self.db.get_tracks_sorted(
            order_by=self.sort_criterion,
            order_desc=self.sort_descending,
            search_query=self.search_query
        )

        for t in tracks:
            row = Adw.ActionRow()
            title = t.display_title
            artist_album = f"{t.display_artist} — {t.display_album}"
            row.set_title(title)
            row.set_subtitle(artist_album)

            # Checkbox
            check_t = Gtk.CheckButton()
            check_t.set_active(t.id in self.selected_track_ids)

            def on_t_toggle(btn, trk=t):
                if self._updating_checks:
                    return
                if btn.get_active():
                    self.selected_track_ids.add(trk.id)
                else:
                    self.selected_track_ids.discard(trk.id)
                self._update_selection_bar()

            check_t.connect("toggled", on_t_toggle)
            row.add_prefix(check_t)

            mins = int(t.duration_seconds // 60)
            secs = int(t.duration_seconds % 60)
            dur_str = f"{mins}:{secs:02d}"
            tech = f"{t.file_format.upper()} {t.bitrate_kbps}k  ({dur_str})"
            lbl_tech = Gtk.Label(label=tech)
            lbl_tech.add_css_class("dim-label")
            row.add_suffix(lbl_tech)

            # Quick send button
            btn_send = Gtk.Button.new_from_icon_name("document-send-symbolic")
            btn_send.set_tooltip_text(f"Enviar '{t.display_title}' para o dispositivo")
            btn_send.add_css_class("flat")
            btn_send.connect(
                "clicked",
                lambda b, trk=t: self._send_tracks_to_device([trk], title=f"'{trk.display_title}'")
            )
            row.add_suffix(btn_send)

            self.track_listbox.append(row)

    def _update_selection_bar(self):
        count = len(self.selected_track_ids)
        if count == 0:
            if not self.sel_progress_box.get_visible():
                self.selection_bar.set_revealed(False)
            return

        self.lbl_selection.set_text(f"{count} faixa{'s' if count > 1 else ''} selecionada{'s' if count > 1 else ''}")
        self.selection_bar.set_revealed(True)

    def _clear_selection(self):
        self.selected_track_ids.clear()
        self._refresh_library()
        self._update_selection_bar()
        self.toast_overlay.add_toast(Adw.Toast.new("Seleção desmarcada"))

    def _select_visible_tracks(self):
        tracks = self.db.get_tracks_sorted(
            order_by=self.sort_criterion,
            order_desc=self.sort_descending,
            search_query=self.search_query,
            filter_artist=self.filter_artist,
            filter_year=self.filter_year,
            filter_genre=self.filter_genre
        )
        for t in tracks:
            if t.id is not None:
                self.selected_track_ids.add(t.id)
        self._refresh_library()
        self._update_selection_bar()
        self.toast_overlay.add_toast(Adw.Toast.new(f"{len(self.selected_track_ids)} faixas selecionadas"))

    def _update_target_device_dropdown(self):
        if not self.available_devices:
            names = ["Nenhum dispositivo (Clique em Enviar para criar)"]
            self.combo_target_device.set_model(Gtk.StringList.new(names))
            self.active_target_device = None
            return

        names = [f"💾 {d.name}" for d in self.available_devices]
        self.combo_target_device.set_model(Gtk.StringList.new(names))

        if self.active_target_device and self.active_target_device in self.available_devices:
            idx = self.available_devices.index(self.active_target_device)
            self.combo_target_device.set_selected(idx)
        else:
            self.active_target_device = self.available_devices[0]
            self.combo_target_device.set_selected(0)

    def _on_target_device_changed(self, dropdown, *args):
        idx = dropdown.get_selected()
        if 0 <= idx < len(self.available_devices):
            self.active_target_device = self.available_devices[idx]

    def _on_save_selection_to_device(self, *args):
        target = self.active_target_device or self.selected_device_profile
        if not target:
            self.toast_overlay.add_toast(Adw.Toast.new("Nenhum dispositivo disponível para salvar."))
            return
        target.selected_track_ids = list(self.selected_track_ids)
        target.sync_all_library = False
        self.dev_manager.save_profile(target)
        self.toast_overlay.add_toast(
            Adw.Toast.new(f"💾 {len(self.selected_track_ids)} faixas salvas no perfil de '{target.name}'!")
        )

    def _on_send_selected_clicked(self, *args):
        if not self.selected_track_ids:
            self.toast_overlay.add_toast(Adw.Toast.new("Nenhuma faixa selecionada"))
            return
        all_tracks = self.db.get_all_tracks()
        tracks_to_send = [t for t in all_tracks if t.id in self.selected_track_ids]
        count = len(tracks_to_send)
        self._send_tracks_to_device(
            tracks_to_send,
            title=f"{count} faixa{'s' if count > 1 else ''} selecionada{'s' if count > 1 else ''}"
        )

    def _send_tracks_to_device(self, tracks: List[Track], title: str = "Faixas"):
        target = self.active_target_device or self.selected_device_profile
        if not target:
            if self.available_devices:
                target = self.available_devices[0]
                self.active_target_device = target
            else:
                self._prompt_destination_and_send(tracks, title)
                return

        if not os.path.isdir(target.root_path):
            self.toast_overlay.add_toast(
                Adw.Toast.new(f"Aviso: Destino '{target.name}' ({target.root_path}) não está acessível.")
            )
            return

        self.toast_overlay.add_toast(Adw.Toast.new(f"Iniciando envio de {title} para '{target.name}'..."))
        self.sel_progress_box.set_visible(True)
        self.sel_progress_bar.set_fraction(0.0)
        self.sel_progress_label.set_text(f"Preparando envio para {target.name}...")
        self.selection_bar.set_revealed(True)

        def bg_send():
            plan = self.sync_engine.create_sync_plan(target, tracks, allow_delete=False)
            total = len(plan.items_to_copy) + len(plan.items_to_transcode)
            if total == 0:
                GLib.idle_add(lambda: self._on_send_completed(target, title, "Todos os arquivos já estavam atualizados."))
                return

            def on_prog(curr, tot, text):
                fraction = curr / max(tot, 1)
                GLib.idle_add(lambda: self._update_send_progress(fraction, text))

            success = self.sync_engine.execute_plan(plan, on_progress=on_prog)
            msg = f"Enviado {total} arquivo(s) com sucesso!" if success else "Envio cancelado ou com erros."
            GLib.idle_add(lambda: self._on_send_completed(target, title, msg))

        threading.Thread(target=bg_send, daemon=True).start()

    def _prompt_destination_and_send(self, tracks: List[Track], title: str):
        dlg = Gtk.FileDialog()
        dlg.set_title("Escolher pasta de destino para envio (Pen drive / Dispositivo)")

        def on_folder(dialog, result):
            try:
                folder = dialog.select_folder_finish(result)
                if folder:
                    path = folder.get_path()
                    name = os.path.basename(path) or "Destino"
                    dev_id = "virt_" + os.urandom(4).hex()
                    prof = DeviceProfile(
                        device_id=dev_id,
                        name=name,
                        device_type="virtual",
                        root_path=path
                    )
                    self.dev_manager.save_profile(prof)
                    self._refresh_devices()
                    self.active_target_device = prof
                    self._send_tracks_to_device(tracks, title)
            except Exception:
                pass

        dlg.select_folder(self, None, on_folder)

    def _update_send_progress(self, fraction: float, text: str):
        self.sel_progress_box.set_visible(True)
        self.sel_progress_bar.set_fraction(fraction)
        self.sel_progress_label.set_text(text)

    def _on_send_completed(self, device: DeviceProfile, title: str, message: str):
        self.sel_progress_box.set_visible(False)
        self.toast_overlay.add_toast(Adw.Toast.new(f"✅ {title}: {message}"))
        self._update_selection_bar()

    def _refresh_devices(self):
        while child := self.box_devices.get_first_child():
            self.box_devices.remove(child)

        connected = self.detector.list_connected_devices()
        profiles = self.dev_manager.get_all_profiles()

        self.available_devices = []

        for d in connected:
            prof = self.dev_manager.get_or_create_profile(d.device_id, d.name, d.root_path, d.device_type)
            self.available_devices.append(prof)
            btn = Gtk.Button(label=f"💾 {prof.name}")
            btn.set_halign(Gtk.Align.FILL)
            btn.connect("clicked", lambda b, p=prof: self._select_device(p))
            self.box_devices.append(btn)

        for p in profiles:
            if p.device_type == "virtual" and not any(c.device_id == p.device_id for c in connected):
                self.available_devices.append(p)
                btn = Gtk.Button(label=f"📁 {p.name}")
                btn.set_halign(Gtk.Align.FILL)
                btn.connect("clicked", lambda b, prof=p: self._select_device(prof))
                self.box_devices.append(btn)

        self._update_target_device_dropdown()

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

        if profile.sync_all_library:
            scope_str = "Toda a Biblioteca Musical"
        else:
            scope_str = f"Apenas Faixas Salvas no Perfil ({len(profile.selected_track_ids)} faixas)"
        self.row_dev_scope.set_subtitle(scope_str)

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
        all_tracks = self.db.get_all_tracks()
        if not all_tracks:
            return

        if not prof.sync_all_library and prof.selected_track_ids:
            sel_set = set(prof.selected_track_ids)
            tracks = [t for t in all_tracks if t.id in sel_set]
            if not tracks:
                self.toast_overlay.add_toast(
                    Adw.Toast.new("Nenhuma faixa da seleção salva foi encontrada na biblioteca.")
                )
                return
        else:
            tracks = all_tracks

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
