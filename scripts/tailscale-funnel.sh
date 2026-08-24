#!/usr/bin/env bash
# Espone il server Minecraft su internet tramite Tailscale Funnel.
# Il Funnel e' l'unico pezzo di stato che NON vive in questo repo: e' salvato
# nel nodo Tailscale. Va rifatto ogni volta che reinstalli il sistema.
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PUBLIC_PORT="$(config_get MINECRAFT_PUBLIC_PORT 10000)"
LOCAL_PORT="$(config_get MINECRAFT_LOCAL_PORT 25565)"

command -v tailscale >/dev/null 2>&1 || die "tailscale non installato. Vedi docs/INSTALL.md."
tailscale status >/dev/null 2>&1 || die "Tailscale non connesso. Esegui: sudo tailscale up"

case "${1:-up}" in
  up)
    step "Espongo Minecraft: :$PUBLIC_PORT (pubblico) -> 127.0.0.1:$LOCAL_PORT"
    info "Se e' la prima volta, Tailscale chiedera' di abilitare il Funnel dalla console admin."
    sudo tailscale funnel --bg --tcp="$PUBLIC_PORT" "tcp://127.0.0.1:$LOCAL_PORT" \
      || die "Funnel fallito. Controlla che HTTPS e Funnel siano abilitati nella admin console del tailnet."
    ok "Funnel attivo"
    tailscale serve status
    ;;
  down)
    step "Rimuovo il Funnel sulla porta $PUBLIC_PORT"
    sudo tailscale funnel --tcp="$PUBLIC_PORT" off || die "Rimozione fallita"
    ok "Funnel rimosso"
    ;;
  status)
    tailscale serve status
    ;;
  *)
    die "Uso: $0 [up|down|status]"
    ;;
esac
