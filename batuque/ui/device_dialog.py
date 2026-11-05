"""
Device Settings Configuration Window for Batuque.
Mirrors MusicBee's DeviceSettings with GNOME Libadwaita design.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from ..core.models import DeviceProfile, Track
from ..core.organizer import FileOrganizer


class DeviceSettingsWindow(Adw.PreferencesWindow):
    def __init__(self, profile: DeviceProfile, on_save_callback=None, **kwargs):
        super().__init__(**kwargs)
        self.profile = profile
        self.on_save_callback = on_save_callback

        self.set_title(f"Configurações do Dispositivo: {profile.name}")
        self.set_default_size(700, 600)

        # Page 1: Sincronização & Geral
        page_general = Adw.PreferencesPage()
        page_general.set_title("Geral & Sincronização")
        page_general.set_icon_name("drive-removable-media-symbolic")

        # Group: Identificação
        group_id = Adw.PreferencesGroup()
        group_id.set_title("Identificação")

        self.entry_name = Adw.EntryRow()
        self.entry_name.set_title("Nome do Dispositivo")
        self.entry_name.set_text(self.profile.name)
        group_id.add(self.entry_name)

        self.entry_root = Adw.ActionRow()
        self.entry_root.set_title("Ponto de Montagem")
        self.entry_root.set_subtitle(self.profile.root_path or "Não montado")
        group_id.add(self.entry_root)

        self.entry_subfolder = Adw.EntryRow()
        self.entry_subfolder.set_title("Subpasta de Músicas no Dispositivo")
        self.entry_subfolder.set_text(self.profile.music_subfolder)
        group_id.add(self.entry_subfolder)

        self.switch_default = Adw.SwitchRow()
        self.switch_default.set_title("Definir como Dispositivo Padrão")
        self.switch_default.set_subtitle("Destino preferencial para envio rápido de músicas")
        self.switch_default.set_active(self.profile.is_default)
        group_id.add(self.switch_default)

        self.switch_ignored = Adw.SwitchRow()
        self.switch_ignored.set_title("Ignorar este dispositivo / volume")
        self.switch_ignored.set_subtitle("Oculta da barra lateral e não envia músicas para este volume")
        self.switch_ignored.set_active(self.profile.ignored)
        group_id.add(self.switch_ignored)

        page_general.add(group_id)

        # Group: Escopo de Sincronização
        group_scope = Adw.PreferencesGroup()
        group_scope.set_title("Escopo da Sincronização")

        self.switch_sync_all = Adw.SwitchRow()
        self.switch_sync_all.set_title("Sincronizar toda a biblioteca")
        self.switch_sync_all.set_subtitle("Copia todas as faixas da biblioteca para o player")
        self.switch_sync_all.set_active(self.profile.sync_all_library)
        group_scope.add(self.switch_sync_all)

        self.switch_delete = Adw.SwitchRow()
        self.switch_delete.set_title("Remover arquivos não selecionados")
        self.switch_delete.set_subtitle("Apaga do dispositivo músicas que não constam na lista de sincronização")
        self.switch_delete.set_active(self.profile.delete_unmatched_files)
        group_scope.add(self.switch_delete)

        page_general.add(group_scope)
        self.add(page_general)

        # Page 2: Conversão de Áudio (Transcoding)
        page_audio = Adw.PreferencesPage()
        page_audio.set_title("Conversão de Áudio")
        page_audio.set_icon_name("audio-x-generic-symbolic")

        group_transcode = Adw.PreferencesGroup()
        group_transcode.set_title("Regras de Transcoding (FFmpeg)")

        self.switch_transcode = Adw.SwitchRow()
        self.switch_transcode.set_title("Ativar conversão de formato")
        self.switch_transcode.set_subtitle("Converte músicas para o formato otimizado do dispositivo")
        self.switch_transcode.set_active(self.profile.transcode_enabled)
        group_transcode.add(self.switch_transcode)

        # Codec dropdown
        self.combo_codec = Adw.ComboRow()
        self.combo_codec.set_title("Formato de Destino")
        codecs_model = Gtk.StringList.new(["MP3", "AAC (m4a)", "Opus", "FLAC"])
        self.combo_codec.set_model(codecs_model)
        codec_idx = {"mp3": 0, "aac": 1, "opus": 2, "flac": 3}.get(self.profile.target_codec.lower(), 0)
        self.combo_codec.set_selected(codec_idx)
        group_transcode.add(self.combo_codec)

        # Bitrate dropdown
        self.combo_bitrate = Adw.ComboRow()
        self.combo_bitrate.set_title("Taxa de Bits (Qualidade)")
        bitrates_model = Gtk.StringList.new(["320 kbps (Alta fidelidade)", "256 kbps", "192 kbps", "128 kbps"])
        self.combo_bitrate.set_model(bitrates_model)
        bitrate_idx = {320: 0, 256: 1, 192: 2, 128: 3}.get(self.profile.target_bitrate, 0)
        self.combo_bitrate.set_selected(bitrate_idx)
        group_transcode.add(self.combo_bitrate)

        # MusicBee conversion filters
        self.switch_lossless_only = Adw.SwitchRow()
        self.switch_lossless_only.set_title("Converter apenas formatos sem perdas (FLAC/WAV)")
        self.switch_lossless_only.set_subtitle("Mantém arquivos MP3/AAC originais intactos e economiza espaço convertendo FLAC")
        self.switch_lossless_only.set_active(self.profile.convert_lossless_only)
        group_transcode.add(self.switch_lossless_only)

        self.switch_unsupported_only = Adw.SwitchRow()
        self.switch_unsupported_only.set_title("Converter apenas formatos não suportados")
        self.switch_unsupported_only.set_subtitle("Mantém qualquer formato suportado nativamente pelo dispositivo")
        self.switch_unsupported_only.set_active(self.profile.convert_unsupported_only)
        group_transcode.add(self.switch_unsupported_only)

        self.switch_volume = Adw.SwitchRow()
        self.switch_volume.set_title("Nivelamento de volume (EBU R128)")
        self.switch_volume.set_subtitle("Normaliza o ganho de áudio na transferência")
        self.switch_volume.set_active(self.profile.volume_leveling)
        group_transcode.add(self.switch_volume)

        page_audio.add(group_transcode)
        self.add(page_audio)

        # Page 3: Organização & Nomenclatura
        page_org = Adw.PreferencesPage()
        page_org.set_title("Nomenclatura")
        page_org.set_icon_name("folder-symbolic")

        group_naming = Adw.PreferencesGroup()
        group_naming.set_title("Estrutura de Pastas e Arquivos")

        self.switch_preserve_folders = Adw.SwitchRow()
        self.switch_preserve_folders.set_title("Preservar estrutura de pastas original")
        self.switch_preserve_folders.set_subtitle("Mantém a mesma hierarquia de diretórios e subpastas da sua biblioteca / NAS")
        self.switch_preserve_folders.set_active(self.profile.preserve_folder_structure)
        self.switch_preserve_folders.connect("notify::active", self._update_preview)
        group_naming.add(self.switch_preserve_folders)

        self.switch_preserve = Adw.SwitchRow()
        self.switch_preserve.set_title("Preservar nomes de arquivo originais")
        self.switch_preserve.set_subtitle("Mantém o nome de arquivo original na pasta de destino")
        self.switch_preserve.set_active(self.profile.preserve_filenames)
        self.switch_preserve.connect("notify::active", self._update_preview)
        group_naming.add(self.switch_preserve)

        self.entry_rule = Adw.EntryRow()
        self.entry_rule.set_title("Máscara de Organização")
        self.entry_rule.set_text(self.profile.naming_rule)
        self.entry_rule.connect("changed", self._update_preview)
        group_naming.add(self.entry_rule)

        # Live preview row
        self.row_preview = Adw.ActionRow()
        self.row_preview.set_title("Exemplo de Caminho Gerado")
        self.row_preview.set_subtitle("")
        group_naming.add(self.row_preview)
        self._update_preview()

        page_org.add(group_naming)
        self.add(page_org)

        # Page 4: Capas & Playlists
        page_media = Adw.PreferencesPage()
        page_media.set_title("Capas & Playlists")
        page_media.set_icon_name("media-optical-symbolic")

        group_art = Adw.PreferencesGroup()
        group_art.set_title("Capas de Álbum")

        self.switch_embed_art = Adw.SwitchRow()
        self.switch_embed_art.set_title("Embutir capa nas tags do arquivo")
        self.switch_embed_art.set_active(self.profile.artwork_embed)
        group_art.add(self.switch_embed_art)

        self.switch_file_art = Adw.SwitchRow()
        self.switch_file_art.set_title("Salvar arquivo cover.jpg na pasta")
        self.switch_file_art.set_active(self.profile.artwork_save_file)
        group_art.add(self.switch_file_art)

        self.combo_art_size = Adw.ComboRow()
        self.combo_art_size.set_title("Tamanho Máximo da Capa")
        size_model = Gtk.StringList.new(["300x300 px (DAP econômico)", "500x500 px", "600x600 px", "800x800 px", "Sem redimensionamento (Original)"])
        self.combo_art_size.set_model(size_model)
        size_idx = {300: 0, 500: 1, 600: 2, 800: 3, 4000: 4}.get(self.profile.artwork_max_size, 2)
        self.combo_art_size.set_selected(size_idx)
        group_art.add(self.combo_art_size)

        page_media.add(group_art)

        group_playlist = Adw.PreferencesGroup()
        group_playlist.set_title("Playlists")

        self.switch_export_pl = Adw.SwitchRow()
        self.switch_export_pl.set_title("Sincronizar Playlists (M3U8)")
        self.switch_export_pl.set_active(self.profile.playlist_export)
        group_playlist.add(self.switch_export_pl)

        self.entry_pl_prefix = Adw.EntryRow()
        self.entry_pl_prefix.set_title("Prefixo de Caminho na Playlist")
        self.entry_pl_prefix.set_text(self.profile.playlist_path_prefix)
        group_playlist.add(self.entry_pl_prefix)

        page_media.add(group_playlist)
        self.add(page_media)

        # Salvar alterações ao fechar
        self.connect("close-request", self._on_close)

    def _update_preview(self, *args):
        if self.switch_preserve_folders.get_active():
            self.entry_rule.set_sensitive(False)
            self.row_preview.set_subtitle("[Estrutura original] Ex: Rock / 2024 - Álbum / 01 - Faixa.mp3")
            return

        self.entry_rule.set_sensitive(True)
        rule = self.entry_rule.get_text() or "{Artist}/{Album}/{Track:02d} - {Title}.{ext}"
        dummy = Track(
            artist="Daft Punk",
            album="Discovery",
            title="Harder, Better, Faster, Stronger",
            track_number=4,
            file_format="mp3"
        )
        preview = FileOrganizer.format_relative_path(dummy, rule, "mp3")
        self.row_preview.set_subtitle(preview)

    def _on_close(self, *args):
        # Save changes to profile
        self.profile.name = self.entry_name.get_text()
        self.profile.music_subfolder = self.entry_subfolder.get_text()
        self.profile.is_default = self.switch_default.get_active()
        self.profile.ignored = self.switch_ignored.get_active()
        if self.profile.ignored:
            self.profile.is_default = False
        self.profile.sync_all_library = self.switch_sync_all.get_active()
        self.profile.delete_unmatched_files = self.switch_delete.get_active()

        self.profile.transcode_enabled = self.switch_transcode.get_active()
        codec_names = ["mp3", "aac", "opus", "flac"]
        self.profile.target_codec = codec_names[self.combo_codec.get_selected()]
        bitrate_vals = [320, 256, 192, 128]
        self.profile.target_bitrate = bitrate_vals[self.combo_bitrate.get_selected()]
        self.profile.convert_lossless_only = self.switch_lossless_only.get_active()
        self.profile.convert_unsupported_only = self.switch_unsupported_only.get_active()
        self.profile.volume_leveling = self.switch_volume.get_active()

        self.profile.preserve_folder_structure = self.switch_preserve_folders.get_active()
        self.profile.preserve_filenames = self.switch_preserve.get_active()
        self.profile.naming_rule = self.entry_rule.get_text()

        self.profile.artwork_embed = self.switch_embed_art.get_active()
        self.profile.artwork_save_file = self.switch_file_art.get_active()
        sizes = [300, 500, 600, 800, 4000]
        self.profile.artwork_max_size = sizes[self.combo_art_size.get_selected()]

        self.profile.playlist_export = self.switch_export_pl.get_active()
        self.profile.playlist_path_prefix = self.entry_pl_prefix.get_text()

        if self.on_save_callback:
            self.on_save_callback(self.profile)
        return False
