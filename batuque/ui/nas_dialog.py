"""
NAS Connection & Configuration Dialog for Batuque.
Includes non-blocking asynchronous connection testing and credentials support.
"""

import uuid
import threading
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib

from ..core.models import NASSource
from ..core.nas import NASManager


class NASConfigDialog(Adw.PreferencesWindow):
    def __init__(self, on_saved_callback=None, existing_source: NASSource = None, **kwargs):
        super().__init__(**kwargs)
        self.on_saved_callback = on_saved_callback
        self.is_edit = existing_source is not None
        self.source = existing_source or NASSource(id="nas_" + uuid.uuid4().hex[:8])

        title = f"Configurar Servidor: {self.source.name}" if self.is_edit else "Conectar Servidor NAS / Compartilhamento"
        self.set_title(title)
        self.set_default_size(620, 520)

        page = Adw.PreferencesPage()

        # Group 1: Endereço do Servidor
        group_net = Adw.PreferencesGroup()
        group_net.set_title("Endereço de Rede")
        group_net.set_description("Informe o endereço SMB, NFS ou a pasta local de montagem.")

        self.entry_name = Adw.EntryRow()
        self.entry_name.set_title("Nome da Conexão")
        self.entry_name.set_text(self.source.name or "Meu NAS")
        group_net.add(self.entry_name)

        self.entry_uri = Adw.EntryRow()
        self.entry_uri.set_title("Endereço (URI ou Caminho)")
        self.entry_uri.set_text(self.source.uri or "smb://100.79.195.46/NAS-4TB/Musica")
        group_net.add(self.entry_uri)

        page.add(group_net)

        # Group 2: Autenticação (Usuário e Senha)
        group_auth = Adw.PreferencesGroup()
        group_auth.set_title("Autenticação (se exigida pelo NAS)")

        self.entry_user = Adw.EntryRow()
        self.entry_user.set_title("Usuário")
        self.entry_user.set_text(self.source.username or "")
        group_auth.add(self.entry_user)

        self.entry_pass = Adw.PasswordEntryRow()
        self.entry_pass.set_title("Senha")
        self.entry_pass.set_text(self.source.password or "")
        group_auth.add(self.entry_pass)

        self.entry_domain = Adw.EntryRow()
        self.entry_domain.set_title("Domínio / Workgroup")
        self.entry_domain.set_text(self.source.domain or "WORKGROUP")
        group_auth.add(self.entry_domain)

        page.add(group_auth)

        # Group 3: Teste de Conexão
        group_status = Adw.PreferencesGroup()
        group_status.set_title("Status e Diagnóstico")

        self.row_status = Adw.ActionRow()
        self.row_status.set_title("Status da Conexão")
        self.row_status.set_subtitle("Preencha os dados e clique em 'Testar'")

        # Spinner indicator
        self.spinner = Gtk.Spinner()
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.row_status.add_suffix(self.spinner)

        self.btn_test = Gtk.Button(label="Testar / Montar")
        self.btn_test.set_valign(Gtk.Align.CENTER)
        self.btn_test.connect("clicked", self._on_test_clicked)
        self.row_status.add_suffix(self.btn_test)
        group_status.add(self.row_status)

        page.add(group_status)

        # Group 4: Ação Salvar
        group_action = Adw.PreferencesGroup()
        save_lbl = "Salvar Configurações" if self.is_edit else "Salvar e Indexar Músicas"
        self.btn_save = Gtk.Button(label=save_lbl)
        self.btn_save.add_css_class("suggested-action")
        self.btn_save.set_halign(Gtk.Align.END)
        self.btn_save.connect("clicked", self._on_save_clicked)
        group_action.add(self.btn_save)
        page.add(group_action)

        self.add(page)

    def _sync_source_from_inputs(self):
        self.source.name = self.entry_name.get_text().strip()
        self.source.uri = self.entry_uri.get_text().strip()
        self.source.username = self.entry_user.get_text().strip() or None
        self.source.password = self.entry_pass.get_text() or None
        self.source.domain = self.entry_domain.get_text().strip() or "WORKGROUP"

    def _on_test_clicked(self, *args):
        self._sync_source_from_inputs()

        # Start loading state
        self.spinner.start()
        self.btn_test.set_sensitive(False)
        self.btn_save.set_sensitive(False)
        self.row_status.set_subtitle("Conectando ao compartilhamento em segundo plano...")

        # Run connection test in worker thread to prevent freezing GTK main loop
        def worker():
            ok, mount_path, msg = NASManager.mount_share(self.source, timeout_seconds=8)
            GLib.idle_add(lambda: self._on_test_completed(ok, mount_path, msg))

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_completed(self, ok: bool, mount_path: str, msg: str):
        self.spinner.stop()
        self.btn_test.set_sensitive(True)
        self.btn_save.set_sensitive(True)

        if ok:
            self.row_status.set_subtitle(f"Acessível: {mount_path} ({msg})")
        else:
            self.row_status.set_subtitle(f"Falha: {msg}")

    def _on_save_clicked(self, *args):
        self._sync_source_from_inputs()

        # Quick validation
        if not self.source.uri:
            self.row_status.set_subtitle("Por favor, preencha o endereço do compartilhamento.")
            return

        self.spinner.start()
        self.btn_test.set_sensitive(False)
        self.btn_save.set_sensitive(False)
        self.row_status.set_subtitle("Montando e salvando...")

        def worker():
            ok, mount_path, msg = NASManager.mount_share(self.source, timeout_seconds=8)
            GLib.idle_add(lambda: self._finish_save(ok, msg))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_save(self, ok: bool, msg: str):
        self.spinner.stop()
        if not ok:
            self.btn_test.set_sensitive(True)
            self.btn_save.set_sensitive(True)
            self.row_status.set_subtitle(f"Erro ao salvar: {msg}")
            return

        if self.on_saved_callback:
            self.on_saved_callback(self.source)
        self.close()
