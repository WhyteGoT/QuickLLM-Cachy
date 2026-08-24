# QuickLLM Sandbox

Worker che esegue **VM QEMU/KVM** e **server Minecraft** per conto di QuickLLM,
che gira su un'altra macchina. Questo repo contiene il worker e tutta
l'infrastruttura di contorno di questo host, in modo da poterla ricostruire da
zero su qualsiasi distro Linux.

```bash
git clone --recurse-submodules https://github.com/WhyteGoT/QuickLLM-Cachy.git
cd QuickLLM-Cachy
./install.sh --all
./scripts/doctor.sh
```

## Documentazione

| | |
|---|---|
| **[docs/INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md)** | Come e' fatto tutto: servizi, porte, rete, API, sicurezza, limiti |
| **[docs/INSTALL.md](docs/INSTALL.md)** | Installare da zero, migrare su un'altra distro, troubleshooting |

## Struttura

```
sandbox_server.py       il worker: API HTTP, QEMU, Minecraft
install.sh              installer idempotente, rileva la distro
config.example.env      ogni opzione documentata  (copia in config.env)
services/*.in           template systemd, senza percorsi hardcoded
scripts/doctor.sh       diagnostica: dipendenze, servizi, porte, rete
scripts/backup.sh       backup e restore di config, stato e mondi Minecraft
scripts/tailscale-funnel.sh   espone Minecraft su internet
bin/                    watchdog di LM Studio
novnc/                  submodule: console web delle VM
```

## In breve

- **Configurazione in un posto solo.** Tutto sta in `config.env` (ignorato da
  git, contiene i segreti). Gli unit systemd non hanno piu' valori hardcoded.
- **Niente percorsi legati alla distro.** Firmware UEFI, noVNC, IP LAN e nome
  Tailscale vengono rilevati a runtime; ogni default e' sovrascrivibile.
- **Aggiungere una distro = copiare una ISO** in `isos/`. Nessun codice da
  toccare.
- **Un solo workload alla volta**: una VM *oppure* un server Minecraft.

> **Attenzione:** l'API non ha autenticazione. Tienila su LAN/Tailscale e non
> aprirla sul router. Vedi la sezione Sicurezza in
> [docs/INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md).
