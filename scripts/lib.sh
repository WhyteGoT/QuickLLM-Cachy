#!/usr/bin/env bash
# Funzioni condivise da install.sh, doctor.sh, backup.sh.
# Nessuna di queste esegue niente: fanno solo detect e stampa.

set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_DIR="/etc/quickllm"
UNIT_DIR="/etc/systemd/system"

# --- output ------------------------------------------------------------------
if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'
else
  C_RESET=""; C_BOLD=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""
fi

info()  { printf '%s==>%s %s\n' "$C_BLUE"  "$C_RESET" "$*"; }
ok()    { printf '%s  ok%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn()  { printf '%s  !!%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
err()   { printf '%s  KO%s %s\n' "$C_RED"   "$C_RESET" "$*" >&2; }
die()   { err "$*"; exit 1; }
step()  { printf '\n%s%s%s\n' "$C_BOLD" "$*" "$C_RESET"; }

# --- distro ------------------------------------------------------------------
# Ritorna la FAMIGLIA di pacchettizzazione, non il nome della distro: e' quello
# che conta per sapere quale package manager e quali nomi di pacchetto usare.
detect_family() {
  if command -v pacman  >/dev/null 2>&1; then echo "arch";   return; fi
  if command -v apt-get >/dev/null 2>&1; then echo "debian"; return; fi
  if command -v dnf     >/dev/null 2>&1; then echo "fedora"; return; fi
  if command -v zypper  >/dev/null 2>&1; then echo "suse";   return; fi
  echo "unknown"
}

distro_pretty() {
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    echo "${PRETTY_NAME:-${NAME:-sconosciuta}}"
  else
    echo "sconosciuta"
  fi
}

# Pacchetti necessari al worker, per famiglia.
core_packages() {
  case "$1" in
    arch)   echo "qemu-full swtpm edk2-ovmf python python-virtualenv jre-openjdk-headless git" ;;
    debian) echo "qemu-system-x86 qemu-utils swtpm swtpm-tools ovmf python3 python3-venv default-jre-headless git" ;;
    fedora) echo "qemu-system-x86 qemu-img swtpm swtpm-tools edk2-ovmf python3 python3-virtualenv java-latest-openjdk-headless git" ;;
    suse)   echo "qemu-x86 qemu-tools swtpm qemu-ovmf-x86_64 python3 python3-virtualenv java-openjdk-headless git" ;;
    *)      echo "" ;;
  esac
}

# Xvfb serve solo a LM Studio.
xvfb_package() {
  case "$1" in
    arch)   echo "xorg-server-xvfb" ;;
    debian) echo "xvfb" ;;
    fedora) echo "xorg-x11-server-Xvfb" ;;
    suse)   echo "xorg-x11-server-Xvfb" ;;
    *)      echo "" ;;
  esac
}

install_packages() {
  local family="$1"; shift
  local pkgs=("$@")
  [[ ${#pkgs[@]} -eq 0 ]] && return 0
  case "$family" in
    arch)   sudo pacman -S --needed --noconfirm "${pkgs[@]}" ;;
    debian) sudo apt-get update && sudo apt-get install -y "${pkgs[@]}" ;;
    fedora) sudo dnf install -y "${pkgs[@]}" ;;
    suse)   sudo zypper install -y "${pkgs[@]}" ;;
    *)      die "Package manager non riconosciuto: installa a mano ${pkgs[*]}" ;;
  esac
}

# --- config ------------------------------------------------------------------
# Legge una chiave da config.env senza fare source (config.env puo' contenere
# valori con spazi che romperebbero un source ingenuo).
config_get() {
  local key="$1" default="${2:-}" file="${3:-$APP_DIR/config.env}"
  [[ -r "$file" ]] || { echo "$default"; return; }
  local line
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" | tail -n1)"
  [[ -z "$line" ]] && { echo "$default"; return; }
  line="${line#*=}"
  line="${line%\"}"; line="${line#\"}"
  line="${line%\'}"; line="${line#\'}"
  [[ -z "$line" ]] && echo "$default" || echo "$line"
}

# Rende un template services/*.in sostituendo i placeholder @NOME@.
render_template() {
  local src="$1" dest="$2"; shift 2
  [[ -r "$src" ]] || die "Template mancante: $src"
  local content; content="$(cat "$src")"
  local pair key value
  for pair in "$@"; do
    key="${pair%%=*}"; value="${pair#*=}"
    content="${content//@${key}@/${value}}"
  done
  if [[ "$content" == *"@"*"@"* ]] && grep -q '@[A-Z_]\+@' <<<"$content"; then
    warn "Placeholder non sostituiti in $dest: $(grep -o '@[A-Z_]\+@' <<<"$content" | sort -u | tr '\n' ' ')"
  fi
  printf '%s\n' "$content" | sudo tee "$dest" >/dev/null
}
