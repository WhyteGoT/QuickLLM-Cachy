# Installare (o reinstallare) tutto

Da macchina Linux vuota a infrastruttura funzionante. Testato su famiglia Arch
(CachyOS); i comandi per Debian/Ubuntu e Fedora sono indicati dove cambiano.

> **Windows non e' supportato.** Il worker si regge su KVM e systemd, che su
> Windows non esistono. Se un giorno torni a Windows, la strada e' far girare
> questo repo dentro una VM Linux o in WSL2 (dove pero' KVM non c'e' e le VM
> vanno in emulazione pura, quindi lente).

---

## TL;DR

```bash
git clone --recurse-submodules https://github.com/WhyteGoT/QuickLLM-Cachy.git
cd QuickLLM-Cachy
./install.sh --all
./scripts/doctor.sh
```

Poi copia le ISO in `isos/` e rifai il Funnel con
`./scripts/tailscale-funnel.sh up`.

---

## 1. Prerequisiti

- Una distro Linux con **systemd** (Arch/CachyOS, Debian/Ubuntu, Fedora, openSUSE).
- **Virtualizzazione hardware attiva nel BIOS** (VT-x / AMD-V). Verifica:
  ```bash
  ls -l /dev/kvm       # deve esistere
  ```
  Se manca, entra nel BIOS e attiva `Intel VT-x` / `SVM Mode`. Senza KVM le VM
  non partono.
- `git` e `sudo`.
- Spazio disco: le ISO e i dischi qcow2 occupano decine di GB. Oggi
  `isos/` + `disks/` pesano ~58 GB.

---

## 2. Clonare il repo

```bash
git clone --recurse-submodules https://github.com/WhyteGoT/QuickLLM-Cachy.git
cd QuickLLM-Cachy
```

Se hai gia' clonato senza submodule:

```bash
git submodule update --init --recursive
```

Il submodule e' **noVNC**, la parte web della console delle VM.

---

## 3. Lanciare l'installer

```bash
./install.sh              # solo il worker QuickLLM
./install.sh --all        # worker + ComfyUI + LM Studio + Beszel
./install.sh --with comfyui,beszel
./install.sh --dry-run    # mostra cosa farebbe, senza toccare niente
```

Opzioni utili: `--skip-packages` (non toccare il package manager), `-y`
(non chiedere conferma).

### Cosa fa, in ordine

1. **Pacchetti.** Rileva la famiglia della distro e installa QEMU, swtpm,
   firmware UEFI, Python, Java, git. Se l'utente non ha accesso a `/dev/kvm`, lo
   aggiunge al gruppo `kvm` (serve logout/login).
2. **noVNC.** Inizializza il submodule; se fallisce, il worker ripiega sul
   noVNC di sistema.
3. **Virtualenv.** Crea `.venv` e ci installa `websockify`. Il worker lo trova
   perche' l'unit systemd mette `.venv/bin` in testa al `PATH`.
4. **Configurazione.** Crea `config.env` da `config.example.env` se manca, con
   permessi `600`. **Non sovrascrive un `config.env` esistente.**
5. **Servizi.** Genera gli unit da `services/*.in` sostituendo utente, gruppo e
   percorsi reali, e li scrive in `/etc/systemd/system/`.
6. **Avvio.** `daemon-reload` + `enable --now` di ogni servizio installato.

L'installer e' **idempotente**: rilanciarlo aggiorna gli unit senza perdere dati
o configurazione.

### Se il tuo package manager non e' supportato

```bash
./install.sh --skip-packages
```

e installa a mano l'equivalente di:
`qemu-system-x86_64`, `qemu-img`, `swtpm`, firmware OVMF/edk2, `python3` con
`venv`, una JRE headless, `git`. Per LM Studio serve anche `xvfb`.

---

## 4. Differenze per distro

| | Arch / CachyOS | Debian / Ubuntu | Fedora |
|---|---|---|---|
| QEMU | `qemu-full` | `qemu-system-x86` `qemu-utils` | `qemu-system-x86` `qemu-img` |
| Firmware UEFI | `edk2-ovmf` | `ovmf` | `edk2-ovmf` |
| Percorso OVMF | `/usr/share/edk2/x64/OVMF_CODE.4m.fd` | `/usr/share/OVMF/OVMF_CODE_4M.fd` | `/usr/share/edk2/ovmf/OVMF_CODE.fd` |
| TPM emulato | `swtpm` | `swtpm` `swtpm-tools` | `swtpm` `swtpm-tools` |
| Java | `jre-openjdk-headless` | `default-jre-headless` | `java-latest-openjdk-headless` |
| Xvfb (LM Studio) | `xorg-server-xvfb` | `xvfb` | `xorg-x11-server-Xvfb` |

**Non serve che tu sappia questi percorsi**: il worker li cerca da solo fra i
candidati noti. Se hai il firmware in un posto strano, forzalo in `config.env`:

```
OVMF_CODE_PATH=/percorso/OVMF_CODE.fd
OVMF_VARS_PATH=/percorso/OVMF_VARS.fd
```

Su Arch, `qemu-full` e' comodo ma pesante: `qemu-base` + `qemu-img` bastano.

---

## 5. Tailscale e il Funnel

Tailscale non e' installato da `install.sh`: ha un suo installer ufficiale.

