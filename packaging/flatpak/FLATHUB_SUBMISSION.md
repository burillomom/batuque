# Como Publicar o Batuque no Flathub 🚀

O Flathub é o repositório central de aplicativos Linux. Uma vez aprovado no Flathub, o Batuque poderá ser instalado com 1 clique no **Fedora, Ubuntu, Debian, Arch Linux, Pop!_OS, Steam Deck, Linux Mint** e em qualquer distribuição moderna, aparecendo nas lojas de aplicativos (GNOME Software e KDE Discover).

---

## 📋 Arquivos Já Preparados

Todos os arquivos exigidos pelas diretrizes do Flathub já foram criados e validados no projeto:
- **Manifesto Flatpak**: `packaging/flatpak/org.batuque.Batuque.yaml`
- **Metainfo AppStream**: `data/org.batuque.Batuque.metainfo.xml` (Validado pelo `appstreamcli`)
- **Arquivo Desktop**: `data/org.batuque.Batuque.desktop` (Validado pelo `desktop-file-validate`)
- **Ícone Vetorial**: `data/icons/org.batuque.Batuque.svg`
- **Captura de Tela**: `data/screenshots/main_grid.png`

---

## 🎯 Método 1: Pelo Portal Web do Flathub (Mais Fácil e Rápido)

O Flathub possui um portal web moderno para submissão de novos aplicativos:

1. Acesse **[flathub.org/apps/new](https://flathub.org/apps/new)** e faça login com sua conta do GitHub (`burillomom`).
2. Digite o App ID: `org.batuque.Batuque`.
3. Indique o link do repositório no GitHub: `https://github.com/burillomom/batuque`.
4. O portal do Flathub lê o manifesto e cria o Pull Request de inclusão automaticamente para você!

---

## 🛠️ Método 2: Pelo Fluxo Tradicional via Pull Request no GitHub

Se preferir o fluxo clássico de desenvolvedor:

### 1. Faça um Fork do repositório do Flathub
1. Acesse **[github.com/flathub/flathub](https://github.com/flathub/flathub)**.
2. Clique no botão **Fork** (canto superior direito) para criar uma cópia na sua conta `burillomom/flathub`.

### 2. Crie uma Branch para o Batuque
No seu computador:
```bash
git clone git@github.com:burillomom/flathub.git ~/desenvolvimento/flathub-submit
cd ~/desenvolvimento/flathub-submit
git checkout -b add-org.batuque.Batuque
```

### 3. Copie o Manifesto
Copie o arquivo do manifesto para a raiz do repositório:
```bash
cp ~/desenvolvimento/soundbee/packaging/flatpak/org.batuque.Batuque.yaml .
git add org.batuque.Batuque.yaml
git commit -m "Add org.batuque.Batuque"
git push origin add-org.batuque.Batuque
```

### 4. Abra o Pull Request
1. Acesse o seu fork no GitHub: `https://github.com/burillomom/flathub`.
2. Clique no botão verde **Compare & pull request**.
3. O bot do Flathub (`@flathubbot`) começará a compilar e testar o Batuque automaticamente em servidores x86_64 e ARM64.
4. Quando os revisores do Flathub aprovarem e fizerem o merge, o repositório oficial `https://github.com/flathub/org.batuque.Batuque` será criado e o app publicado no Flathub!
