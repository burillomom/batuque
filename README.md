# SoundBee 🐝

**SoundBee** é um gerenciador de biblioteca musical e sincronizador de dispositivos open-source para Linux, desenvolvido e projetado com base na arquitetura e nas rotinas do **MusicBee 3.6** (descobertas via engenharia reversa com **REA**).

Projetado especificamente para quem possui uma biblioteca musical centralizada (como em um **NAS**) e precisa sincronizar músicas para múltiplos dispositivos portáteis (**DAPs**, smartphones Android, cartões SD, pendrives, som automotivo), permitindo uma **configuração independente e personalizada para cada dispositivo**.

---

## Principais Funcionalidades

### 1. Conexão Nativa com NAS (Network Attached Storage)
- **Suporte a Compartilhamentos de Rede:** Conecta-se a compartilhamentos **SMB/CIFS** (`smb://`), **NFS** (`nfs://`), **WebDAV** (`davs://`) ou pontos de montagem locais (`/mnt/nas`, `/media/nas`).
- **Montagem Automática via GIO/GVFS:** Monta e acessa compartilhamentos diretamente em espaço de usuário, sem necessidade de privilégios de `root` ou edição manual de `/etc/fstab`.
- **Indexação Incremental de Alta Velocidade (SQLite):** Verifica carimbos de data/hora (`mtime`) e tamanhos de arquivo para evitar transferências lentas de tags pela rede. Re-escanear dezenas de milhares de faixas no NAS leva segundos.

### 2. Configurações Individuais por Dispositivo (*Device Profiles*)
Assim como o MusicBee, o SoundBee permite salvar perfis de configuração exclusivos para cada dispositivo conectado:
- **Transcoding sob Demanda (FFmpeg):**
  - Converte formatos na sincronização para economizar espaço de armazenamento (ex: FLAC/WAV para MP3 320k, AAC 256k ou Opus 192k).
  - Regra *"Converter apenas formatos sem perdas"*: converte arquivos FLAC, mas transfere arquivos MP3 já existentes sem re-codificação.
  - Regra *"Converter apenas formatos não suportados"*: respeita a lista de codecs suportados pelo player.
  - Normalização de ganho de volume (EBU R128).
- **Máscaras de Nomenclatura e Pastas:**
  - Padroniza a organização de diretórios por tags (ex: `{Artist}/{Album}/{Track:02d} - {Title}.{ext}`).
  - Sanitização automática de caracteres proibidos em sistemas de arquivos **FAT32** e **exFAT** (comuns em cartões micro-SD e DAPs).
- **Gestão de Capas de Álbum:**
  - Redimensionamento automático de capas (ex: limitar a 500x500 ou 600x600 px para players portáteis com pouca memória RAM).
  - Opções para embutir arte diretamente nas tags (ID3v2.3 / Vorbis) ou salvar como `cover.jpg` na pasta do álbum.
- **Exportação de Playlists:**
  - Gera playlists **M3U8** com caminhos relativos ou prefixos customizados (ex: `/storage/emulated/0/Music/` para players Android).
- **Limpeza de Órfãos:**
  - Remove do player arquivos excluídos da biblioteca ou desmarcados da sincronização.

### 3. Detecção Automática de Dispositivos (Plug-and-Play)
- Usa o `Gio.VolumeMonitor` do Linux para detectar instantaneamente a inserção de pendrives, cartões SD, DAPs (FiiO, Sony Walkman, Astell&Kern, Shanling, Hiby) e dispositivos MTP.
- Permite criar **Dispositivos Virtuais** apontando para qualquer pasta local ou remota.

---

## Arquitetura do Projeto

```
soundbee/
├── soundbee/
│   ├── core/
│   │   ├── models.py       # Modelos de dados (Track, DeviceProfile, NASSource, SyncPlan)
│   │   ├── database.py     # SQLite com cache incremental e busca
│   │   ├── nas.py          # Conector e montador de compartilhamentos NAS (GIO/GVFS)
│   │   ├── scanner.py      # Scanner incremental de biblioteca
│   │   ├── metadata.py     # Leitor de tags (Mutagen: ID3, FLAC, Vorbis, MP4)
│   │   ├── transcoder.py   # Motor de conversão de áudio (FFmpeg)
│   │   ├── organizer.py    # Motor de templates e sanitização FAT32
│   │   ├── artwork.py      # Extrator, redimensionador e embutidor de capas
│   │   ├── playlist.py     # Gerador de playlists M3U8
│   │   └── sync_engine.py  # Motor de sincronização diferencial
│   ├── devices/
│   │   ├── detector.py     # Monitor de hardware plug-and-play (GIO VolumeMonitor)
│   │   └── manager.py      # Persistência de perfis JSON em ~/.config/soundbee/devices/
│   ├── ui/
│   │   ├── app.py          # Aplicação GTK 4 + Libadwaita
│   │   ├── main_window.py  # Janela principal adaptativa
│   │   ├── device_dialog.py# Janela de configurações por dispositivo (estilo MusicBee)
│   │   └── nas_dialog.py   # Diálogo de conexão com servidor NAS
│   └── cli.py              # Interface de linha de comando para uso headless
├── tests/                  # Suíte de testes unitários automatizados
├── pyproject.toml
└── README.md
```

---

## Como Usar

### 1. Interface Gráfica (GUI)
Para abrir a interface nativa em GTK 4 / Libadwaita:
```bash
python3 -m soundbee
```

### 2. Linha de Comando (CLI)

**Ver estatísticas da biblioteca:**
```bash
python3 -m soundbee stats
```

**Escanear pasta de músicas:**
```bash
python3 -m soundbee scan /caminho/para/musicas
```

**Conectar e escanear um NAS:**
```bash
# Adicionar compartilhamento SMB ou NFS
python3 -m soundbee nas add "Meu NAS" "smb://192.168.1.100/musicas"

# Listar fontes NAS
python3 -m soundbee nas list

# Escanear
python3 -m soundbee nas scan <ID_DO_NAS>
```

**Listar dispositivos conectados:**
```bash
python3 -m soundbee devices list
```

**Sincronizar com simulação prévia (dry-run):**
```bash
python3 -m soundbee sync <DEVICE_ID> --dry-run
```

**Executar a sincronização real:**
```bash
python3 -m soundbee sync <DEVICE_ID>
```

---

## Licença
GPLv3. Código aberto e livre para a comunidade Linux.
