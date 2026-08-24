#!/usr/bin/env bash
# Diagnostica completa dell'host: dipendenze, servizi, porte, rete.
# Non modifica niente. Exit code 1 se qualcosa di essenziale manca.
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

FAILED=0
check() { # check <descrizione> <comando...>
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$desc"; else err "$desc"; FAILED=1; fi
}
soft_check() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$desc"; else warn "$desc (opzionale)"; fi
}

step "Sistema"
info "Distro:  $(distro_pretty)  (famiglia: $(detect_family))"
info "Kernel:  $(uname -r)"
info "Repo:    $APP_DIR"

step "Dipendenze VM"
check "qemu-system-x86_64 presente"  command -v qemu-system-x86_64
check "qemu-img presente"            command -v qemu-img
check "/dev/kvm leggibile e scrivibile" bash -c '[[ -r /dev/kvm && -w /dev/kvm ]]'
soft_check "swtpm presente (serve solo per Windows 11)" command -v swtpm
if [[ -x "$APP_DIR/.venv/bin/websockify" ]]; then
  ok "websockify presente nel venv"
else
  err "websockify assente dal venv: rilancia ./install.sh"; FAILED=1
fi

ovmf_found=""
for candidate in /usr/share/edk2/x64/OVMF_CODE.4m.fd /usr/share/OVMF/OVMF_CODE_4M.fd \
                 /usr/share/edk2/ovmf/OVMF_CODE.fd /usr/share/OVMF/OVMF_CODE.fd; do
  [[ -f "$candidate" ]] && { ovmf_found="$candidate"; break; }
done
if [[ -n "$ovmf_found" ]]; then ok "Firmware UEFI: $ovmf_found"
else warn "Firmware UEFI non trovato: niente VM Windows 11 (installa edk2-ovmf / ovmf)"; fi

if [[ -f "$APP_DIR/novnc/vnc.html" ]]; then ok "noVNC: submodule del repo"
elif [[ -f /usr/share/novnc/vnc.html ]]; then ok "noVNC: /usr/share/novnc"
elif [[ -f /usr/share/webapps/novnc/vnc.html ]]; then ok "noVNC: /usr/share/webapps/novnc"
else err "noVNC non trovato: git submodule update --init"; FAILED=1; fi

step "Dipendenze Minecraft"
check "java presente" command -v java
[[ -d "$APP_DIR/minecraft/servers" ]] && ok "Cartella server Minecraft presente" || warn "minecraft/servers assente"

step "Configurazione"
if [[ -f "$APP_DIR/config.env" ]]; then
  ok "config.env presente"
  perms="$(stat -c '%a' "$APP_DIR/config.env")"
  [[ "$perms" == "600" ]] && ok "Permessi config.env: $perms" || warn "config.env ha permessi $perms: contiene segreti, meglio 600"
else
  warn "config.env assente: si usano i default (cp config.example.env config.env)"
fi
iso_count=$(find "$APP_DIR/isos" -maxdepth 1 \( -name '*.iso' -o -name '*.img' \) 2>/dev/null | wc -l)
[[ "$iso_count" -gt 0 ]] && ok "ISO disponibili: $iso_count" || warn "Nessuna ISO in isos/: nessuna distro sara' avviabile"

step "Servizi"
for unit in quickllm-sandbox comfyui lmstudio-headless beszel-agent; do
  if ! systemctl list-unit-files "$unit.service" >/dev/null 2>&1 || \
     ! systemctl cat "$unit.service" >/dev/null 2>&1; then
    info "$unit: non installato"
  elif systemctl is-active --quiet "$unit.service"; then
    ok "$unit: attivo"
  else
    err "$unit: installato ma NON attivo  (journalctl -u $unit -n 50)"; FAILED=1
  fi
done

step "Rete"
port="$(config_get SANDBOX_PORT 8765)"
if health="$(curl -fsS -m 5 "http://127.0.0.1:$port/health" 2>/dev/null)"; then
  ok "API worker risponde su :$port"
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$health" <<'PY'
import json, sys
data = json.loads(sys.argv[1])
for key, value in data.get("network", {}).items():
    print(f"     {key:20} {value}")
bad = [k for k, v in data.get("checks", {}).items() if v is False]
if bad:
    print(f"     dipendenze mancanti: {', '.join(bad)}")
PY
  fi
else
  err "API worker non risponde su 127.0.0.1:$port"; FAILED=1
fi

if command -v tailscale >/dev/null 2>&1; then
  if tailscale status >/dev/null 2>&1; then
    ok "Tailscale connesso: $(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || echo '?')"
    # Niente pipe verso grep -q: uscirebbe in anticipo mandando SIGPIPE a
    # tailscale, e con pipefail il test fallirebbe pur essendo il funnel attivo.
    serve_status="$(tailscale serve status 2>/dev/null)"
    if [[ "$serve_status" == *"Funnel on"* ]]; then
      ok "Funnel Minecraft attivo"
    else
      warn "Funnel Minecraft non attivo: ./scripts/tailscale-funnel.sh"
    fi
  else
    warn "Tailscale installato ma non connesso: sudo tailscale up"
  fi
else
  warn "Tailscale assente: Minecraft sara' raggiungibile solo in LAN"
fi

step "Esito"
if [[ $FAILED -eq 0 ]]; then
  ok "Tutti i controlli essenziali passati"
else
  err "Alcuni controlli essenziali sono falliti (vedi sopra)"
fi
exit $FAILED
