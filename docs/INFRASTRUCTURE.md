# L'infrastruttura, spiegata

Questo documento e' la mappa completa di cosa gira su questa macchina, perche',
e come i pezzi si parlano. Se stai reinstallando il sistema parti da
[INSTALL.md](INSTALL.md); questo file serve a **capire**, quello a **rifare**.

---

## 1. Il quadro generale

**QuickLLM** e' un servizio LLM che gira su un'**altra** macchina. Non e' in
questo repo. Quando un utente di QuickLLM chiede "avviami una VM Debian" o
"tirami su un server Minecraft", QuickLLM non fa il lavoro: lo delega via HTTP a
questa macchina, che fa da **worker**.

Questo repo e' il worker e tutto cio' che gli sta intorno.

```
                          INTERNET
                              │
                   Tailscale Funnel (TCP 10000)
                              │
┌─────────────────────────────┼──────────────────────────────────────────┐
│  QUESTA MACCHINA  (host CachyOS, hostname Tailscale: cachyos)          │
│                             │                                          │
│   ┌─────────────────────────▼──────────────────────────────────┐       │
│   │  quickllm-sandbox.service          :8765  (API HTTP)        │      │
│   │  sandbox_server.py                                          │      │
│   │                                                             │      │
│   │   ├── VM:        QEMU/KVM ──> VNC :5901 ──> websockify :6080 │     │
│   │   │                                          └─> noVNC (web) │     │
│   │   └── Minecraft: PaperMC (java) ──> :25565                   │     │
│   └──────────────────────────────────────────────────────────────┘     │
│                                                                        │
│   ┌──────────────────────┐  ┌──────────────────────┐                   │
│   │ comfyui.service      │  │ lmstudio-headless    │                   │
│   │ :8188  immagini      │  │ :1234  API LLM local │                   │
│   └──────────────────────┘  └──────────────────────┘                   │
│                                                                        │
│   ┌──────────────────────┐                                             │
│   │ beszel-agent :45876  │──> hub di monitoraggio su 192.168.1.87:8090 │
│   └──────────────────────┘                                             │
└────────────────────────────────────────────────────────────────────────┘
                              │
                         LAN 192.168.1.0/24
                              │
                   ┌──────────▼──────────┐
                   │  192.168.1.87       │  hub Beszel + forge-generator
                   └─────────────────────┘
```

**Il punto chiave:** solo `quickllm-sandbox` parla con QuickLLM. Gli altri
servizi sono indipendenti e sopravvivono da soli — ma vanno reinstallati anche
loro quando cambi distro, ed e' per questo che stanno in questo repo.

---

## 2. Inventario dei servizi

| Servizio | Unit systemd | Porta | Cosa fa | Se muore |
|---|---|---|---|---|
| **QuickLLM worker** | `quickllm-sandbox.service` | 8765 | API che avvia VM e server Minecraft su richiesta di QuickLLM | QuickLLM perde VM e Minecraft |
| **Console VM** | (figlio del worker) | 6080 | `websockify` + noVNC: schermo della VM nel browser | Le VM girano ma non le vedi |
| **Minecraft** | (figlio del worker) | 25565 | Server PaperMC | Server offline |
| **ComfyUI** | `comfyui.service` | 8188 | Generazione immagini | Niente immagini |
| **LM Studio** | `lmstudio-headless.service` | 1234 | API LLM locale compatibile OpenAI | Niente inferenza locale |
| **Beszel agent** | `beszel-agent.service` | 45876 | Manda metriche host all'hub | Perdi solo il monitoraggio |
| **Tailscale** | `tailscaled.service` | — | Rete privata + Funnel pubblico | Minecraft non raggiungibile da fuori |

Tutti gli unit sono **generati** da `install.sh` a partire dai template in
`services/*.in`. Non modificarli direttamente in `/etc/systemd/system/`: le
modifiche verrebbero sovrascritte al prossimo `install.sh`. Modifica il template
o `config.env`.

---

## 3. La rete

### Indirizzi
- **LAN**: `192.168.1.2` (rilevato in automatico dalla rotta di default)
- **Tailscale**: `cachyos.tail9776fa.ts.net` / `100.81.164.96`
- **Hub Beszel / altro server**: `192.168.1.87`

### Cosa e' esposto e dove

