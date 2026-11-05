# Batuque 🥁

**Batuque** é um gerenciador de biblioteca musical e sincronizador de dispositivos portáteis (DAPs) open-source para Linux, desenvolvido e projetado com foco em alta fidelidade, velocidade e na arquitetura do **MusicBee 3.6**.

Projetado especificamente para quem possui uma biblioteca musical centralizada (como em um **NAS**) e precisa sincronizar músicas para múltiplos dispositivos portáteis (**DAPs** como FiiO, Sony Walkman, Astell&Kern, Hiby, Shanling, smartphones Android, cartões SD, pendrives), permitindo uma **configuração independente e personalizada para cada dispositivo**.

---

## Principais Funcionalidades

### 1. Conexão Nativa com NAS (Network Attached Storage) & Sincronização Incremental Ultra-Rápida
- **Suporte a Compartilhamentos de Rede:** Conecta-se a compartilhamentos **SMB/CIFS** (`smb://`), **NFS** (`nfs://`), **WebDAV** (`davs://`) ou pontos de montagem locais (`/mnt/nas`, `/media/nas`).
- **Streaming de Tags Não-Bloqueante (GioFileStream):** Leitura de metadados em streaming sob demanda via GIO, eliminando falhas em arquivos FLAC/MP3 com capas grandes ou embutidas de alta resolução.
- **Indexação Incremental O(1) de Alta Velocidade:** Comparação diferencial em memória RAM via carimbos de data/hora (`mtime`) e tamanho (`size_bytes`). Ao adicionar músicas novas, o Batuque verifica instantaneamente faixas já existentes e indexa em paralelo apenas as faixas novas/modificadas, sem reindexar toda a coleção do zero.

### 2. Configurações Individuais por Dispositivo (*Device Profiles*)
Assim como o MusicBee, o Batuque permite salvar perfis de configuração exclusivos para cada dispositivo conectado:
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

### 3. Detecção Automática de Dispositivos (Plug-and-Play) & Compatibilidade com DAPs
- Usa o `Gio.VolumeMonitor` do Linux para detectar instantaneamente a inserção de pendrives, cartões SD, DAPs (FiiO, Sony Walkman, Astell&Kern, Shanling, Hiby), iPod Classic (Disk Mode / Rockbox) e dispositivos MTP.
- **Tratamento de Sistema de Arquivos Rockbox / FAT32:** Auto-recuperação de montagens em modo somente-leitura com remount `rw` seguro.
- **Opção de Preservação de Pastas:** Permite manter a estrutura de pastas original da biblioteca exatamente como está no NAS/disco sem forçar templates de renomeação.
- Permite criar **Dispositivos Virtuais** apontando para qualquer pasta local ou remota.

### 4. Interface Gráfica Moderna e Flexível (GTK 4 / Libadwaita)
- **Grade de Capas Densa e Customizável:**
  - **Seletor de Colunas:** Modo automático responsivo ou fixo (4, 5, 6, 7, 8, 9, 10 ou 12 colunas).
  - **Zoom Contínuo das Capas (75px a 220px):** Ajuste fino com slider, botões de passo e atalho de mouse `Ctrl + Roda do Mouse`.
  - **Predefinições Rápidas (1 clique):** Mini (80px), Compacto (100px), Padrão (116px), Grande (150px) e Extra (190px).
- **Seleção Bidirecional Inteligente (Shift + Clique):**
  - Seleção contígua em lote rápida no estilo file-manager.
  - Correção de erros com reajuste automático: caso clique além do desejado, um novo Shift+Clique desmarca os itens excedentes imediatamente.
  - Desmarcação de intervalos com Shift+Clique a partir de um item desmarcado.
- **Auto-Cura e Otimização de Capas:**
  - Detecção automática de imagens JPEG truncadas ou com barras cinzas causadas por buffering incompleto no Rockbox/DAPs.
  - Substituição inteligente da arte e extração em segundo plano.

---

## Arquitetura do Projeto

```
batuque/
├── batuque/
│   ├── core/
│   │   ├── models.py       # Modelos de dados (Track, DeviceProfile, NASSource, SyncPlan)
│   │   ├── database.py     # SQLite com cache incremental e busca (~/.local/share/batuque/)
│   │   ├── nas.py          # Conector e montador de compartilhamentos NAS (GIO/GVFS)
│   │   ├── scanner.py      # Scanner incremental de biblioteca
│   │   ├── metadata.py     # Leitor de tags com streaming GioFileStream
│   │   ├── transcoder.py   # Motor de conversão de áudio (FFmpeg)
│   │   ├── organizer.py    # Motor de templates e sanitização FAT32
│   │   ├── artwork.py      # Extrator de capas com auto-cura e cache (~/.cache/batuque/covers/)
│   │   ├── playlist.py     # Gerador de playlists M3U8
│   │   └── sync_engine.py  # Motor de sincronização diferencial
│   ├── devices/
│   │   ├── detector.py     # Monitor de hardware plug-and-play (GIO VolumeMonitor)
│   │   ├── inspector.py    # Inspetor de conteúdo e otimizador de DAP
│   │   └── manager.py      # Persistência de perfis JSON em ~/.config/batuque/devices/
│   ├── ui/
│   │   ├── app.py          # Aplicação GTK 4 + Libadwaita
│   │   ├── main_window.py  # Janela principal adaptativa
│   │   ├── device_dialog.py# Janela de configurações por dispositivo (estilo MusicBee)
│   │   └── nas_dialog.py   # Diálogo de conexão com servidor NAS
│   └── cli.py              # Interface de linha de comando para uso headless
├── soundbee/               # Camada de compatibilidade retroativa para aliases existentes
├── tests/                  # Suíte de testes unitários automatizados
├── packaging/              # Scripts de empacotamento ZipApp e RPM Fedora
├── pyproject.toml
└── README.md
```

---

## Como Usar

### 1. Interface Gráfica (GUI)
Para abrir a interface nativa em GTK 4 / Libadwaita:
```bash
batuque
# ou via Python:
python3 -m batuque
```

### 2. Linha de Comando (CLI)

**Ver estatísticas da biblioteca:**
```bash
batuque stats
```

**Escanear pasta de músicas:**
```bash
batuque scan /caminho/para/musicas
```

**Conectar e escanear um NAS:**
```bash
# Adicionar compartilhamento SMB ou NFS
batuque nas add "Meu NAS" "smb://192.168.1.100/musicas"

# Listar fontes NAS
batuque nas list

# Escanear
batuque nas scan <ID_DO_NAS>
```

**Listar dispositivos conectados:**
```bash
batuque devices list
```

**Inspecionar álbuns e estado das capas no dispositivo:**
```bash
batuque devices inspect <DEVICE_ID>
```

**Reparar capas cortadas ou com barras cinzas no dispositivo:**
```bash
batuque devices repair-artwork <DEVICE_ID>
# Ou forçar a substituição de todas as capas do dispositivo:
batuque devices repair-artwork <DEVICE_ID> --all
```

**Otimizar capas pesadas/grandes no dispositivo:**
```bash
batuque devices optimize-artwork <DEVICE_ID>
```

**Sincronizar com simulação prévia (dry-run):**
```bash
batuque sync <DEVICE_ID> --dry-run
```

**Executar a sincronização real (com auto-cura automática de capas):**
```bash
batuque sync <DEVICE_ID>
# Ou forçando a sobrescrita de capas:
batuque sync <DEVICE_ID> --force-artwork
```

---

## Licença
GPLv3. Código aberto e livre para a comunidade Linux.
