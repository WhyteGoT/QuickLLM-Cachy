#!/usr/bin/env bash
# =============================================================================
# QuickLLM Sandbox - installer
# =============================================================================
# Ricostruisce l'infrastruttura su una macchina Linux pulita.
# Idempotente: rieseguirlo aggiorna quello che c'e' senza rompere niente.
#
#   ./install.sh                      # solo il worker QuickLLM
#   ./install.sh --all                # worker + ComfyUI + LM Studio + Beszel
#   ./install.sh --with comfyui,beszel
#   ./install.sh --skip-packages      # non toccare il package manager
#   ./install.sh --dry-run            # mostra cosa farebbe
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "$SCRIPT_DIR/scripts/lib.sh"

COMPONENTS="worker"
SKIP_PACKAGES=0
DRY_RUN=0
ASSUME_YES=0

usage() { sed -n '2,15p' "$0" | sed 's/^# \?//'; exit 0; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)           COMPONENTS="worker,comfyui,lmstudio,beszel" ;;
    --with)          COMPONENTS="worker,$2"; shift ;;
    --with=*)        COMPONENTS="worker,${1#*=}" ;;
    --only)          COMPONENTS="$2"; shift ;;
    --only=*)        COMPONENTS="${1#*=}" ;;
    --skip-packages) SKIP_PACKAGES=1 ;;
    --dry-run)       DRY_RUN=1 ;;
    -y|--yes)        ASSUME_YES=1 ;;
    -h|--help)       usage ;;
    *)               die "Opzione sconosciuta: $1 (usa --help)" ;;
  esac
  shift
done

has_component() { [[ ",$COMPONENTS," == *",$1,"* ]]; }

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '    [dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

[[ $EUID -eq 0 ]] && die "Non lanciare come root: lo script chiede sudo quando serve."

TARGET_USER="$(id -un)"
TARGET_GROUP="$(id -gn)"
TARGET_HOME="$HOME"
FAMILY="$(detect_family)"

step "QuickLLM Sandbox - installazione"
info "Distro:      $(distro_pretty)  (famiglia: $FAMILY)"
info "Utente:      $TARGET_USER:$TARGET_GROUP"
info "Repo:        $APP_DIR"
info "Componenti:  $COMPONENTS"
[[ $DRY_RUN -eq 1 ]] && warn "DRY RUN: nessuna modifica verra' applicata"

[[ "$FAMILY" == "unknown" ]] && die "Package manager non riconosciuto. Usa --skip-packages e installa a mano le dipendenze elencate in docs/INSTALL.md."

if [[ $ASSUME_YES -eq 0 && $DRY_RUN -eq 0 ]]; then
  read -rp $'\nProcedo? [s/N] ' reply
  [[ "$reply" =~ ^[sSyY]$ ]] || die "Annullato."
fi

# -----------------------------------------------------------------------------
step "1/6  Pacchetti di sistema"
# -----------------------------------------------------------------------------
if [[ $SKIP_PACKAGES -eq 1 ]]; then
  info "Salto l'installazione dei pacchetti (--skip-packages)"
else
  read -ra PKGS <<<"$(core_packages "$FAMILY")"
  if has_component lmstudio; then
    xvfb="$(xvfb_package "$FAMILY")"
    [[ -n "$xvfb" ]] && PKGS+=("$xvfb")
  fi
  info "Installo: ${PKGS[*]}"
  run install_packages "$FAMILY" "${PKGS[@]}" || die "Installazione pacchetti fallita"
  ok "Pacchetti a posto"
fi

# KVM richiede che l'utente sia nel gruppo kvm/libvirt su molte distro.
if [[ -e /dev/kvm ]]; then
  if [[ -r /dev/kvm && -w /dev/kvm ]]; then
    ok "/dev/kvm accessibile"
  else
    warn "/dev/kvm non accessibile da $TARGET_USER: aggiungo al gruppo kvm"
    run sudo usermod -aG kvm "$TARGET_USER"
    warn "Devi fare logout/login (o riavviare) perche' il gruppo abbia effetto"
  fi
else
  warn "/dev/kvm assente: la virtualizzazione hardware e' disattivata nel BIOS o non supportata. Le VM non partiranno."
fi

# -----------------------------------------------------------------------------
step "2/6  noVNC"
# -----------------------------------------------------------------------------
if [[ -f "$APP_DIR/novnc/vnc.html" ]]; then
  ok "Submodule noVNC gia' presente"
else
  info "Inizializzo il submodule noVNC"
  run git -C "$APP_DIR" submodule update --init --recursive || \
    warn "Submodule fallito: il worker ripieghera' su /usr/share/novnc se installato"
fi

# -----------------------------------------------------------------------------
step "3/6  Ambiente Python"
# -----------------------------------------------------------------------------
PYTHON_BIN="$(command -v python3 || command -v python)"
[[ -n "$PYTHON_BIN" ]] || die "python3 non trovato"
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  info "Creo il virtualenv in .venv"
  run "$PYTHON_BIN" -m venv "$APP_DIR/.venv" || die "Creazione venv fallita"
fi
info "Installo le dipendenze Python (websockify)"
run "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip || warn "Aggiornamento pip fallito"
run "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt" || die "pip install fallito"
ok "Virtualenv pronto"

# -----------------------------------------------------------------------------
step "4/6  Configurazione"
# -----------------------------------------------------------------------------
if [[ -f "$APP_DIR/config.env" ]]; then
  ok "config.env gia' presente, lo lascio com'e'"
