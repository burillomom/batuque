"""
NAS Connection & Configuration Dialog for SoundBee.
"""

import uuid
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from ..core.models import NASSource
from ..core.nas import NASManager


class NASConfigDialog(Adw.PreferencesWindow):
    def __init__(self, on_saved_callback=None, existing_source: NASSource = None, **kwargs):
        super().__init__(**kwargs)
        self.on_saved_callback = on_saved_callback
        self.source = existing_source or NASSource(id="nas_" + uuid.uuid4().hex[:8])

        self.set_title("Conectar Servidor NAS / Compartilhamento")
        self.set_default_size(600, 450)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        group.set_title("Configuração de Rede")
        group.set_description("Informe o endereço de rede (SMB/CIFS, NFS, WebDAV) ou a pasta local de montagem do seu NAS.")

        self.entry_name = Adw.EntryRow()
        self.entry_name.set_title("Nome da Conexão")
        self.entry_name.set_text(self.source.name or "Meu NAS")
        group.add(self.entry_name)

        self.entry_uri = Adw.EntryRow()
        self.entry_uri.set_title("URI de Rede ou Caminho Local")
        self.entry_uri.set_text(self.source.uri or "smb://192.168.1.100/musicas")
        group.add(self.entry_uri)

        # Status row
        self.row_status = Adw.ActionRow()
        self.row_status.set_title("Status da Conexão")
        self.row_status.set_subtitle("Não testado")

        btn_test = Gtk.Button(label="Testar / Montar")
        btn_test.set_valign(Gtk.Align.CENTER)
        btn_test.connect("clicked", self._on_test_clicked)
        self.row_status.add_suffix(btn_test)
        group.add(self.row_status)

        page.add(group)

        # Save button group
        group_action = Adw.PreferencesGroup()
        btn_save = Gtk.Button(label="Salvar e Conectar")
        btn_save.add_css_class("suggested-action")
        btn_save.set_halign(Gtk.Align.END)
        btn_save.connect("clicked", self._on_save_clicked)
        group_action.add(btn_save)
        page.add(group_action)

        self.add(page)

    def _on_test_clicked(self, *args):
        self.source.uri = self.entry_uri.get_text()
        self.row_status.set_subtitle("Conectando ao compartilhamento...")
        ok, mount_path, msg = NASManager.mount_share(self.source)
        if ok:
            self.row_status.set_subtitle(f"Acessível: {msg}")
        else:
            self.row_status.set_subtitle(f"Falha: {msg}")

    def _on_save_clicked(self, *args):
        self.source.name = self.entry_name.get_text()
        self.source.uri = self.entry_uri.get_text()
        ok, mount_path, msg = NASManager.mount_share(self.source)
        if self.on_saved_callback:
            self.on_saved_callback(self.source)
        self.close()
