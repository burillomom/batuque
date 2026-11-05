Name:           batuque
Version:        0.1.0
Release:        1%{?dist}
Summary:        Gerenciador de música e sincronizador de DAPs para Linux inspirado no MusicBee

License:        GPL-3.0-or-later
URL:            https://github.com/murillobom/soundbee
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Provides:       soundbee = %{version}-%{release}
Obsoletes:      soundbee < %{version}-%{release}

Requires:       python3 >= 3.10
Requires:       python3-gobject
Requires:       python3-mutagen
Requires:       python3-pillow
Requires:       gtk4
Requires:       libadwaita
Requires:       gvfs
Requires:       gvfs-smb
Requires:       ffmpeg-free

%description
Batuque é um gerenciador de coleções musicais open-source para Linux focado em
integração nativa com servidores NAS (SMB/NFS/WebDAV) e sincronização inteligente
para dispositivos portáteis (DAPs, USB, cartões SD, MTP e pastas virtuais).

%prep
%setup -q

%build
# Pure Python package

%install
rm -rf %{buildroot}

# Install Python packages (batuque and compatibility soundbee)
mkdir -p %{buildroot}%{python3_sitelib}/batuque
cp -r batuque/* %{buildroot}%{python3_sitelib}/batuque/

mkdir -p %{buildroot}%{python3_sitelib}/soundbee
cp -r soundbee/* %{buildroot}%{python3_sitelib}/soundbee/

# Install executable launcher
mkdir -p %{buildroot}%{_bindir}
cat << 'EOF' > %{buildroot}%{_bindir}/batuque
#!/usr/bin/python3
import sys
from batuque.__main__ import run_entrypoint
sys.exit(run_entrypoint() or 0)
EOF
chmod 0755 %{buildroot}%{_bindir}/batuque
ln -sf batuque %{buildroot}%{_bindir}/soundbee

# Install desktop file, icon, and AppStream metainfo
mkdir -p %{buildroot}%{_datadir}/applications
install -m 0644 data/org.batuque.Batuque.desktop %{buildroot}%{_datadir}/applications/org.batuque.Batuque.desktop

mkdir -p %{buildroot}%{_datadir}/icons/hicolor/scalable/apps
install -m 0644 data/icons/org.batuque.Batuque.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/org.batuque.Batuque.svg
install -m 0644 data/icons/org.soundbee.SoundBee.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/org.soundbee.SoundBee.svg

mkdir -p %{buildroot}%{_datadir}/metainfo
install -m 0644 data/org.batuque.Batuque.metainfo.xml %{buildroot}%{_datadir}/metainfo/org.batuque.Batuque.metainfo.xml

%files
%doc README.md
%{_bindir}/batuque
%{_bindir}/soundbee
%{python3_sitelib}/batuque/
%{python3_sitelib}/soundbee/
%{_datadir}/applications/org.batuque.Batuque.desktop
%{_datadir}/icons/hicolor/scalable/apps/org.batuque.Batuque.svg
%{_datadir}/icons/hicolor/scalable/apps/org.soundbee.SoundBee.svg
%{_datadir}/metainfo/org.batuque.Batuque.metainfo.xml

%changelog
* Wed Nov 04 2026 Murillo Bom <murillo@batuque.org> - 0.1.0-1
- Transição de identidade para Batuque 0.1.0 para Linux (GTK4 / Libadwaita)
- Novo ícone oficial de percussão brasileira com suporte Adwaita
- Compatibilidade integral mantida com perfis anteriores
