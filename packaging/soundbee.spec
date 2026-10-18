Name:           soundbee
Version:        0.1.0
Release:        1%{?dist}
Summary:        Gerenciador de música e sincronizador de dispositivos inspirado no MusicBee

License:        GPL-3.0-or-later
URL:            https://github.com/murillobom/soundbee
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

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
SoundBee é um gerenciador de coleções musicais open-source para Linux focado em
integração nativa com servidores NAS (SMB/NFS/WebDAV) e sincronização inteligente
para dispositivos portáteis (DAPs, USB, cartões SD, MTP e pastas virtuais).

%prep
%setup -q

%build
# Pure Python package

%install
rm -rf %{buildroot}

# Install Python package
mkdir -p %{buildroot}%{python3_sitelib}/soundbee
cp -r soundbee/* %{buildroot}%{python3_sitelib}/soundbee/

# Install executable launcher
mkdir -p %{buildroot}%{_bindir}
cat << 'EOF' > %{buildroot}%{_bindir}/soundbee
#!/usr/bin/python3
import sys
from soundbee.__main__ import run_entrypoint
sys.exit(run_entrypoint() or 0)
EOF
chmod 0755 %{buildroot}%{_bindir}/soundbee

# Install desktop file, icon, and AppStream metainfo
mkdir -p %{buildroot}%{_datadir}/applications
install -m 0644 data/org.soundbee.SoundBee.desktop %{buildroot}%{_datadir}/applications/org.soundbee.SoundBee.desktop

mkdir -p %{buildroot}%{_datadir}/icons/hicolor/scalable/apps
install -m 0644 data/icons/org.soundbee.SoundBee.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/org.soundbee.SoundBee.svg

mkdir -p %{buildroot}%{_datadir}/metainfo
install -m 0644 data/org.soundbee.SoundBee.metainfo.xml %{buildroot}%{_datadir}/metainfo/org.soundbee.SoundBee.metainfo.xml

%files
%doc README.md
%{_bindir}/soundbee
%{python3_sitelib}/soundbee/
%{_datadir}/applications/org.soundbee.SoundBee.desktop
%{_datadir}/icons/hicolor/scalable/apps/org.soundbee.SoundBee.svg
%{_datadir}/metainfo/org.soundbee.SoundBee.metainfo.xml

%changelog
* Sun Oct 18 2026 Murillo Bom <murillo@soundbee.org> - 0.1.0-1
- Lançamento inicial do SoundBee 0.1.0 para Linux (GTK4 / Libadwaita)