| Porta | Ascolta su | Raggiungibile da |
|---|---|---|
| 8765 (API worker) | `0.0.0.0` | LAN + Tailscale. **Nessuna autenticazione.** |
| 6080 (noVNC) | `0.0.0.0` | LAN + Tailscale. **Nessuna autenticazione.** |
| 25565 (Minecraft) | `0.0.0.0` | LAN + Tailscale |
| 10000 (Funnel) | Tailscale | **Tutta internet** |
| 8188 (ComfyUI) | `0.0.0.0` | LAN + Tailscale. **Nessuna autenticazione.** |
| 1234 (LM Studio) | `0.0.0.0` | LAN + Tailscale. **Nessuna autenticazione.** |

### Il Funnel Tailscale

Il server Minecraft e' pubblico su internet tramite Tailscale Funnel:

```
internet → cachyos.tail9776fa.ts.net:10000 → 127.0.0.1:25565
```

**Questo e' l'unico pezzo di configurazione che non vive in questo repo.** Sta
salvato nello stato del nodo Tailscale, non su disco. Se reinstalli il sistema
va rifatto:

```bash
./scripts/tailscale-funnel.sh up
```

---

## 4. Cosa e' versionato e cosa no

La regola: **in git ci va solo cio' che non e' ne' segreto ne' ricreabile.**

| Percorso | In git? | Perche' |
|---|---|---|
| `sandbox_server.py`, `install.sh`, `scripts/`, `services/`, `bin/`, `docs/` | Si | E' il progetto |
| `config.example.env` | Si | Documenta ogni opzione |
| `config.env` | **No** | Contiene i segreti Beszel e i valori di questa macchina |
| `novnc/` | Si (submodule) | Puntatore a un commit upstream |
| `isos/` | No | Decine di GB, riscaricabili |
| `disks/` | No | Immagini qcow2, ricreate al bisogno |
| `minecraft/servers/` | No | Mondi e plugin: **vanno in backup**, non in git |
| `minecraft/jars/` | No | Riscaricati dall'API PaperMC |
| `logs/`, `run/`, `data/` | No | Runtime |

Da cui: `git clone` ti da' un sistema **funzionante ma vuoto**. Le ISO le
ricopi, i mondi Minecraft li recuperi dal backup.

### Backup

```bash
./scripts/backup.sh              # -> ./backups/quickllm-<data>.tar.gz
./scripts/backup.sh /mnt/usb     # altrove
./scripts/backup.sh --restore backups/quickllm-20260824-1600.tar.gz
```

Salva `config.env`, `data/` e `minecraft/servers/`. Esclude di proposito ISO,
dischi e jar: sono grossi e si riottengono.

---

## 5. Come funziona una VM

Quando arriva `POST /api/rag/vms/start`:

1. **Scelta della ISO.** Il worker elenca `isos/*.iso` e le mappa a un id
   derivato dal nome del file (`Debian.iso` → `debian`). Aggiungere una distro
   significa **solo copiare una ISO nella cartella**: nessun codice da toccare.
