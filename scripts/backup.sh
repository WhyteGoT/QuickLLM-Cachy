#!/usr/bin/env bash
# Backup di cio' che NON e' ricostruibile: config, mondi Minecraft, plugin,
# stato. Esclude ISO, dischi qcow2 e jar (grossi e riscaricabili).
#
#   ./scripts/backup.sh                  -> ./backups/quickllm-<data>.tar.gz
#   ./scripts/backup.sh /mnt/usb         -> /mnt/usb/quickllm-<data>.tar.gz
#   ./scripts/backup.sh --restore FILE   -> ripristina un backup
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

if [[ "${1:-}" == "--restore" ]]; then
  archive="${2:-}"
  [[ -f "$archive" ]] || die "Uso: $0 --restore <file.tar.gz>"
  step "Ripristino da $archive"
  warn "Sovrascrivo config.env, data/ e minecraft/servers/ in $APP_DIR"
  read -rp "Confermi? [s/N] " reply
  [[ "$reply" =~ ^[sSyY]$ ]] || die "Annullato."
  tar -xzf "$archive" -C "$APP_DIR"
  ok "Ripristinato. Riavvia il worker: sudo systemctl restart quickllm-sandbox"
  exit 0
fi

DEST="${1:-$APP_DIR/backups}"
mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$DEST/quickllm-$STAMP.tar.gz"

step "Backup in $ARCHIVE"
ITEMS=()
[[ -f "$APP_DIR/config.env" ]]       && ITEMS+=("config.env")
[[ -d "$APP_DIR/data" ]]             && ITEMS+=("data")
[[ -d "$APP_DIR/minecraft/servers" ]] && ITEMS+=("minecraft/servers")

[[ ${#ITEMS[@]} -eq 0 ]] && die "Niente da salvare."
info "Includo: ${ITEMS[*]}"

# Percorsi relativi alla radice del repo: il restore li rimette dove stavano.
tar -czf "$ARCHIVE" -C "$APP_DIR" "${ITEMS[@]}" || die "tar fallito"

ok "Backup creato: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
info "Le ISO e i dischi qcow2 NON sono inclusi: sono ricreabili."