else
  info "Creo config.env da config.example.env"
  run cp "$APP_DIR/config.example.env" "$APP_DIR/config.env"
  run chmod 600 "$APP_DIR/config.env"
  warn "Rivedi $APP_DIR/config.env prima di usare i servizi companion"
fi

run mkdir -p "$APP_DIR"/{data,isos,disks,logs,run} "$APP_DIR"/minecraft/{jars,servers,run,logs}
ok "Cartelle dati create"

# -----------------------------------------------------------------------------
step "5/6  Servizi systemd"
# -----------------------------------------------------------------------------
INSTALLED_UNITS=()

install_unit() {
  local name="$1"; shift
  info "Genero $name"
  run render_template "$APP_DIR/services/${name}.in" "$UNIT_DIR/$name" "$@"
  INSTALLED_UNITS+=("$name")
}

COMMON_VARS=(
  "APP_DIR=$APP_DIR"
  "USER=$TARGET_USER"
  "GROUP=$TARGET_GROUP"
  "HOME=$TARGET_HOME"
  "SECRETS_DIR=$SECRETS_DIR"
)

if has_component worker; then
  install_unit "quickllm-sandbox.service" "${COMMON_VARS[@]}"
fi

if has_component comfyui; then
  comfy_dir="$(config_get COMFYUI_DIR "")"
  comfy_py="$(config_get COMFYUI_PYTHON "")"
  if [[ -z "$comfy_dir" || ! -d "$comfy_dir" ]]; then
    warn "ComfyUI saltato: COMFYUI_DIR ('$comfy_dir') non esiste. Installa ComfyUI e correggi config.env."
  elif [[ ! -x "$comfy_py" ]]; then
    warn "ComfyUI saltato: COMFYUI_PYTHON ('$comfy_py') non eseguibile."
  else
    install_unit "comfyui.service" "${COMMON_VARS[@]}" \
      "COMFYUI_DIR=$comfy_dir" \
      "COMFYUI_PYTHON=$comfy_py" \
      "COMFYUI_ARGS=$(config_get COMFYUI_ARGS '--listen 0.0.0.0')"
  fi
fi

if has_component lmstudio; then
  lms_bin="$(config_get LMSTUDIO_BIN /usr/bin/lm-studio)"
  if [[ ! -x "$lms_bin" ]]; then
    warn "LM Studio saltato: '$lms_bin' non trovato. Installa LM Studio e correggi LMSTUDIO_BIN in config.env."
  else
    install_unit "lmstudio-headless.service" "${COMMON_VARS[@]}" \
      "LMSTUDIO_BIN=$lms_bin" \
      "LMSTUDIO_CLI_DIR=$(config_get LMSTUDIO_CLI_DIR "$TARGET_HOME/.lmstudio/bin")" \
      "LMS_SERVER_HOST=$(config_get LMS_SERVER_HOST 0.0.0.0)"
  fi
fi

if has_component beszel; then
  beszel_bin="$(config_get BESZEL_AGENT_BIN /opt/beszel-agent/beszel-agent)"
  beszel_key="$(config_get BESZEL_KEY "")"
  beszel_token="$(config_get BESZEL_TOKEN "")"
  if [[ ! -x "$beszel_bin" ]]; then
    warn "Beszel saltato: '$beszel_bin' non trovato. Vedi docs/INSTALL.md per installare l'agent."
  elif [[ -z "$beszel_key" || -z "$beszel_token" ]]; then
    warn "Beszel saltato: BESZEL_KEY o BESZEL_TOKEN vuoti in config.env. Prendili dall'hub."
  else
    id -u beszel >/dev/null 2>&1 || run sudo useradd --system --no-create-home --shell /usr/sbin/nologin beszel
    info "Scrivo i segreti Beszel in $SECRETS_DIR/beszel-agent.env (0600, root)"
    run sudo mkdir -p "$SECRETS_DIR"
    if [[ $DRY_RUN -eq 0 ]]; then
      printf 'PORT=%s\nKEY=%s\nTOKEN=%s\nHUB_URL=%s\n' \
        "$(config_get BESZEL_PORT 45876)" "$beszel_key" "$beszel_token" \
        "$(config_get BESZEL_HUB_URL '')" | sudo tee "$SECRETS_DIR/beszel-agent.env" >/dev/null
      sudo chmod 600 "$SECRETS_DIR/beszel-agent.env"
      sudo chown root:root "$SECRETS_DIR/beszel-agent.env"
    fi
    install_unit "beszel-agent.service" "${COMMON_VARS[@]}" "BESZEL_AGENT_BIN=$beszel_bin"
  fi
fi

# -----------------------------------------------------------------------------
step "6/6  Avvio"
# -----------------------------------------------------------------------------
if [[ ${#INSTALLED_UNITS[@]} -eq 0 ]]; then
  warn "Nessun servizio installato."
else
  run sudo systemctl daemon-reload
  for unit in "${INSTALLED_UNITS[@]}"; do
    info "Abilito e avvio $unit"
    run sudo systemctl enable --now "$unit" || warn "$unit non e' partito: systemctl status $unit"
  done
fi

step "Fatto"
cat <<SUMMARY
Servizi installati: ${INSTALLED_UNITS[*]:-nessuno}

Prossimi passi:
  1. Copia le ISO in $APP_DIR/isos/  (il worker le elenca in automatico)
  2. Verifica lo stato:      ./scripts/doctor.sh
  3. Esponi Minecraft:       ./scripts/tailscale-funnel.sh
  4. Config della macchina:  $APP_DIR/config.env

Guida completa: docs/INFRASTRUCTURE.md
SUMMARY