2. **Profilo hardware.** Dal nome della ISO il worker sceglie come emulare:
   - `windows11` → q35 + UEFI (OVMF) + TPM 2.0 emulato (`swtpm`) + e1000e.
     Windows 11 rifiuta di installarsi senza TPM, da cui `swtpm`.
     In piu' il worker manda `sendkey ret` per 15 secondi sul monitor QEMU per
     superare il prompt "Press any key to boot from CD".
   - `windows98` → i440fx, ACPI off, CPU pentium3, 512 MB, rete rtl8139.
   - `windowsxp` → i440fx, CPU qemu32, rete rtl8139.
   - tutto il resto → q35 + virtio (il piu' veloce).
3. **Disco.** `disks/<owner>-<distro>.qcow2`, creato se manca (40 GB, 60 per
   Win11). E' **per utente e per distro**: lo stesso utente che riavvia la
   stessa distro ritrova il suo disco.
4. **Console.** QEMU espone VNC su `127.0.0.1:5901` (solo locale), e
   `websockify` lo traduce in WebSocket su `0.0.0.0:6080` servendo anche i file
   di noVNC. Il browser apre `/vm-console/vnc.html`.

**Un solo workload alla volta** (`MAX_RUNNING_WORKLOADS=1`): una VM *oppure* un
server Minecraft, mai entrambi. E' una scelta deliberata — la macchina e' un
desktop, non un hypervisor.

---

## 6. API del worker

Base: `http://<host>:8765`

| Metodo | Endpoint | Note |
|---|---|---|
| GET | `/health` | Diagnostica completa: dipendenze, percorsi, rete |
| GET | `/api/rag/vms` | Capacita', distro disponibili, VM in esecuzione |
| POST | `/api/rag/vms/start` | `{"distro":"debian","owner_id":"..."}` |
| POST | `/api/rag/vms/stop` | `{"id":"..."}` (opzionale) |
| GET | `/api/rag/minecraft` | Stato del server |
| POST | `/api/rag/minecraft/start` | `{"owner_id","name","version"}`; version vuota o `latest` = ultima PaperMC |
| POST | `/api/rag/minecraft/stop` | |
| POST | `/api/rag/minecraft/command` | `{"command":"say ciao"}` senza `/` iniziale |
| POST | `/api/rag/minecraft/upload-world` | multipart, `.zip`/`.mcworld`, server fermo |
| POST | `/api/rag/minecraft/upload-plugins` | multipart, `.jar` o `.zip` di jar |
| POST | `/api/rag/minecraft/upload-mods` | multipart; finiscono in `mods/` |

Esempi:

```bash
curl http://127.0.0.1:8765/health

curl -X POST http://127.0.0.1:8765/api/rag/vms/start \
  -H 'Content-Type: application/json' \
  -d '{"distro":"debian","owner_id":"test"}'

curl -X POST http://127.0.0.1:8765/api/rag/minecraft/start \
  -H 'Content-Type: application/json' \
  -d '{"owner_id":"test","name":"Test server","version":"latest"}'

curl -X POST http://127.0.0.1:8765/api/rag/minecraft/upload-world \
  -F owner_id=test -F file=@world.zip
```

`/health` e' il posto da cui partire quando qualcosa non va: dice esattamente
quale dipendenza manca e quali percorsi ha risolto.

---

## 7. Sicurezza: leggi questo

**L'API non ha alcuna autenticazione, e CORS e' aperto a `*`.** Chiunque
raggiunga la porta 8765 puo' avviare VM, fermare server e caricare file. Lo
stesso vale per noVNC (6080), ComfyUI (8188) e LM Studio (1234).

Oggi regge perche' quelle porte sono su LAN e Tailscale, non su internet. Il
**Funnel espone solo la 10000 → Minecraft**, non l'API.

Se vuoi stringere:

1. **Chiudi l'API alla LAN** e usa solo Tailscale — in `config.env`:
   ```
   SANDBOX_HOST=127.0.0.1
   ```
   poi esponila ai soli nodi del tailnet con
   `tailscale serve --bg --tcp=8765 tcp://127.0.0.1:8765`.
2. **Non aprire mai la 8765 sul router.** Il Funnel Tailscale e' la via giusta
   per esporre roba, ed e' gia' limitato alla sola porta Minecraft.
3. **`config.env` e' `chmod 600`** e ignorato da git: i token Beszel stanno li'.
   Se un token finisce per sbaglio in un commit, va **ruotato**, non solo
   rimosso dal file.

---

## 8. Limiti noti

- **Console Minecraft e riavvio del worker.** Il canale stdin verso il processo
  Java vive in memoria (`MC_PROCS`). Se riavvii `quickllm-sandbox` mentre il
  server Minecraft gira, il server resta vivo ma
  `POST /api/rag/minecraft/command` risponde "console non disponibile" finche'
  non lo riavvii dal backend.
- **API PaperMC v2.** `ensure_paper_jar()` usa `api.papermc.io/v2`, che PaperMC
  ha dichiarato deprecata in favore della v3. Il giorno che la spengono, l'avvio
  di Minecraft fallisce: va aggiornata la funzione.
- **Rete delle VM in modalita' user.** Le VM usano `-netdev user`: hanno accesso
  a internet ma **non sono raggiungibili** dall'esterno e non si vedono tra
  loro. Va bene per il caso d'uso (console via noVNC), non per fare rete tra VM.
- **Un workload alla volta**, per scelta (vedi sezione 5).
- **VM Windows 11 senza `swtpm`**: se il pacchetto manca, l'avvio risponde
  `503 ovmf_unavailable` / `swtpm_unavailable`. Tutte le altre distro partono
  comunque.

---

## 9. Operazioni quotidiane

```bash
# Lo stato di tutto, in un colpo
./scripts/doctor.sh

# Log del worker
journalctl -u quickllm-sandbox -f

# Log di una VM o del server Minecraft
ls -lt logs/ | head
tail -f minecraft/logs/<owner>.log

# Riavviare dopo aver toccato config.env
sudo systemctl restart quickllm-sandbox

# Aggiungere una distro: basta la ISO
cp ~/Scaricati/Alpine.iso isos/
curl -s http://127.0.0.1:8765/api/rag/vms | grep alpine

# Funnel Minecraft
./scripts/tailscale-funnel.sh status
```
