"""
Main Application Window for Batuque.
Adaptive Libadwaita layout with library browsing, NAS management, and device synchronization.
Features rich library sorting and grouping by Albums, Artists, Years, Genres, and Tracks.
"""

import os
import json
import threading
from typing import Optional, Dict, Any, List, Set, Tuple
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Pango, Gdk

from ..core.database import LibraryDatabase
from ..core.scanner import LibraryScanner
from ..core.sync_engine import SyncEngine
from ..core.nas import NASManager
from ..core.artwork import AlbumArtworkCache
from ..core.models import Track, DeviceProfile, NASSource
from ..devices.detector import DeviceDetector, DetectedDevice
from ..devices.manager import DeviceManager
from ..devices.inspector import DeviceInspector, DeviceAlbum
from .device_dialog import DeviceSettingsWindow
from .nas_dialog import NASConfigDialog


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.db = LibraryDatabase()
        self.dev_manager = DeviceManager()
        self.detector = DeviceDetector()
        self.sync_engine = SyncEngine()

        self.set_title("Batuque")
        self.set_default_size(1050, 700)
        self.set_size_request(360, 480)

        self._search_timeout_id = None
        self._current_render_token = None

        self.selected_device_profile: Optional[DeviceProfile] = None
        self.current_view_mode = "albums"  # "albums", "artists", "years", "genres", "tracks"
        self.album_view_style = "grid"  # "grid" or "list"
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
        self._grid_album_items: List[Dict[str, Any]] = []
        self._last_clicked_album_idx: Optional[int] = None
        self._last_shift_anchor: Optional[int] = None
        self._last_shift_state: bool = True
        self._last_shift_range: Optional[Tuple[int, int]] = None

        self._load_ui_settings()
        self._build_ui()
        self._refresh_library()
        self._refresh_devices()
        self._refresh_nas_sources()

        # Listen for hotplugged USB drives / DAPs
        self.detector.add_change_listener(lambda: GLib.idle_add(self._refresh_devices))

    def _build_ui(self):
        # Custom CSS for modern artwork styling and clean borderless tiles
        try:
            display = Gdk.Display.get_default()
            if display:
                css_provider = Gtk.CssProvider()
                css_provider.load_from_data(b"""
                /* Tight FlowBoxChild layout: zero margins & zero padding */
                flowboxchild {
                    padding: 0px;
                    margin: 0px;
                    border-radius: 8px;
                    background: transparent;
                }
                flowboxchild:focus {
                    outline: none;
                }

                /* Compact, borderless album grid tile with active selection state */
                .album-grid-tile {
                    border-radius: 8px;
                    padding: 3px;
                    transition: all 120ms ease;
                    background: transparent;
                    border: 1.5px solid transparent;
                }
                .album-grid-tile:hover {
                    background-color: alpha(currentColor, 0.08);
                }
                .album-grid-tile.selected {
                    background-color: alpha(var(--accent-color, #3584e4), 0.16);
                    border: 1.5px solid var(--accent-color, #3584e4);
                    box-shadow: 0 2px 8px alpha(var(--accent-color, #3584e4), 0.25);
                }

                /* Clean cover art container with subtle depth */
                .album-cover-container {
                    border-radius: 6px;
                    box-shadow: 0 3px 10px rgba(0, 0, 0, 0.22);
                    background-color: alpha(currentColor, 0.06);
                }
                .album-cover-container picture, .album-cover-container image {
                    border-radius: 6px;
                }

                .album-cover-thumb {
                    border-radius: 6px;
                    background-color: alpha(currentColor, 0.06);
                }
                .album-cover-thumb picture, .album-cover-thumb image {
                    border-radius: 6px;
                }

                /* Floating action buttons on top of cover */
                .cover-overlay-check {
                    margin: 4px;
                    filter: drop-shadow(0 2px 4px rgba(0,0,0,0.6));
                }
                .cover-overlay-btn {
                    margin: 4px;
                    border-radius: 9999px;
                    background: rgba(0, 0, 0, 0.72);
                    color: #ffffff;
                    min-width: 28px;
                    min-height: 28px;
                    padding: 3px;
                    box-shadow: 0 2px 6px rgba(0,0,0,0.3);
                    transition: transform 120ms ease, background 120ms ease;
                }
                .cover-overlay-btn:hover {
                    background: @accent_bg_color;
                    color: @accent_fg_color;
                    transform: scale(1.08);
                }
                """)
                Gtk.StyleContext.add_provider_for_display(
                    display,
                    css_provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
        except Exception:
            pass

        # Toast overlay for floating feedback notifications
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        # Main vertical box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(main_box)

        # Header bar
        header = Adw.HeaderBar()
        main_box.append(header)

        # Responsive Sidebar Toggle button
        self.btn_toggle_sidebar = Gtk.Button(icon_name="view-sidebar-symbolic")
        self.btn_toggle_sidebar.set_tooltip_text("Alternar menu lateral")
        header.pack_start(self.btn_toggle_sidebar)

        # Quick selection buttons in header
        btn_sel_all = Gtk.Button(icon_name="edit-select-all-symbolic")
        btn_sel_all.set_tooltip_text("Selecionar todas as músicas visíveis")
        btn_sel_all.connect("clicked", lambda b: self._select_visible_tracks())
        header.pack_start(btn_sel_all)

        btn_clear_sel = Gtk.Button(icon_name="edit-clear-all-symbolic")
        btn_clear_sel.set_tooltip_text("Limpar seleção de músicas")
        btn_clear_sel.connect("clicked", lambda b: self._clear_selection())
        header.pack_start(btn_clear_sel)

        # Search Entry in header (flexible & responsive)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Buscar por título, artista, álbum, ano...")
        self.search_entry.set_hexpand(False)
        self.search_entry.set_size_request(240, -1)
        self.search_entry.connect("search-changed", self._on_search_changed)
        header.set_title_widget(self.search_entry)

        # Header buttons
        btn_add_nas = Gtk.Button(icon_name="network-server-symbolic")
        btn_add_nas.set_tooltip_text("Conectar servidor NAS")
        btn_add_nas.connect("clicked", self._on_add_nas_clicked)
        header.pack_end(btn_add_nas)

        btn_refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        btn_refresh.set_tooltip_text("Atualizar biblioteca (verificação incremental rápida)")
        btn_refresh.connect("clicked", self._on_refresh_all_clicked)
        header.pack_end(btn_refresh)

        btn_scan = Gtk.Button(icon_name="folder-open-symbolic")
        btn_scan.set_tooltip_text("Escanear pasta local de músicas")
        btn_scan.connect("clicked", self._on_scan_folder_clicked)
        header.pack_end(btn_scan)

        # Responsive OverlaySplitView (Sidebar + Main Content)
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_collapsed(False)
        self.split_view.set_min_sidebar_width(210)
        self.split_view.set_max_sidebar_width(280)
        self.split_view.set_sidebar_width_fraction(0.23)
        self.split_view.set_vexpand(True)
        self.btn_toggle_sidebar.connect(
            "clicked",
            lambda b: self.split_view.set_show_sidebar(not self.split_view.get_show_sidebar())
        )
        main_box.append(self.split_view)

        # Sidebar
        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.split_view.set_sidebar(sidebar_scroll)

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
        self.btn_nav_albums.add_css_class("suggested-action")
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
        self.lbl_nas = Gtk.Label(label="SERVIDORES NAS", xalign=0)
        self.lbl_nas.set_margin_top(16)
        self.lbl_nas.add_css_class("heading")
        sidebar_box.append(self.lbl_nas)

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
        self.split_view.set_content(self.stack)

        # --- 1. Library Page ---
        lib_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack.add_named(lib_box, "library")

        # Top Toolbar (Responsive View Title + Sort Menu + Grid/List)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        toolbar.set_margin_top(10)
        toolbar.set_margin_bottom(10)
        toolbar.set_margin_start(16)
        toolbar.set_margin_end(16)
        lib_box.append(toolbar)

        # View Title Label (Replaces wide duplicated buttons)
        self.lbl_view_title = Gtk.Label(label="Álbuns", xalign=0)
        self.lbl_view_title.add_css_class("title-2")
        self.lbl_view_title.add_css_class("heading")
        toolbar.append(self.lbl_view_title)

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

        # Album view style toggle (Grid vs List)
        self.box_album_style = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.box_album_style.add_css_class("linked")

        self.btn_album_grid = Gtk.ToggleButton(icon_name="view-grid-symbolic")
        self.btn_album_grid.set_tooltip_text("Visualização em Grade de Capas")
        self.btn_album_grid.set_active(True)
        self.btn_album_grid.connect("toggled", lambda b: b.get_active() and self._switch_album_style("grid"))
        self.box_album_style.append(self.btn_album_grid)

        self.btn_album_list = Gtk.ToggleButton(icon_name="view-list-symbolic")
        self.btn_album_list.set_tooltip_text("Visualização em Lista / Cards com Faixas")
        self.btn_album_list.connect("toggled", lambda b: b.get_active() and self._switch_album_style("list"))
        self.box_album_style.append(self.btn_album_list)

        toolbar.append(self.box_album_style)

        # Grid density / Zoom settings MenuButton
        self.btn_grid_settings = Gtk.MenuButton()
        self.btn_grid_settings.set_icon_name("zoom-in-symbolic")
        self.btn_grid_settings.set_tooltip_text("Ajustar densidade da grade (Zoom e Colunas)")
        toolbar.append(self.btn_grid_settings)

        # Inner Library Stack for different viewing styles
        self.lib_stack = Gtk.Stack()
        self.lib_stack.set_vexpand(True)
        self.lib_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        lib_box.append(self.lib_stack)

        # View A: Albums (Stack supporting Grid Mode and List Mode)
        self.albums_stack = Gtk.Stack()
        self.albums_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        # A1: Responsive Cover Grid (FlowBox)
        scroll_albums_grid = Gtk.ScrolledWindow()
        scroll_albums_grid.set_vexpand(True)
        self.albums_flowbox = Gtk.FlowBox()
        self.albums_flowbox.set_valign(Gtk.Align.START)
        self.albums_flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.albums_flowbox.set_homogeneous(True)
        self.albums_flowbox.set_row_spacing(6)
        self.albums_flowbox.set_column_spacing(6)
        self.albums_flowbox.set_margin_start(8)
        self.albums_flowbox.set_margin_end(8)
        self.albums_flowbox.set_margin_top(8)
        self.albums_flowbox.set_margin_bottom(8)
        self._apply_grid_dimensions()

        scroll_albums_grid.set_child(self.albums_flowbox)
        self.albums_stack.add_named(scroll_albums_grid, "grid")

        # Ctrl + Mouse Wheel Zoom directly over the cover grid
        scroll_ctrl = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        def on_grid_scroll(controller, dx, dy):
            event = controller.get_current_event()
            if event:
                mods = event.get_modifier_state()
                if bool(mods & Gdk.ModifierType.CONTROL_MASK):
                    step = 10 if dy < 0 else -10
                    new_size = max(75, min(220, self.grid_cover_size + step))
                    if new_size != self.grid_cover_size:
                        self.grid_cover_size = new_size
                        if hasattr(self, "grid_scale") and self.grid_scale:
                            self.grid_scale.set_value(new_size)
                        else:
                            self._save_ui_settings()
                            self._apply_grid_dimensions()
                            self._render_albums_view()
                    return True
            return False
        scroll_ctrl.connect("scroll", on_grid_scroll)
        scroll_albums_grid.add_controller(scroll_ctrl)

        self._setup_grid_density_popover()

        # A2: List / Grouped Cards View
        scroll_albums_list = Gtk.ScrolledWindow()
        scroll_albums_list.set_vexpand(True)
        self.albums_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.albums_container.set_margin_start(16)
        self.albums_container.set_margin_end(16)
        self.albums_container.set_margin_top(12)
        self.albums_container.set_margin_bottom(16)
        scroll_albums_list.set_child(self.albums_container)
        self.albums_stack.add_named(scroll_albums_list, "list")

        self.lib_stack.add_named(self.albums_stack, "albums")

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

        btn_sel_all = Gtk.Button(label="Selecionar Todas")
        btn_sel_all.add_css_class("flat")
        btn_sel_all.connect("clicked", lambda b: self._select_visible_tracks())
        sel_start_box.append(btn_sel_all)

        btn_clear = Gtk.Button(label="Limpar")
        btn_clear.add_css_class("flat")
        btn_clear.connect("clicked", lambda b: self._clear_selection())
        sel_start_box.append(btn_clear)
        self.selection_bar.pack_start(sel_start_box)

        # Center box: Progress bar during live transfer
        self.sel_progress_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.sel_progress_bar = Gtk.ProgressBar()
        self.sel_progress_bar.set_size_request(110, -1)
        self.sel_progress_bar.set_show_text(True)
        self.sel_progress_label = Gtk.Label(label="")
        self.sel_progress_label.add_css_class("dim-label")
        self.sel_progress_box.append(self.sel_progress_bar)
        self.sel_progress_box.append(self.sel_progress_label)
        self.sel_progress_box.set_visible(False)
        self.selection_bar.set_center_widget(self.sel_progress_box)

        # End box: Target device, Save to profile, and Send Selected
        sel_end_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_target = Gtk.Label(label="Destino:")
        lbl_target.add_css_class("dim-label")
        sel_end_box.append(lbl_target)

        self.combo_target_device = Gtk.DropDown()
        self.combo_target_device.connect("notify::selected", self._on_target_device_changed)
        sel_end_box.append(self.combo_target_device)

        btn_save_to_device = Gtk.Button(label="💾 Salvar")
        btn_save_to_device.set_tooltip_text("Salva as faixas selecionadas no perfil do dispositivo para sincronizações futuras")
        btn_save_to_device.connect("clicked", self._on_save_selection_to_device)
        sel_end_box.append(btn_save_to_device)

        self.btn_send_selected = Gtk.Button(label="🚀 Enviar")
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
        self.device_scroll = Gtk.ScrolledWindow()
        self.device_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.stack.add_named(self.device_scroll, "device")

        self.device_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.device_box.set_margin_start(24)
        self.device_box.set_margin_end(24)
        self.device_box.set_margin_top(20)
        self.device_box.set_margin_bottom(20)
        self.device_scroll.set_child(self.device_box)

        # Header with Title and Default/Ignored Badge
        header_dev_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.dev_title = Gtk.Label(label="Dispositivo", xalign=0)
        self.dev_title.add_css_class("title-1")
        self.dev_title.set_hexpand(True)
        header_dev_box.append(self.dev_title)

        self.lbl_dev_badge = Gtk.Label(label="")
        self.lbl_dev_badge.add_css_class("badge")
        header_dev_box.append(self.lbl_dev_badge)
        self.device_box.append(header_dev_box)

        # Action Buttons Bar
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.device_box.append(btn_box)

        self.btn_set_default = Gtk.Button(label="⭐ Definir como Padrão")
        self.btn_set_default.connect("clicked", self._on_set_default_selected_device)
        btn_box.append(self.btn_set_default)

        self.btn_ignore_device = Gtk.Button(label="🚫 Ignorar Volume")
        self.btn_ignore_device.connect("clicked", self._on_ignore_selected_device)
        btn_box.append(self.btn_ignore_device)

        btn_configure = Gtk.Button(label="⚙️ Configurar")
        btn_configure.connect("clicked", self._on_configure_selected_device)
        btn_box.append(btn_configure)

        self.btn_optimize_art = Gtk.Button(label="🖼️ Otimizar Capas")
        self.btn_optimize_art.set_tooltip_text("Reduz capas grandes de álbuns presentes no dispositivo para economizar espaço e evitar travamentos no player")
        self.btn_optimize_art.connect("clicked", self._on_optimize_artwork_clicked)
        btn_box.append(self.btn_optimize_art)

        self.btn_repair_art = Gtk.Button(label="🛠️ Reparar Capas")
        self.btn_repair_art.set_tooltip_text("Corrige capas cortadas ou com barras cinzas nos álbuns do dispositivo, restaurando-as a partir da biblioteca")
        self.btn_repair_art.connect("clicked", self._on_repair_artwork_clicked)
        btn_box.append(self.btn_repair_art)

        self.btn_force_sync = Gtk.Button(label="🔄 Forçar Ressincronização")
        self.btn_force_sync.set_tooltip_text("Reenvia e sobrescreve todas as faixas e capas no dispositivo")
        self.btn_force_sync.connect("clicked", lambda b: self._on_start_sync(force_overwrite=True))
        btn_box.append(self.btn_force_sync)

        self.btn_sync_now = Gtk.Button(label="Sincronizar Agora")
        self.btn_sync_now.add_css_class("suggested-action")
        self.btn_sync_now.connect("clicked", lambda b: self._on_start_sync(force_overwrite=False))
        btn_box.append(self.btn_sync_now)

        # Device Info Group
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

        # Sync Progress Area
        self.sync_progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.sync_progress_box.set_visible(False)
        self.device_box.append(self.sync_progress_box)

        self.lbl_sync_status = Gtk.Label(label="Preparando sincronização...", xalign=0)
        self.sync_progress_box.append(self.lbl_sync_status)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.sync_progress_box.append(self.progress_bar)

        # Device Explorer Group: Music present on Device
        dev_explorer_group = Adw.PreferencesGroup()
        dev_explorer_group.set_title("Músicas Armazenadas no Dispositivo")
        dev_explorer_group.set_description("Álbuns e capas atualmente detectados na memória deste player.")

        explorer_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_device_tracks_stats = Gtk.Label(label="Carregando faixas...", xalign=0)
        self.lbl_device_tracks_stats.set_hexpand(True)
        explorer_hdr.append(self.lbl_device_tracks_stats)

        btn_refresh_dev_tracks = Gtk.Button(icon_name="view-refresh-symbolic")
        btn_refresh_dev_tracks.set_tooltip_text("Recarregar músicas do player")
        btn_refresh_dev_tracks.connect("clicked", lambda b: self._reload_device_tracks())
        explorer_hdr.append(btn_refresh_dev_tracks)
        dev_explorer_group.add(explorer_hdr)

        self.device_albums_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.device_albums_box.set_margin_top(8)
        dev_explorer_group.add(self.device_albums_box)

        self.device_box.append(dev_explorer_group)

        # Global key controller for shortcuts (Ctrl+A to select all visible)
        key_ctrl = Gtk.EventControllerKey()
        def on_key_pressed(controller, keyval, keycode, state):
            is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
            if is_ctrl and keyval in (Gdk.KEY_a, Gdk.KEY_A):
                focus_widget = self.get_focus()
                if not isinstance(focus_widget, (Gtk.Editable, Gtk.Entry, Gtk.SearchEntry)):
                    self._select_visible_tracks()
                    return True
            return False
        key_ctrl.connect("key-pressed", on_key_pressed)
        self.add_controller(key_ctrl)

    def _switch_view(self, mode: str):
        self.current_view_mode = mode
        title_map = {
            "albums": "Álbuns",
            "artists": "Artistas",
            "years": "Linha do Tempo",
            "genres": "Gêneros",
            "tracks": "Todas as Músicas"
        }
        if hasattr(self, "lbl_view_title"):
            self.lbl_view_title.set_text(title_map.get(mode, "Biblioteca"))

        for m, btn in [
            ("albums", getattr(self, "btn_nav_albums", None)),
            ("artists", getattr(self, "btn_nav_artists", None)),
            ("years", getattr(self, "btn_nav_years", None)),
            ("genres", getattr(self, "btn_nav_genres", None)),
            ("tracks", getattr(self, "btn_nav_tracks", None)),
        ]:
            if btn:
                if m == mode:
                    btn.add_css_class("suggested-action")
                else:
                    btn.remove_css_class("suggested-action")

        self.box_album_style.set_visible(mode == "albums")
        if hasattr(self, "btn_grid_settings"):
            self.btn_grid_settings.set_visible(mode == "albums" and self.album_view_style == "grid")
        if mode == "albums":
            self.albums_stack.set_visible_child_name(self.album_view_style)

        self.lib_stack.set_visible_child_name(mode)
        self.stack.set_visible_child_name("library")
        self._refresh_library()

    def _switch_album_style(self, style: str):
        self.album_view_style = style
        self.btn_album_grid.set_active(style == "grid")
        self.btn_album_list.set_active(style == "list")
        if hasattr(self, "btn_grid_settings"):
            self.btn_grid_settings.set_visible(style == "grid")
        self.albums_stack.set_visible_child_name(style)
        self._render_albums_view()

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
        if getattr(self, "_search_timeout_id", None):
            GLib.source_remove(self._search_timeout_id)

        def do_search():
            self._search_timeout_id = None
            self.search_query = entry.get_text()
            self._refresh_library()
            return False

        self._search_timeout_id = GLib.timeout_add(180, do_search)

    def _get_ui_settings_path(self) -> str:
        d = os.path.expanduser("~/.config/batuque")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "ui_settings.json")

    def _load_ui_settings(self):
        self.grid_cover_size = 116
        self.grid_fixed_columns = None
        p = self._get_ui_settings_path()
        old_p = os.path.expanduser("~/.config/soundbee/ui_settings.json")
        target = p if os.path.exists(p) else (old_p if os.path.exists(old_p) else None)
        if target:
            try:
                with open(target, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.grid_cover_size = max(75, min(240, int(data.get("grid_cover_size", 116))))
                    fc = data.get("grid_fixed_columns")
                    self.grid_fixed_columns = int(fc) if fc is not None else None
            except Exception:
                pass

    def _save_ui_settings(self):
        p = self._get_ui_settings_path()
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump({
                    "grid_cover_size": self.grid_cover_size,
                    "grid_fixed_columns": self.grid_fixed_columns
                }, f, indent=2)
        except Exception:
            pass

    def _apply_grid_dimensions(self):
        if not hasattr(self, "albums_flowbox"):
            return
        if getattr(self, "grid_fixed_columns", None) is not None:
            self.albums_flowbox.set_min_children_per_line(self.grid_fixed_columns)
            self.albums_flowbox.set_max_children_per_line(self.grid_fixed_columns)
        else:
            self.albums_flowbox.set_min_children_per_line(2)
            self.albums_flowbox.set_max_children_per_line(36)

    def _setup_grid_density_popover(self):
        pop = Gtk.Popover()
        pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        pop_box.set_margin_start(14)
        pop_box.set_margin_end(14)
        pop_box.set_margin_top(14)
        pop_box.set_margin_bottom(14)
        pop_box.set_size_request(240, -1)

        # Title
        lbl_pop_title = Gtk.Label(label="Densidade da Grade", xalign=0)
        lbl_pop_title.add_css_class("heading")
        pop_box.append(lbl_pop_title)

        # 1. Column Selection DropDown
        col_group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        lbl_col = Gtk.Label(label="Colunas:", xalign=0)
        lbl_col.add_css_class("dim-label")
        lbl_col.add_css_class("caption")
        col_group.append(lbl_col)

        col_options = [
            "Automático (Responsivo)",
            "4 Colunas",
            "5 Colunas",
            "6 Colunas",
            "7 Colunas",
            "8 Colunas",
            "9 Colunas",
            "10 Colunas",
            "12 Colunas"
        ]
        self.combo_grid_columns = Gtk.DropDown.new_from_strings(col_options)
        col_val_map = {None: 0, 4: 1, 5: 2, 6: 3, 7: 4, 8: 5, 9: 6, 10: 7, 12: 8}
        idx_val_map = {0: None, 1: 4, 2: 5, 3: 6, 4: 7, 5: 8, 6: 9, 7: 10, 8: 12}
        self.combo_grid_columns.set_selected(col_val_map.get(self.grid_fixed_columns, 0))

        def on_columns_changed(dropdown, param):
            sel = dropdown.get_selected()
            self.grid_fixed_columns = idx_val_map.get(sel, None)
            if self.grid_fixed_columns is not None:
                # Calculate ideal cover size for this column count
                parent_w = self.albums_flowbox.get_parent().get_width()
                if parent_w > 200:
                    avail = parent_w - 16 - (self.grid_fixed_columns - 1) * 6
                    cell_w = max(60, avail // self.grid_fixed_columns)
                    ideal_size = min(220, max(75, cell_w - 12))
                    self.grid_cover_size = ideal_size
                    if hasattr(self, "grid_scale"):
                        self.grid_scale.set_value(ideal_size)
            self._save_ui_settings()
            self._apply_grid_dimensions()
            self._render_albums_view()

        self.combo_grid_columns.connect("notify::selected", on_columns_changed)
        col_group.append(self.combo_grid_columns)
        pop_box.append(col_group)

        # 2. Cover Zoom Slider Group
        zoom_group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        zoom_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_zoom = Gtk.Label(label="Tamanho das Capas:", xalign=0)
        lbl_zoom.add_css_class("dim-label")
        lbl_zoom.add_css_class("caption")
        lbl_zoom.set_hexpand(True)
        zoom_hdr.append(lbl_zoom)

        self.lbl_cover_size_val = Gtk.Label(label=f"{self.grid_cover_size} px")
        self.lbl_cover_size_val.add_css_class("caption")
        zoom_hdr.append(self.lbl_cover_size_val)
        zoom_group.append(zoom_hdr)

        scale_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_zoom_out = Gtk.Button.new_from_icon_name("zoom-out-symbolic")
        btn_zoom_out.add_css_class("flat")
        scale_box.append(btn_zoom_out)

        self.grid_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 75, 220, 5)
        self.grid_scale.set_value(self.grid_cover_size)
        self.grid_scale.set_hexpand(True)
        scale_box.append(self.grid_scale)

        btn_zoom_in = Gtk.Button.new_from_icon_name("zoom-in-symbolic")
        btn_zoom_in.add_css_class("flat")
        scale_box.append(btn_zoom_in)
        zoom_group.append(scale_box)
        pop_box.append(zoom_group)

        # Debounced scale change
        self._zoom_timeout_id = None
        def on_scale_value_changed(scale):
            val = int(scale.get_value())
            self.lbl_cover_size_val.set_text(f"{val} px")
            if getattr(self, "_zoom_timeout_id", None):
                GLib.source_remove(self._zoom_timeout_id)
            def apply_zoom():
                self.grid_cover_size = val
                self._save_ui_settings()
                self._apply_grid_dimensions()
                self._render_albums_view()
                self._zoom_timeout_id = None
                return False
            self._zoom_timeout_id = GLib.timeout_add(100, apply_zoom)

        self.grid_scale.connect("value-changed", on_scale_value_changed)
        btn_zoom_out.connect("clicked", lambda b: self.grid_scale.set_value(max(75, self.grid_scale.get_value() - 15)))
        btn_zoom_in.connect("clicked", lambda b: self.grid_scale.set_value(min(220, self.grid_scale.get_value() + 15)))

        # 3. Quick Preset Buttons
        lbl_pre = Gtk.Label(label="Predefinições:", xalign=0)
        lbl_pre.add_css_class("dim-label")
        lbl_pre.add_css_class("caption")
        pop_box.append(lbl_pre)

        presets_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        presets_box.add_css_class("linked")
        presets = [
            ("Mini", 80),
            ("Compacto", 100),
            ("Padrão", 116),
            ("Grande", 150),
            ("Extra", 190)
        ]
        for name, sz in presets:
            btn_p = Gtk.Button(label=name)
            btn_p.set_hexpand(True)
            btn_p.connect("clicked", lambda b, s=sz: self.grid_scale.set_value(s))
            presets_box.append(btn_p)
        pop_box.append(presets_box)

        # 4. Keyboard/Mouse Wheel Hint
        lbl_hint = Gtk.Label(label="💡 Segure Ctrl e gire a roda do mouse sobre a grade para zoom rápido.", xalign=0)
        lbl_hint.add_css_class("dim-label")
        lbl_hint.add_css_class("caption")
        lbl_hint.set_wrap(True)
        lbl_hint.set_max_width_chars(32)
        lbl_hint.set_margin_top(4)
        pop_box.append(lbl_hint)

        pop.set_child(pop_box)
        self.btn_grid_settings.set_popover(pop)

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
        stack_thumb = Gtk.Stack()
        stack_thumb.set_size_request(56, 56)
        stack_thumb.add_css_class("album-cover-thumb")
        stack_thumb.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        placeholder = Gtk.Image.new_from_icon_name("media-optical-symbolic")
        placeholder.set_pixel_size(40)
        placeholder.set_size_request(56, 56)
        stack_thumb.add_named(placeholder, "placeholder")

        pic_cover = Gtk.Picture()
        pic_cover.set_can_shrink(True)
        pic_cover.set_content_fit(Gtk.ContentFit.COVER)
        pic_cover.set_size_request(56, 56)
        stack_thumb.add_named(pic_cover, "cover")

        header_box.append(stack_thumb)

        def set_card_cover(item):
            if isinstance(item, Gdk.Texture):
                pic_cover.set_paintable(item)
            else:
                pic_cover.set_filename(str(item))
            stack_thumb.set_visible_child_name("cover")

        cached_tex = AlbumArtworkCache.get_cached_paintable(album["artist"], album["album"])
        if cached_tex:
            set_card_cover(cached_tex)
        else:
            stack_thumb.set_visible_child_name("placeholder")
            candidate_paths = album.get("sample_paths") or ([album["sample_path"]] if album.get("sample_path") else [])
            if candidate_paths:
                AlbumArtworkCache.load_cover_async(
                    album["artist"], album["album"], candidate_paths,
                    set_card_cover,
                    max_dim=160
                )

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
            row.set_title(GLib.markup_escape_text(t.display_title))

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

    def _handle_album_grid_click(self, tile_idx: int, is_shift: bool):
        """Handles single click and bidirectional Shift+Click range selection/deselection."""
        if tile_idx < 0 or tile_idx >= len(self._grid_album_items):
            return

        it_clicked = self._grid_album_items[tile_idx]

        if not is_shift or getattr(self, "_last_shift_anchor", None) is None:
            # Single click without Shift (or first click without anchor)
            new_state = not it_clicked["check"].get_active()
            self._updating_checks = True
            it_clicked["check"].set_active(new_state)
            it_clicked["check"].set_inconsistent(False)
            if new_state:
                it_clicked["tile"].add_css_class("selected")
                for tid in it_clicked["track_ids"]:
                    self.selected_track_ids.add(tid)
            else:
                it_clicked["tile"].remove_css_class("selected")
                for tid in it_clicked["track_ids"]:
                    self.selected_track_ids.discard(tid)
            self._updating_checks = False

            self._last_clicked_album_idx = tile_idx
            self._last_shift_anchor = tile_idx
            self._last_shift_state = new_state
            self._last_shift_range = None
            self._update_selection_bar()
            return

        # Shift + Click
        anchor = self._last_shift_anchor
        target_state = getattr(self, "_last_shift_state", True)

        start_new = min(anchor, tile_idx)
        end_new = max(anchor, tile_idx)
        prev_range = getattr(self, "_last_shift_range", None)

        self._updating_checks = True

        # If user is adjusting an active Shift range, revert any items trimmed out
        if prev_range:
            p_start, p_end = prev_range
            for i in range(p_start, p_end + 1):
                if i < start_new or i > end_new:
                    if 0 <= i < len(self._grid_album_items):
                        it = self._grid_album_items[i]
                        it["check"].set_active(not target_state)
                        it["check"].set_inconsistent(False)
                        if not target_state:
                            it["tile"].add_css_class("selected")
                            for tid in it["track_ids"]:
                                self.selected_track_ids.add(tid)
                        else:
                            it["tile"].remove_css_class("selected")
                            for tid in it["track_ids"]:
                                self.selected_track_ids.discard(tid)

        # Apply target state to the new range
        for i in range(start_new, end_new + 1):
            if 0 <= i < len(self._grid_album_items):
                it = self._grid_album_items[i]
                it["check"].set_active(target_state)
                it["check"].set_inconsistent(False)
                if target_state:
                    it["tile"].add_css_class("selected")
                    for tid in it["track_ids"]:
                        self.selected_track_ids.add(tid)
                else:
                    it["tile"].remove_css_class("selected")
                    for tid in it["track_ids"]:
                        self.selected_track_ids.discard(tid)

        self._updating_checks = False
        self._last_shift_range = (start_new, end_new)
        self._last_clicked_album_idx = tile_idx
        self._update_selection_bar()

    def _create_album_grid_tile(self, album: Dict[str, Any], idx: int = 0) -> Gtk.Widget:
        """Constructs an elegant, compact, artwork-focused Album Tile for the dense Cover Grid view."""
        cover_size = getattr(self, "grid_cover_size", 116)
        tile_w = cover_size
        cover_w = cover_size
        max_chars = max(8, int(cover_w / 8.2))

        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        tile.add_css_class("album-grid-tile")
        tile.set_size_request(tile_w, -1)
        tile.set_valign(Gtk.Align.START)
        tile.set_halign(Gtk.Align.CENTER)

        album_tracks: List[Track] = album["tracks"]
        album_track_ids = [t.id for t in album_tracks if t.id is not None]

        # 1. Cover container with Overlay for Checkbox and Send Button
        overlay = Gtk.Overlay()
        overlay.set_size_request(cover_w, cover_w)
        overlay.set_halign(Gtk.Align.CENTER)
        tile.append(overlay)

        # Base Cover Stack
        stack_cover = Gtk.Stack()
        stack_cover.set_size_request(cover_w, cover_w)
        stack_cover.add_css_class("album-cover-container")
        stack_cover.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        placeholder_size = max(24, int(cover_w * 0.38))
        placeholder = Gtk.Image.new_from_icon_name("media-optical-symbolic")
        placeholder.set_pixel_size(placeholder_size)
        placeholder.set_size_request(cover_w, cover_w)
        stack_cover.add_named(placeholder, "placeholder")

        img_cover = Gtk.Image()
        img_cover.set_pixel_size(cover_w)
        img_cover.set_size_request(cover_w, cover_w)
        stack_cover.add_named(img_cover, "cover")

        overlay.set_child(stack_cover)

        # Floating Top-Right: Selection Checkbox
        check_album = Gtk.CheckButton()
        check_album.set_valign(Gtk.Align.START)
        check_album.set_halign(Gtk.Align.END)
        check_album.add_css_class("cover-overlay-check")
        check_album.set_tooltip_text("Selecionar álbum")
        selected_in_album = [tid for tid in album_track_ids if tid in self.selected_track_ids]
        is_fully_selected = selected_in_album and len(selected_in_album) == len(album_track_ids)
        if is_fully_selected:
            check_album.set_active(True)
            tile.add_css_class("selected")
        elif selected_in_album:
            check_album.set_inconsistent(True)
            tile.add_css_class("selected")

        def on_tile_check_toggled(btn, t_ids=album_track_ids, tile_idx=idx):
            if self._updating_checks:
                return
            self._updating_checks = True
            is_active = btn.get_active()
            btn.set_inconsistent(False)
            if is_active:
                tile.add_css_class("selected")
            else:
                tile.remove_css_class("selected")
            for tid in t_ids:
                if is_active:
                    self.selected_track_ids.add(tid)
                else:
                    self.selected_track_ids.discard(tid)
            self._last_clicked_album_idx = tile_idx
            self._last_shift_anchor = tile_idx
            self._last_shift_state = is_active
            self._last_shift_range = None
            self._updating_checks = False
            self._update_selection_bar()

        check_album.connect("toggled", on_tile_check_toggled)

        # Allow Shift+Click directly on the checkbox overlay
        check_gesture = Gtk.GestureClick()
        def on_check_pressed(g, n_press, x, y, tile_idx=idx):
            if n_press == 1:
                current_event = g.get_current_event()
                is_shift = False
                if current_event:
                    mods = current_event.get_modifier_state()
                    is_shift = bool(mods & Gdk.ModifierType.SHIFT_MASK)
                if is_shift:
                    self._handle_album_grid_click(tile_idx, is_shift=True)
                    g.set_state(Gtk.EventSequenceState.CLAIMED)
        check_gesture.connect("pressed", on_check_pressed)
        check_album.add_controller(check_gesture)

        overlay.add_overlay(check_album)

        # Floating Bottom-Right: Quick Send Button
        btn_send = Gtk.Button.new_from_icon_name("document-send-symbolic")
        btn_send.set_valign(Gtk.Align.END)
        btn_send.set_halign(Gtk.Align.END)
        btn_send.add_css_class("cover-overlay-btn")
        btn_send.set_tooltip_text(f"Enviar álbum '{album['album']}' para o dispositivo")
        btn_send.connect(
            "clicked",
            lambda b, al=album: self._send_tracks_to_device(al["tracks"], title=f"Álbum '{al['album']}'")
        )
        overlay.add_overlay(btn_send)

        # Store reference for Shift+Click range selection
        tile_item = {
            "album": album,
            "track_ids": album_track_ids,
            "check": check_album,
            "tile": tile
        }
        self._grid_album_items.append(tile_item)

        # Load cover (Instant texture or async)
        def set_grid_cover(item):
            if isinstance(item, Gdk.Texture):
                img_cover.set_from_paintable(item)
            else:
                img_cover.set_from_file(str(item))
            stack_cover.set_visible_child_name("cover")

        cached_tex = AlbumArtworkCache.get_cached_paintable(album["artist"], album["album"])
        if cached_tex:
            set_grid_cover(cached_tex)
        else:
            stack_cover.set_visible_child_name("placeholder")
            candidate_paths = album.get("sample_paths") or ([album["sample_path"]] if album.get("sample_path") else [])
            if candidate_paths:
                AlbumArtworkCache.load_cover_async(
                    album["artist"], album["album"], candidate_paths,
                    set_grid_cover,
                    max_dim=300
                )

        # Click controller on cover & tile:
        # Single click toggles selection (Shift+Click: range selection/deselection)
        # Double click opens list view
        gesture = Gtk.GestureClick()
        def on_cover_clicked(g, n_press, x, y, al=album, tile_idx=idx):
            if n_press == 1:
                current_event = g.get_current_event()
                is_shift = False
                if current_event:
                    mods = current_event.get_modifier_state()
                    is_shift = bool(mods & Gdk.ModifierType.SHIFT_MASK)
                self._handle_album_grid_click(tile_idx, is_shift=is_shift)
            elif n_press >= 2:
                self.search_entry.set_text(al["album"])
                self._switch_album_style("list")
        gesture.connect("released", on_cover_clicked)
        stack_cover.add_controller(gesture)

        # 2. Metadata underneath cover (2 compact lines constrained to tile_w width)
        meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        meta_box.set_size_request(tile_w, -1)
        meta_box.set_halign(Gtk.Align.CENTER)
        tile.append(meta_box)

        lbl_title = Gtk.Label(label=album["album"], xalign=0)
        lbl_title.add_css_class("caption-heading")
        lbl_title.set_ellipsize(Pango.EllipsizeMode.END)
        lbl_title.set_max_width_chars(max_chars)
        lbl_title.set_hexpand(True)
        lbl_title.set_tooltip_text(album["album"])
        meta_box.append(lbl_title)

        year_str = f" ({album['year']})" if album.get("year") else ""
        meta_sub = f"{album['artist']}{year_str} • {album['track_count']} fx"
        lbl_artist = Gtk.Label(label=meta_sub, xalign=0)
        lbl_artist.add_css_class("dim-label")
        lbl_artist.add_css_class("caption")
        lbl_artist.set_ellipsize(Pango.EllipsizeMode.END)
        lbl_artist.set_max_width_chars(max_chars + 1)
        lbl_artist.set_hexpand(True)
        meta_box.append(lbl_artist)

        meta_gesture = Gtk.GestureClick()
        meta_gesture.connect("released", on_cover_clicked)
        meta_box.add_controller(meta_gesture)

        tile.set_tooltip_text(f"'{album['album']}' por {album['artist']} ({album['track_count']} faixas)\nClique para selecionar • Shift+Clique para intervalo • Duplo clique para abrir")

        return tile

    def _render_albums_view(self):
        albums = self.db.get_albums_grouped(
            order_by=self.sort_criterion,
            order_desc=self.sort_descending,
            search_query=self.search_query
        )

        # Invalidate previous idle rendering tokens
        render_token = object()
        self._current_render_token = render_token

        if self.album_view_style == "grid":
            self._apply_grid_dimensions()
            while child := self.albums_flowbox.get_first_child():
                self.albums_flowbox.remove(child)

            self._grid_album_items = []
            self._last_clicked_album_idx = None
            self._last_shift_anchor = None
            self._last_shift_state = True
            self._last_shift_range = None

            batch_size = 24
            for i, alb in enumerate(albums[:batch_size]):
                self.albums_flowbox.append(self._create_album_grid_tile(alb, i))

            remaining = albums[batch_size:]
            if remaining:
                def render_grid_batch(chunk, start_idx):
                    if getattr(self, "_current_render_token", None) != render_token:
                        return False
                    batch = chunk[:batch_size]
                    rest = chunk[batch_size:]
                    for j, alb in enumerate(batch):
                        self.albums_flowbox.append(self._create_album_grid_tile(alb, start_idx + j))
                    if rest:
                        GLib.idle_add(lambda: render_grid_batch(rest, start_idx + len(batch)))
                    return False

                GLib.idle_add(lambda: render_grid_batch(remaining, batch_size))
        else:
            while child := self.albums_container.get_first_child():
                self.albums_container.remove(child)

            batch_size = 12
            for alb in albums[:batch_size]:
                self.albums_container.append(self._create_album_card(alb))

            remaining = albums[batch_size:]
            if remaining:
                def render_list_batch(chunk):
                    if getattr(self, "_current_render_token", None) != render_token:
                        return False
                    batch = chunk[:batch_size]
                    rest = chunk[batch_size:]
                    for alb in batch:
                        self.albums_container.append(self._create_album_card(alb))
                    if rest:
                        GLib.idle_add(lambda: render_list_batch(rest))
                    return False

                GLib.idle_add(lambda: render_list_batch(remaining))

        AlbumArtworkCache.preload_album_covers(albums)

    def _render_artists_view(self):
        while child := self.artists_listbox.get_first_child():
            self.artists_listbox.remove(child)

        artists = self.db.get_artists_summary(search_query=self.search_query)
        for art in artists:
            row = Adw.ActionRow()
            row.set_title(GLib.markup_escape_text(art["artist_name"]))
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
            row.set_title(GLib.markup_escape_text(gen["genre_name"]))
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
            row.set_title(GLib.markup_escape_text(title))
            row.set_subtitle(GLib.markup_escape_text(artist_album))

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
            names = ["Nenhum dispositivo conectado"]
            self.combo_target_device.set_model(Gtk.StringList.new(names))
            self.active_target_device = None
            return

        names = [f"{'⭐ ' if d.is_default else ''}💾 {d.name}" for d in self.available_devices]
        self.combo_target_device.set_model(Gtk.StringList.new(names))

        # Check default profile
        default_prof = self.dev_manager.get_default_profile()
        target = None
        if default_prof and any(d.device_id == default_prof.device_id for d in self.available_devices):
            target = next(d for d in self.available_devices if d.device_id == default_prof.device_id)
        elif self.active_target_device in self.available_devices:
            target = self.active_target_device
        elif len(self.available_devices) == 1:
            target = self.available_devices[0]

        if target:
            self.active_target_device = target
            idx = self.available_devices.index(target)
            self.combo_target_device.set_selected(idx)
        else:
            self.active_target_device = None

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

    def _send_tracks_to_device(self, tracks: List[Track], title: str = "Faixas", force_overwrite: bool = False):
        target = self.active_target_device
        if not target:
            default_prof = self.dev_manager.get_default_profile()
            if default_prof and any(d.device_id == default_prof.device_id for d in self.available_devices):
                target = next(d for d in self.available_devices if d.device_id == default_prof.device_id)
            elif len(self.available_devices) == 1:
                target = self.available_devices[0]

        if not target:
            if not self.available_devices:
                self._prompt_destination_and_send(tracks, title)
                return
            else:
                self._prompt_choose_target_and_send(tracks, title, force_overwrite)
                return

        if not os.path.isdir(target.root_path):
            self.toast_overlay.add_toast(
                Adw.Toast.new(f"Aviso: Destino '{target.name}' ({target.root_path}) não está acessível.")
            )
            return

        if not os.access(target.root_path, os.W_OK):
            self.toast_overlay.add_toast(Adw.Toast.new(f"'{target.name}' está em modo somente leitura. Reparando sistema de arquivos..."))
            ok, msg = DeviceManager.repair_filesystem(target.root_path)
            if not ok or not os.access(target.root_path, os.W_OK):
                self.toast_overlay.add_toast(Adw.Toast.new(f"Erro: '{target.name}' está em modo somente leitura (Read-Only)."))
                return
            self.toast_overlay.add_toast(Adw.Toast.new(f"✅ '{target.name}' reparado e liberado para gravação!"))

        self.toast_overlay.add_toast(Adw.Toast.new(f"Iniciando envio de {title} para '{target.name}'..."))
        self.sel_progress_box.set_visible(True)
        self.sel_progress_bar.set_fraction(0.0)
        self.sel_progress_label.set_text(f"Preparando envio para {target.name}...")
        self.selection_bar.set_revealed(True)

        def bg_send():
            self._ensure_sources_mounted_for_tracks(tracks)
            plan = self.sync_engine.create_sync_plan(target, tracks, allow_delete=False, force_overwrite=force_overwrite)
            total = len(plan.items_to_copy) + len(plan.items_to_transcode)
            if total == 0:
                GLib.idle_add(lambda: self._on_send_completed(target, title, "Todos os arquivos já estavam atualizados."))
                return

            def on_prog(curr, tot, text):
                fraction = curr / max(tot, 1)
                GLib.idle_add(lambda: self._update_send_progress(fraction, text))

            success = self.sync_engine.execute_plan(plan, on_progress=on_prog)
            if success and plan.failed_items == 0:
                msg = f"Enviado {plan.successful_items} arquivo(s) com sucesso!"
            elif plan.successful_items > 0:
                msg = f"Envio parcial: {plan.successful_items} enviado(s), {plan.failed_items} com falha."
            else:
                msg = f"Falha no envio: nenhuma faixa pôde ser transferida para '{target.name}'."
            GLib.idle_add(lambda: self._on_send_completed(target, title, msg))
            GLib.idle_add(self._reload_device_tracks)

        threading.Thread(target=bg_send, daemon=True).start()

    def _prompt_choose_target_and_send(self, tracks: List[Track], title: str, force_overwrite: bool = False):
        dialog = Adw.MessageDialog.new(
            self,
            "Escolha o Dispositivo de Destino",
            f"Mais de um volume está conectado. Escolha para onde deseja transferir {title}:"
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        selected_dev = [self.available_devices[0]]
        group_radio = None

        for dev in self.available_devices:
            r = Gtk.CheckButton(label=f"💾 {dev.name} ({dev.root_path})")
            if group_radio is None:
                group_radio = r
                r.set_active(True)
            else:
                r.set_group(group_radio)

            def on_toggled(btn, d=dev):
                if btn.get_active():
                    selected_dev[0] = d
            r.connect("toggled", on_toggled)
            box.append(r)

        chk_default = Gtk.CheckButton(label="Definir como dispositivo padrão ⭐")
        chk_default.set_margin_top(8)
        box.append(chk_default)

        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("send", "Enviar Músicas")
        dialog.set_response_appearance("send", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, resp):
            if resp == "send" and selected_dev[0]:
                chosen = selected_dev[0]
                if chk_default.get_active():
                    self.dev_manager.set_default_device(chosen.device_id)
                    self._refresh_devices()
                self.active_target_device = chosen
                self._send_tracks_to_device(tracks, title, force_overwrite=force_overwrite)

        dialog.connect("response", on_response)
        dialog.present()

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

        connected_profiles = []
        for d in connected:
            prof = self.dev_manager.get_or_create_profile(d.device_id, d.name, d.root_path, d.device_type)
            connected_profiles.append(prof)

        for p in profiles:
            if p.device_type == "virtual" and not any(c.device_id == p.device_id for c in connected):
                connected_profiles.append(p)

        active = [p for p in connected_profiles if not p.ignored]
        ignored = [p for p in connected_profiles if p.ignored]

        self.available_devices = active

        for prof in active:
            star = "⭐ " if prof.is_default else ""
            btn = Gtk.Button(label=f"{star}💾 {prof.name}")
            btn.set_halign(Gtk.Align.FILL)
            btn.connect("clicked", lambda b, p=prof: self._select_device(p))
            self.box_devices.append(btn)

        if ignored:
            expander = Gtk.Expander(label=f"🚫 Ocultos ({len(ignored)})")
            expander.set_margin_top(8)
            exp_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            expander.set_child(exp_box)
            for prof in ignored:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                lbl = Gtk.Label(label=prof.name, xalign=0, hexpand=True)
                lbl.add_css_class("dim-label")
                row.append(lbl)
                btn_restore = Gtk.Button(label="Restaurar")
                btn_restore.add_css_class("flat")
                btn_restore.connect("clicked", lambda b, p=prof: self._on_restore_device(p))
                row.append(btn_restore)
                exp_box.append(row)
            self.box_devices.append(expander)

        self._update_target_device_dropdown()

    def _on_restore_device(self, prof: DeviceProfile):
        self.dev_manager.set_ignored_device(prof.device_id, False)
        self.toast_overlay.add_toast(Adw.Toast.new(f"Volume '{prof.name}' restaurado."))
        self._refresh_devices()

    def _refresh_nas_sources(self):
        while child := self.box_nas.get_first_child():
            self.box_nas.remove(child)

        sources = self.db.get_nas_sources()
        if not sources:
            self.lbl_nas.set_visible(False)
            self.box_nas.set_visible(False)
            return

        self.lbl_nas.set_visible(True)
        self.box_nas.set_visible(True)

        # Auto-connect NAS sources in background if configured
        def bg_auto_connect():
            for src in sources:
                if src.auto_connect:
                    NASManager.mount_share(src)
        threading.Thread(target=bg_auto_connect, daemon=True, name="batuque_auto_nas").start()

        for s in sources:
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            btn = Gtk.Button(label=f"🌐 {s.name}")
            btn.set_hexpand(True)
            btn.set_halign(Gtk.Align.FILL)
            btn.set_tooltip_text(s.uri)
            btn.connect("clicked", lambda b, src=s: self._on_nas_clicked(src))
            row_box.append(btn)

            btn_edit = Gtk.Button(icon_name="document-edit-symbolic")
            btn_edit.add_css_class("flat")
            btn_edit.set_tooltip_text(f"Editar servidor '{s.name}'")
            btn_edit.connect("clicked", lambda b, src=s: self._on_edit_nas_clicked(src))
            row_box.append(btn_edit)

            btn_del = Gtk.Button(icon_name="user-trash-symbolic")
            btn_del.add_css_class("flat")
            btn_del.add_css_class("destructive-action")
            btn_del.set_tooltip_text(f"Remover servidor '{s.name}'")
            btn_del.connect("clicked", lambda b, src=s: self._on_delete_nas_clicked(src))
            row_box.append(btn_del)

            self.box_nas.append(row_box)

    def _ensure_sources_mounted_for_tracks(self, tracks: List[Track]):
        """Proactively checks and mounts NAS sources if any tracks reside on network shares."""
        has_network = any(t.file_path.startswith(("smb://", "nfs://", "sftp://", "davs://", "ftp://")) for t in tracks)
        if has_network:
            for s in self.db.get_nas_sources():
                NASManager.mount_share(s)

    def _on_edit_nas_clicked(self, src: NASSource):
        def on_saved(updated_src: NASSource):
            self.db.upsert_nas_source(updated_src)
            self._refresh_nas_sources()
            self.toast_overlay.add_toast(Adw.Toast.new(f"Servidor '{updated_src.name}' atualizado."))

        dlg = NASConfigDialog(on_saved_callback=on_saved, existing_source=src, transient_for=self)
        dlg.present()

    def _on_delete_nas_clicked(self, src: NASSource):
        dialog = Adw.MessageDialog.new(
            self,
            f"Remover Servidor NAS '{src.name}'?",
            f"O compartilhamento {src.uri} será desconectado e as faixas associadas serão removidas da biblioteca."
        )
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("delete", "Remover Servidor")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_resp(d, resp):
            if resp == "delete":
                self.db.delete_nas_source(src.id)
                self._refresh_nas_sources()
                self._refresh_library()
                self.toast_overlay.add_toast(Adw.Toast.new(f"Servidor '{src.name}' removido com sucesso."))

        dialog.connect("response", on_resp)
        dialog.present()

    def _select_device(self, profile: DeviceProfile):
        self.selected_device_profile = profile
        self.dev_title.set_text(profile.name)
        if profile.is_default:
            self.lbl_dev_badge.set_text("⭐ Padrão")
            self.lbl_dev_badge.set_visible(True)
            self.btn_set_default.set_label("⭐ Dispositivo Padrão")
            self.btn_set_default.set_sensitive(False)
        else:
            self.lbl_dev_badge.set_text("")
            self.lbl_dev_badge.set_visible(False)
            self.btn_set_default.set_label("⭐ Definir como Padrão")
            self.btn_set_default.set_sensitive(True)

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

        self._reload_device_tracks()

    def _on_set_default_selected_device(self, *args):
        if not self.selected_device_profile:
            return
        prof = self.selected_device_profile
        self.dev_manager.set_default_device(prof.device_id)
        self.toast_overlay.add_toast(Adw.Toast.new(f"⭐ '{prof.name}' definido como dispositivo padrão!"))
        self._refresh_devices()
        self._select_device(prof)

    def _on_ignore_selected_device(self, *args):
        if not self.selected_device_profile:
            return
        prof = self.selected_device_profile
        self.dev_manager.set_ignored_device(prof.device_id, True)
        self.toast_overlay.add_toast(Adw.Toast.new(f"Volume '{prof.name}' ocultado."))
        self._refresh_devices()
        self._switch_view("albums")

    def _reload_device_tracks(self):
        if not self.selected_device_profile:
            return
        prof = self.selected_device_profile
        self.lbl_device_tracks_stats.set_text("🔍 Escaneando músicas no player...")
        while child := self.device_albums_box.get_first_child():
            self.device_albums_box.remove(child)

        def worker():
            albums = DeviceInspector.scan_device_albums(prof)
            GLib.idle_add(lambda: self._render_device_albums(albums, prof))

        threading.Thread(target=worker, daemon=True).start()

    def _render_device_albums(self, albums: List[DeviceAlbum], prof: DeviceProfile):
        while child := self.device_albums_box.get_first_child():
            self.device_albums_box.remove(child)

        if not albums:
            lbl_empty = Gtk.Label(label="Nenhum arquivo de áudio encontrado no dispositivo.", xalign=0)
            lbl_empty.add_css_class("dim-label")
            self.device_albums_box.append(lbl_empty)
            self.lbl_device_tracks_stats.set_text("0 álbuns encontrados.")
            return

        total_tracks = sum(a.track_count for a in albums)
        oversized = [a for a in albums if a.is_oversized]
        corrupted = [a for a in albums if a.has_corrupted_cover]
        stats_text = f"🎵 {len(albums)} álbum{'ns' if len(albums) > 1 else ''} ({total_tracks} faixas)"
        if corrupted:
            stats_text += f" • ⚠️ {len(corrupted)} capa{'s' if len(corrupted) > 1 else ''} cortada/corrompida{'s' if len(corrupted) > 1 else ''}"
        if oversized:
            stats_text += f" • ⚠️ {len(oversized)} capa{'s' if len(oversized) > 1 else ''} grande{'s' if len(oversized) > 1 else ''}"
        self.lbl_device_tracks_stats.set_text(stats_text)

        lib_albums = {a["album"].lower(): a for a in self.db.get_albums()}

        for alb in albums:
            row = Adw.ActionRow()
            row.set_title(alb.album)
            row.set_subtitle(f"{alb.artist} • {alb.track_count} faixa(s)")

            if alb.has_corrupted_cover:
                art_lbl = Gtk.Label(label="⚠️ Capa Corrompida")
                art_lbl.add_css_class("warning")
                row.add_suffix(art_lbl)

                btn_repair = Gtk.Button(label="🛠️ Reparar Capa")
                btn_repair.add_css_class("suggested-action")
                btn_repair.connect("clicked", lambda b, a=alb: self._on_repair_single_album(a, prof))
                row.add_suffix(btn_repair)
            elif alb.is_oversized:
                art_lbl = Gtk.Label(label=f"⚠️ {alb.display_cover_info}")
                art_lbl.add_css_class("warning")
                row.add_suffix(art_lbl)

                btn_opt = Gtk.Button(label="🖼️ Reduzir Capa")
                btn_opt.add_css_class("flat")
                btn_opt.connect("clicked", lambda b, a=alb: self._on_optimize_single_album(a, prof))
                row.add_suffix(btn_opt)
            elif alb.cover_width > 0 or alb.has_embedded_cover:
                art_lbl = Gtk.Label(label=f"✅ {alb.display_cover_info}")
                art_lbl.add_css_class("dim-label")
                row.add_suffix(art_lbl)
            else:
                art_lbl = Gtk.Label(label="⚪ Sem capa")
                art_lbl.add_css_class("dim-label")
                row.add_suffix(art_lbl)

            matching_lib = lib_albums.get(alb.album.lower())
            if matching_lib:
                btn_resync = Gtk.Button(icon_name="emblem-synchronizing-symbolic")
                btn_resync.set_tooltip_text("Reenviar este álbum da biblioteca (sobrescrever arquivos e capa)")
                btn_resync.add_css_class("flat")
                btn_resync.connect("clicked", lambda b, tr=matching_lib["tracks"], title=alb.album: self._send_tracks_to_device(tr, title=title, force_overwrite=True))
                row.add_suffix(btn_resync)

            self.device_albums_box.append(row)

    def _on_repair_single_album(self, alb: DeviceAlbum, prof: DeviceProfile):
        if not DeviceInspector.is_writable(prof):
            self.toast_overlay.add_toast(Adw.Toast.new("Dispositivo montado em modo somente leitura (read-only)."))
            return
        self.lbl_sync_status.set_text(f"Reparando capa de {alb.album}...")
        self.sync_progress_box.set_visible(True)

        def worker():
            ok = DeviceInspector.repair_album_artwork(alb, prof, force=True)
            GLib.idle_add(lambda: self._on_single_album_repaired(alb, ok))

        threading.Thread(target=worker, daemon=True).start()

    def _on_single_album_repaired(self, alb: DeviceAlbum, ok: bool):
        self.sync_progress_box.set_visible(False)
        if ok:
            self.toast_overlay.add_toast(Adw.Toast.new(f"✅ Capa de '{alb.album}' restaurada com sucesso!"))
        else:
            self.toast_overlay.add_toast(Adw.Toast.new(f"Aviso: Não foi possível restaurar capa de '{alb.album}'."))
        self._reload_device_tracks()

    def _on_repair_artwork_clicked(self, *args):
        if not self.selected_device_profile:
            return
        prof = self.selected_device_profile
        if not DeviceInspector.is_writable(prof):
            self.toast_overlay.add_toast(Adw.Toast.new("Dispositivo montado em modo somente leitura (read-only)."))
            return

        self.sync_progress_box.set_visible(True)
        self.progress_bar.set_fraction(0.0)
        self.lbl_sync_status.set_text("Varrendo e reparando capas no dispositivo...")
        self.btn_repair_art.set_sensitive(False)

        def worker():
            def on_prog(curr, tot, name):
                frac = curr / max(tot, 1)
                GLib.idle_add(lambda: self._update_sync_ui(frac, f"Reparando [{curr}/{tot}]: {name}"))

            res = DeviceInspector.repair_all_device_artwork(prof, force_all=False, on_progress=on_prog)
            if res.get("corrupted_found", 0) == 0:
                res = DeviceInspector.repair_all_device_artwork(prof, force_all=True, on_progress=on_prog)

            GLib.idle_add(lambda: self._on_all_artwork_repaired(res))

        threading.Thread(target=worker, daemon=True).start()

    def _on_all_artwork_repaired(self, res: Dict[str, Any]):
        self.sync_progress_box.set_visible(False)
        self.btn_repair_art.set_sensitive(True)
        rep = res.get("repaired_count", 0)
        self.toast_overlay.add_toast(Adw.Toast.new(f"✅ Reparo concluído: {rep} capa(s) restaurada(s)!"))
        self._reload_device_tracks()

    def _on_optimize_single_album(self, alb: DeviceAlbum, prof: DeviceProfile):
        if not DeviceInspector.is_writable(prof):
            self.toast_overlay.add_toast(Adw.Toast.new("Dispositivo montado em modo somente leitura (read-only)."))
            return
        self.lbl_sync_status.set_text(f"Otimizando capa de {alb.album}...")
        self.sync_progress_box.set_visible(True)

        def worker():
            max_dim = prof.artwork_max_size or 300
            ok = DeviceInspector.optimize_album_artwork(alb, max_dim=max_dim)
            GLib.idle_add(lambda: self._on_single_album_optimized(alb, ok))

        threading.Thread(target=worker, daemon=True).start()

    def _on_single_album_optimized(self, alb: DeviceAlbum, ok: bool):
        self.sync_progress_box.set_visible(False)
        if ok:
            self.toast_overlay.add_toast(Adw.Toast.new(f"✅ Capa de '{alb.album}' otimizada!"))
        else:
            self.toast_overlay.add_toast(Adw.Toast.new(f"Aviso: Não foi possível otimizar capa de '{alb.album}'."))
        self._reload_device_tracks()

    def _on_optimize_artwork_clicked(self, *args):
        if not self.selected_device_profile:
            return
        prof = self.selected_device_profile
        if not DeviceInspector.is_writable(prof):
            self.toast_overlay.add_toast(Adw.Toast.new("Dispositivo montado em modo somente leitura (read-only)."))
            return

        self.sync_progress_box.set_visible(True)
        self.progress_bar.set_fraction(0.0)
        self.lbl_sync_status.set_text("Escaneando e otimizando capas grandes no dispositivo...")
        self.btn_optimize_art.set_sensitive(False)

        def worker():
            def on_prog(curr, tot, name):
                frac = curr / max(tot, 1)
                GLib.idle_add(lambda: self._update_sync_ui(frac, f"Otimizando [{curr}/{tot}]: {name}"))

            res = DeviceInspector.optimize_all_device_artwork(prof, on_progress=on_prog)
            GLib.idle_add(lambda: self._on_all_artwork_optimized(res))

        threading.Thread(target=worker, daemon=True).start()

    def _on_all_artwork_optimized(self, res: Dict[str, Any]):
        self.sync_progress_box.set_visible(False)
        self.btn_optimize_art.set_sensitive(True)
        opt = res.get("optimized_count", 0)
        self.toast_overlay.add_toast(Adw.Toast.new(f"✅ Otimização concluída: {opt} capa(s) reduzida(s)!"))
        self._reload_device_tracks()

    def _on_configure_selected_device(self, *args):
        if not self.selected_device_profile:
            return

        def on_saved(saved_prof):
            self.dev_manager.save_profile(saved_prof)
            self._select_device(saved_prof)

        dlg = DeviceSettingsWindow(self.selected_device_profile, on_save_callback=on_saved, transient_for=self)
        dlg.present()

    def _on_start_sync(self, *args, force_overwrite: bool = False):
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

        if prof.root_path and os.path.isdir(prof.root_path) and not os.access(prof.root_path, os.W_OK):
            self.toast_overlay.add_toast(Adw.Toast.new(f"'{prof.name}' está em modo somente leitura. Reparando sistema de arquivos..."))
            ok, msg = DeviceManager.repair_filesystem(prof.root_path)
            if not ok or not os.access(prof.root_path, os.W_OK):
                self.toast_overlay.add_toast(Adw.Toast.new(f"Erro: '{prof.name}' está em modo somente leitura (Read-Only)."))
                return
            self.toast_overlay.add_toast(Adw.Toast.new(f"✅ '{prof.name}' reparado e liberado para gravação!"))

        self.sync_progress_box.set_visible(True)
        self.btn_sync_now.set_sensitive(False)
        self.progress_bar.set_fraction(0.0)
        status_prefix = "Forçando ressincronização" if force_overwrite else "Calculando diferenças"
        self.lbl_sync_status.set_text(f"{status_prefix} e arquivos...")

        def run_thread():
            self._ensure_sources_mounted_for_tracks(tracks)
            plan = self.sync_engine.create_sync_plan(
                prof, tracks, allow_delete=prof.delete_unmatched_files, force_overwrite=force_overwrite
            )
            total_ops = len(plan.items_to_copy) + len(plan.items_to_transcode) + len(plan.items_to_delete)
            if total_ops == 0:
                GLib.idle_add(lambda: self.lbl_sync_status.set_text("Todos os arquivos já estão sincronizados."))
                GLib.idle_add(lambda: self.btn_sync_now.set_sensitive(True))
                return

            def on_prog(curr, tot, text):
                fraction = curr / max(tot, 1)
                GLib.idle_add(lambda: self._update_sync_ui(fraction, text))

            success = self.sync_engine.execute_plan(plan, on_progress=on_prog)
            GLib.idle_add(lambda: self._sync_finished(plan, success))
            GLib.idle_add(self._reload_device_tracks)

        threading.Thread(target=run_thread, daemon=True).start()

    def _update_sync_ui(self, fraction, text):
        self.progress_bar.set_fraction(fraction)
        self.lbl_sync_status.set_text(text)

    def _sync_finished(self, plan: SyncPlan, success: bool):
        self.progress_bar.set_fraction(1.0)
        if success and plan.failed_items == 0:
            self.lbl_sync_status.set_text(f"Sincronização concluída! ({plan.successful_items} faixas transferidas)")
            self.toast_overlay.add_toast(Adw.Toast.new(f"✅ Sincronização concluída: {plan.successful_items} faixas enviadas."))
        elif plan.successful_items > 0:
            self.lbl_sync_status.set_text(f"Sincronização com avisos: {plan.successful_items} enviada(s), {plan.failed_items} falha(s).")
            self.toast_overlay.add_toast(Adw.Toast.new(f"⚠️ {plan.failed_items} faixas falharam ao enviar."))
        else:
            self.lbl_sync_status.set_text("Falha na sincronização: nenhuma faixa pôde ser transferida.")
            self.toast_overlay.add_toast(Adw.Toast.new("❌ Falha: verifique se o NAS e o dispositivo estão acessíveis."))
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

    def _on_refresh_all_clicked(self, *args):
        sources = self.db.get_nas_sources()
        if not sources:
            self.toast_overlay.add_toast(Adw.Toast.new("Nenhum servidor NAS configurado para atualizar."))
            return
        for s in sources:
            self._on_nas_clicked(s)

    def _scan_in_background(self, path: str, source_id: str):
        self.lbl_stats.set_text(f"Verificando atualizações em '{path}'...")
        scanner = LibraryScanner(self.db)

        def thread_target():
            def on_prog(curr, tot, name):
                GLib.idle_add(lambda: self.lbl_stats.set_text(f"Atualizando [{curr}/{tot}]: {name}"))

            count = scanner.scan_directory(path, source_id=source_id, on_progress=on_prog)

            def done():
                self._refresh_library()
                if count > 0:
                    self.toast_overlay.add_toast(Adw.Toast.new(f"✅ {count} novas faixas adicionadas à biblioteca!"))
                else:
                    self.toast_overlay.add_toast(Adw.Toast.new("✅ Biblioteca verificada: todas as faixas já estavam atualizadas."))

            GLib.idle_add(done)

        threading.Thread(target=thread_target, daemon=True).start()