```bash
# Arch
sudo pacman -S tailscale && sudo systemctl enable --now tailscaled

# Debian / Ubuntu / Fedora
curl -fsSL https://tailscale.com/install.sh | sh
sudo systemctl enable --now tailscaled

# In tutti i casi
sudo tailscale up
```

Poi riesponi Minecraft su internet:

```bash
./scripts/tailscale-funnel.sh up
```

Il Funnel richiede che **HTTPS e Funnel siano abilitati** nella admin console
del tailnet (`Settings → Feature previews`). La prima volta Tailscale te lo dice
e ti da' il link.

> Il Funnel e' l'unico stato che non sta in questo repo: vive nel nodo
> Tailscale. Va rifatto a ogni reinstallazione.

---

## 6. Servizi companion

`install.sh` genera i loro unit systemd, ma **non installa le applicazioni**:
sono pacchetti di terze parti con installer propri. L'installer se ne accorge e
salta il servizio con un avviso invece di rompersi.

### ComfyUI
Oggi arriva da **StabilityMatrix**. Installa StabilityMatrix, aggiungi il
pacchetto ComfyUI, poi allinea `config.env`:
```
COMFYUI_DIR=/home/<utente>/StabilityMatrix/Packages/ComfyUI
COMFYUI_PYTHON=/home/<utente>/StabilityMatrix/Packages/ComfyUI/venv/bin/python
```
e rilancia `./install.sh --with comfyui`.

### LM Studio
Su Arch: AUR (`lmstudio-bin`). Altrove: AppImage dal sito ufficiale — in quel
caso punta `LMSTUDIO_BIN` all'AppImage.

LM Studio e' un'app Electron senza vera modalita' server: `bin/lmstudio-headless-watchdog`
la tiene viva dentro un framebuffer virtuale (Xvfb) e la riavvia se cade. Per
questo serve `xvfb`.

### Beszel
L'agent si installa dall'**hub**: quando aggiungi un sistema, l'hub genera il
comando di installazione con la chiave gia' dentro. Copia da li' `KEY` e
`TOKEN`, mettili in `config.env`:
```
BESZEL_KEY=ssh-ed25519 AAAA...
BESZEL_TOKEN=...
BESZEL_HUB_URL=http://192.168.1.87:8090
```
e rilancia `./install.sh --with beszel`. L'installer li copia in
`/etc/quickllm/beszel-agent.env` (root, `0600`) — **non nell'unit systemd**,
dove sarebbero leggibili da chiunque con `systemctl cat`.

---

## 7. Migrare da una macchina all'altra

Sulla **vecchia** macchina:

```bash
./scripts/backup.sh /mnt/usb          # config, stato, mondi Minecraft
cp -r isos/ /mnt/usb/isos/            # opzionale: sono riscaricabili
tailscale serve status > /mnt/usb/funnel-config.txt   # per memoria
```

Sulla **nuova**:

```bash
git clone --recurse-submodules https://github.com/WhyteGoT/QuickLLM-Cachy.git
cd QuickLLM-Cachy
./install.sh --all
./scripts/backup.sh --restore /mnt/usb/quickllm-<data>.tar.gz
cp -r /mnt/usb/isos/* isos/
sudo tailscale up
./scripts/tailscale-funnel.sh up
./scripts/doctor.sh
```

Infine, su **QuickLLM** (l'altra macchina), aggiorna l'indirizzo del worker se
l'IP LAN o il nome Tailscale sono cambiati.

**Cosa non si porta dietro:** i dischi `qcow2` delle VM (ricreati vuoti al primo
avvio) e i jar PaperMC (riscaricati). Se ti servono davvero i dischi, copiali a
mano: sono in `disks/`.

---

## 8. Quando qualcosa non va

Prima mossa, sempre:

```bash
./scripts/doctor.sh
curl -s http://127.0.0.1:8765/health | python3 -m json.tool
```

| Sintomo | Causa probabile | Rimedio |
|---|---|---|
| `503 kvm_unavailable` | VT-x/AMD-V spento, o utente fuori dal gruppo `kvm` | Attiva nel BIOS; `sudo usermod -aG kvm $USER` e rifai login |
| `503 websockify_unavailable` | venv non creato o `PATH` dell'unit sbagliato | `./install.sh --skip-packages` |
| `503 novnc_unavailable` | submodule non inizializzato | `git submodule update --init --recursive` |
| `503 ovmf_unavailable` | firmware UEFI non installato | Installa `edk2-ovmf` / `ovmf`, o imposta `OVMF_CODE_PATH` |
| `503 swtpm_unavailable` | manca `swtpm` | Installa `swtpm` (solo Windows 11 ne ha bisogno) |
| `409 workload_already_running` | c'e' gia' una VM o un Minecraft attivo | Fermalo: solo un workload alla volta |
| Minecraft: "console non disponibile" | il worker e' stato riavviato mentre il server girava | Ferma e riavvia il server dal backend |
| Minecraft irraggiungibile da internet | Funnel non ripristinato | `./scripts/tailscale-funnel.sh up` |
| Il servizio non parte | dipendenza o percorso sbagliato | `journalctl -u quickllm-sandbox -n 50` |
| VM avviata ma schermo nero | la ISO sta ancora bootando, o profilo hardware sbagliato | Aspetta; poi `tail -f logs/vm-*.log` |

Il worker scrive in `journalctl` un avviso all'avvio con l'elenco delle
dipendenze mancanti — e' il modo piu' rapido per capire cosa manca dopo una
reinstallazione.
