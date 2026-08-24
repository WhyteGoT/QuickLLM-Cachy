#!/usr/bin/env python3
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ISO_DIR = BASE_DIR / "isos"
DISK_DIR = BASE_DIR / "disks"
RUN_DIR = BASE_DIR / "run"
LOG_DIR = BASE_DIR / "logs"
MC_DIR = BASE_DIR / "minecraft"
MC_JAR_DIR = MC_DIR / "jars"
MC_SERVER_DIR = MC_DIR / "servers"
MC_RUN_DIR = MC_DIR / "run"
MC_LOG_DIR = MC_DIR / "logs"
STATE_FILE = DATA_DIR / "state.json"
CONFIG_FILE = Path(os.environ.get("SANDBOX_CONFIG") or (BASE_DIR / "config.env"))


def load_env_file(path):
    """Seed os.environ from a KEY=VALUE file. Real environment variables win,
    so systemd Environment= lines always override config.env."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(CONFIG_FILE)


def env_str(name, default=""):
    return (os.environ.get(name) or "").strip() or default


def env_int(name, default):
    try:
        return int(env_str(name, str(default)))
    except ValueError:
        return default


# OVMF/edk2 firmware lives in a different place on every distro. Probe instead
# of hardcoding, and let OVMF_CODE_PATH / OVMF_VARS_PATH force a location.
OVMF_CODE_CANDIDATES = (
    "/usr/share/edk2/x64/OVMF_CODE.4m.fd",      # Arch, CachyOS, Manjaro (edk2-ovmf)
    "/usr/share/edk2/x64/OVMF_CODE.fd",
    "/usr/share/OVMF/OVMF_CODE_4M.fd",          # Debian, Ubuntu, Mint (ovmf)
    "/usr/share/OVMF/OVMF_CODE.fd",
    "/usr/share/edk2/ovmf/OVMF_CODE.fd",        # Fedora, RHEL (edk2-ovmf)
    "/usr/share/qemu/ovmf-x86_64-code.bin",     # openSUSE (qemu-ovmf-x86_64)
)
OVMF_VARS_CANDIDATES = (
    "/usr/share/edk2/x64/OVMF_VARS.4m.fd",
    "/usr/share/edk2/x64/OVMF_VARS.fd",
    "/usr/share/OVMF/OVMF_VARS_4M.fd",
    "/usr/share/OVMF/OVMF_VARS.fd",
    "/usr/share/edk2/ovmf/OVMF_VARS.fd",
    "/usr/share/qemu/ovmf-x86_64-vars.bin",
)


def first_existing(candidates, override=""):
    if override:
        path = Path(override)
        return path if path.exists() else None
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def ovmf_code_path():
    return first_existing(OVMF_CODE_CANDIDATES, env_str("OVMF_CODE_PATH"))


def ovmf_vars_path():
    return first_existing(OVMF_VARS_CANDIDATES, env_str("OVMF_VARS_PATH"))


def detect_lan_host():
    """Address of the interface that carries the default route. No packet is
    sent: connect() on UDP only consults the routing table."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def detect_tailscale_host():
    if not shutil.which("tailscale"):
        return ""
    try:
        raw = subprocess.check_output(
            ["tailscale", "status", "--json"], text=True, timeout=5, stderr=subprocess.DEVNULL
        )
        return ((json.loads(raw).get("Self") or {}).get("DNSName") or "").rstrip(".")
    except Exception:
        return ""


API_HOST = env_str("SANDBOX_HOST", "0.0.0.0")
API_PORT = env_int("SANDBOX_PORT", 8765)
LAN_HOST = env_str("SANDBOX_LAN_HOST") or detect_lan_host()
PUBLIC_MC_PORT = env_int("MINECRAFT_PUBLIC_PORT", 10000)
MC_PORT = env_int("MINECRAFT_LOCAL_PORT", 25565)
PUBLIC_MC_HOST = env_str("MINECRAFT_PUBLIC_HOST") or detect_tailscale_host() or LAN_HOST

DEFAULT_VM_MEMORY_MB = env_int("VM_MEMORY_MB", 4096)
DEFAULT_VM_VCPUS = env_int("VM_VCPUS", 4)
DEFAULT_VM_DISK_SIZE = env_str("VM_DISK_SIZE", "40G")
WIN11_VM_DISK_SIZE = env_str("VM_DISK_SIZE_WIN11", "60G")
MAX_RUNNING_WORKLOADS = env_int("MAX_RUNNING_WORKLOADS", 1)
HOST_RESERVE_MB = env_int("HOST_RESERVE_MB", 4096)
VNC_HOST = "127.0.0.1"
VNC_DISPLAY = env_int("VNC_DISPLAY", 1)
VNC_PORT = 5900 + VNC_DISPLAY
NOVNC_HOST = env_str("NOVNC_HOST", "0.0.0.0")
NOVNC_PORT = env_int("NOVNC_PORT", 6080)
WIN11_ALIASES = ("windows11", "windows-11", "win11", "win-11")
WIN98_ALIASES = ("windows98", "windows-98", "win98", "win-98")
WINXP_ALIASES = ("windowsxp", "windows-xp", "winxp", "win-xp")
WIN11_BOOT_KEY_SECONDS = env_int("WIN11_BOOT_KEY_SECONDS", 15)
WIN11_BOOT_KEY_INTERVAL = 0.25

DEFAULT_MC_MEMORY_MB = env_int("MINECRAFT_MEMORY_MB", 4096)
DEFAULT_MC_VCPUS = env_int("MINECRAFT_VCPUS", 4)
MAX_UPLOAD_BYTES = env_int("MAX_UPLOAD_MB", 512) * 1024 * 1024
WIN98_MEMORY_MB = env_int("VM_MEMORY_MB_WIN98", 512)

MC_PROCS = {}


def ensure_dirs():
    for path in [DATA_DIR, ISO_DIR, DISK_DIR, RUN_DIR, LOG_DIR, MC_JAR_DIR, MC_SERVER_DIR, MC_RUN_DIR, MC_LOG_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def load_state():
    if not STATE_FILE.exists():
        return {"vm": None, "minecraft": None}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"vm": None, "minecraft": None}
    return {"vm": state.get("vm"), "minecraft": state.get("minecraft")}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    tmp.replace(STATE_FILE)


def process_alive(pid):
    if not pid:
        return False
    try:
        pid = int(pid)
        os.kill(pid, 0)
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.exists():
            stat_tail = stat_path.read_text(encoding="utf-8").rsplit(") ", 1)[-1]
            state = stat_tail.split(maxsplit=1)[0]
            if state == "Z":
                return False
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False


def terminate_pid(pid, timeout=15):
    if not process_alive(pid):
        return
    try:
        os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except Exception:
            return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not process_alive(pid):
            return
        time.sleep(0.3)
    try:
        os.killpg(os.getpgid(int(pid)), signal.SIGKILL)
    except Exception:
        try:
            os.kill(int(pid), signal.SIGKILL)
        except Exception:
            pass


def read_pid_file(path):
    try:
        return int(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def cleanup_state():
    state = load_state()
    changed = False
    vm = state.get("vm")
    if vm and not process_alive(vm.get("qemu_pid")):
        if vm.get("websockify_pid"):
            terminate_pid(vm.get("websockify_pid"), timeout=2)
        if vm.get("tpm_pid"):
            terminate_pid(vm.get("tpm_pid"), timeout=2)
        state["vm"] = None
        changed = True
    mc = state.get("minecraft")
    if mc and not process_alive(mc.get("pid")):
        MC_PROCS.pop(mc.get("id"), None)
        state["minecraft"] = None
        changed = True
    if changed:
        save_state(state)
    return state


def slug(value, fallback="default"):
    value = (value or fallback).strip().lower()
    value = re.sub(r"[^a-z0-9_.-]+", "-", value)
    value = value.strip("-._")
    return value or fallback


def iso_id(path):
    return slug(path.stem)


def distro_name(path):
    words = re.split(r"[-_.]+", path.stem)
    return " ".join(w.capitalize() for w in words if w) or path.stem


def list_distros():
    distros = []
    for path in sorted(ISO_DIR.glob("*")):
        if path.is_file() and path.suffix.lower() in [".iso", ".img"]:
            did = iso_id(path)
            disk_present = any(DISK_DIR.glob(f"*-{did}.qcow2"))
            distros.append({
                "id": did,
                "name": distro_name(path),
                "iso_present": True,
                "disk_present": disk_present,
                "iso": path.name,
            })
    return distros


def find_iso(distro):
    wanted = slug(distro)
    for path in sorted(ISO_DIR.glob("*")):
        if path.is_file() and path.suffix.lower() in [".iso", ".img"] and iso_id(path) == wanted:
            return path
    return None


def matches_iso_alias(path, aliases):
    did = iso_id(path)
    name = path.stem.lower().replace("_", "-").replace(".", "-")
    compact = name.replace("-", "")
    return any(alias in did or alias in name or alias.replace("-", "") in compact for alias in aliases)


def vm_profile(path):
    if matches_iso_alias(path, WIN11_ALIASES):
        return "windows11"
    if matches_iso_alias(path, WIN98_ALIASES):
        return "windows98"
    if matches_iso_alias(path, WINXP_ALIASES):
        return "windowsxp"
    return "default"


def mem_available_mb():
    try:
        with Path("/proc/meminfo").open("r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def cpu_count():
    return os.cpu_count() or 1


def kvm_available():
    return os.path.exists("/dev/kvm") and os.access("/dev/kvm", os.R_OK | os.W_OK)


def active_workload(state):
    if state.get("vm"):
        return "vm"
    if state.get("minecraft"):
        return "minecraft"
    return None


def ensure_inside_base(path):
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(BASE_DIR)
    except ValueError:
        raise ApiError(400, {"error": "Percorso non valido."})
    return resolved


def minecraft_server_id(owner):
    return slug(owner, "anonymous")


def minecraft_server_dir(owner):
    return ensure_inside_base(MC_SERVER_DIR / minecraft_server_id(owner))


def ensure_minecraft_base_files(server_dir, body=None):
    server_dir.mkdir(parents=True, exist_ok=True)
    eula = server_dir / "eula.txt"
    if not eula.exists():
        eula.write_text("eula=true\n", encoding="utf-8")
    props = server_dir / "server.properties"
    if not props.exists():
        write_server_properties(server_dir, body or {})


def tail_text(path, max_bytes=8192):
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            raw = f.read()
        lines = raw.decode("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    cleaned = [line.strip() for line in lines if line.strip()]
    return "\n".join(cleaned[-8:])


NOVNC_CANDIDATES = (
    "novnc",                        # git submodule shipped with this repo
    "/usr/share/novnc",             # Debian, Ubuntu, Fedora (novnc)
    "/usr/share/webapps/novnc",     # Arch, CachyOS (novnc)
    "/usr/share/noVNC",
    "/usr/share/novnc-common",
)


def novnc_web_dir():
    override = env_str("NOVNC_WEB_DIR")
    if override:
        path = Path(override)
        return str(path) if (path / "vnc.html").exists() else None
    for candidate in NOVNC_CANDIDATES:
        path = Path(candidate)
        if not path.is_absolute():
            path = BASE_DIR / path
        if (path / "vnc.html").exists():
            return str(path)
    return None


def response_vm(vm):
    return {
        "id": vm["id"],
        "distro_id": vm["distro_id"],
        "name": vm["name"],
        "profile": vm.get("profile", "default"),
        "status": "running",
        "memory_mb": vm.get("memory_mb", DEFAULT_VM_MEMORY_MB),
        "vcpus": vm.get("vcpus", DEFAULT_VM_VCPUS),
        "novnc": {
            "public_url": "/vm-console/vnc.html?autoconnect=true&resize=scale&quality=5&compression=9&path=vm-console%2Fwebsockify"
        },
    }


def vm_status(owner_id=None):
    state = cleanup_state()
    running = []
    if state.get("vm"):
        running.append(response_vm(state["vm"]))
    return {
        "capacity": {
            "cpu_count": cpu_count(),
            "mem_available_mb": mem_available_mb(),
            "kvm_available": kvm_available(),
            "max_running_vms": MAX_RUNNING_WORKLOADS,
            "default_memory_mb": DEFAULT_VM_MEMORY_MB,
            "max_memory_mb": DEFAULT_VM_MEMORY_MB,
            "default_vcpus": DEFAULT_VM_VCPUS,
            "max_vcpus": DEFAULT_VM_VCPUS,
            "host_reserve_memory_mb": HOST_RESERVE_MB,
        },
        "distros": list_distros(),
        "running": running,
        "owner_id": owner_id or "",
    }


def run_logged(cmd, log_path, cwd=None, stdin=None):
    log = open(log_path, "ab", buffering=0)
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=stdin,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def start_swtpm(vm_id):
    if not shutil.which("swtpm"):
        raise ApiError(503, {"error": "swtpm_unavailable"})
    tpm_dir = RUN_DIR / f"tpm-{vm_id}"
    tpm_dir.mkdir(parents=True, exist_ok=True)
    sock = tpm_dir / "swtpm.sock"
    pid_file = tpm_dir / "swtpm.pid"
    for path in [sock, pid_file]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    cmd = [
        "swtpm",
        "socket",
        "--tpm2",
        "--tpmstate", f"dir={tpm_dir}",
        "--ctrl", f"type=unixio,path={sock}",
        "--pid", f"file={pid_file}",
        "--daemon",
    ]
    subprocess.check_call(cmd)
    deadline = time.time() + 3
    while time.time() < deadline:
        pid = read_pid_file(pid_file)
        if pid and sock.exists() and process_alive(pid):
            return pid, sock
        time.sleep(0.1)
    raise ApiError(500, {"error": "swtpm_failed_to_start"})


def send_hmp_command(monitor_path, command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        sock.connect(str(monitor_path))
        sock.sendall(command.encode("ascii") + b"\n")


def trigger_windows11_dvd_boot(monitor_path):
    deadline = time.time() + WIN11_BOOT_KEY_SECONDS
    while time.time() < deadline:
        try:
            send_hmp_command(monitor_path, "sendkey ret")
        except OSError:
            pass
        time.sleep(WIN11_BOOT_KEY_INTERVAL)


def qemu_size_to_bytes(size):
    match = re.fullmatch(r"(\d+)([KMGT]?)", str(size).strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"invalid qemu size: {size}")
    value = int(match.group(1))
    suffix = match.group(2).upper()
    multipliers = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    return value * multipliers[suffix]


def vm_disk_size(profile):
    if profile == "windows11":
        return WIN11_VM_DISK_SIZE
    return DEFAULT_VM_DISK_SIZE


def ensure_vm_disk(disk, size):
    if not disk.exists():
        subprocess.check_call(["qemu-img", "create", "-f", "qcow2", str(disk), size])
        return

    info = subprocess.check_output(["qemu-img", "info", "--output=json", str(disk)], text=True)
    virtual_size = int(json.loads(info).get("virtual-size", 0))
    desired_size = qemu_size_to_bytes(size)
    if virtual_size < desired_size:
        subprocess.check_call(["qemu-img", "resize", str(disk), size])


def windows11_vm_args(vm_id, disk):
    code_path = ovmf_code_path()
    vars_template = ovmf_vars_path()
    if not code_path or not vars_template:
        raise ApiError(503, {
            "error": "ovmf_unavailable",
            "hint": "installa il pacchetto UEFI di QEMU (edk2-ovmf su Arch/Fedora, ovmf su Debian)",
            "searched": list(OVMF_CODE_CANDIDATES),
        })
    vars_path = disk.with_suffix(".OVMF_VARS.fd")
    monitor_path = RUN_DIR / f"{vm_id}-monitor.sock"
    try:
        monitor_path.unlink()
    except FileNotFoundError:
        pass
    if not vars_path.exists():
        shutil.copyfile(vars_template, vars_path)
    tpm_pid, tpm_sock = start_swtpm(vm_id)
    args = [
        "-machine", "q35,accel=kvm",
        "-cpu", "host",
        "-drive", f"if=pflash,format=raw,readonly=on,file={code_path}",
        "-drive", f"if=pflash,format=raw,file={vars_path}",
        "-chardev", f"socket,id=chrtpm,path={tpm_sock}",
        "-tpmdev", "emulator,id=tpm0,chardev=chrtpm",
        "-device", "tpm-tis,tpmdev=tpm0",
        "-drive", f"file={disk},format=qcow2,if=none,id=drive0",
        "-device", "ich9-ahci,id=ahci",
        "-device", "ide-hd,drive=drive0,bus=ahci.0",
        "-device", "qemu-xhci,id=xhci",
        "-device", "usb-tablet,bus=xhci.0",
        "-monitor", f"unix:{monitor_path},server=on,wait=off",
    ]
    return args, tpm_pid, str(vars_path), str(monitor_path)


def legacy_windows_vm_args(profile, disk):
    args = [
        "-machine", "pc-i440fx-9.2,accel=kvm,acpi=off",
        "-drive", f"file={disk},format=qcow2,if=ide",
        "-vga", "std",
        "-rtc", "base=localtime",
    ]
    if profile == "windows98":
        args.extend(["-cpu", "pentium3"])
    else:
        args.extend(["-cpu", "qemu32"])
    return args


def start_vm(body):
    state = cleanup_state()
    if active_workload(state):
        raise ApiError(409, {"error": "workload_already_running", "active": active_workload(state)})
    if not kvm_available():
        raise ApiError(503, {"error": "kvm_unavailable"})
    if not shutil.which("qemu-system-x86_64") or not shutil.which("qemu-img"):
        raise ApiError(503, {"error": "qemu_unavailable"})
    if not shutil.which("websockify"):
        raise ApiError(503, {"error": "websockify_unavailable"})
    web_dir = novnc_web_dir()
    if not web_dir:
        raise ApiError(503, {"error": "novnc_unavailable", "expected": str(BASE_DIR / "novnc")})

    distro = body.get("distro") or "ubuntu"
    iso = find_iso(distro)
    if not iso:
        raise ApiError(404, {"error": "iso_not_found", "distro": distro, "available": list_distros()})

    owner = slug(body.get("owner_id"), "anonymous")
    did = iso_id(iso)
    vm_id = f"{owner}-{did}-{int(time.time())}"
    disk = DISK_DIR / f"{owner}-{did}.qcow2"

    profile = vm_profile(iso)
    ensure_vm_disk(disk, vm_disk_size(profile))
    tpm_pid = None
    ovmf_vars = None
    monitor_path = None
    memory_mb = DEFAULT_VM_MEMORY_MB
    if profile == "windows11":
        profile_args, tpm_pid, ovmf_vars, monitor_path = windows11_vm_args(vm_id, disk)
        net_device = "e1000e"
    elif profile in {"windows98", "windowsxp"}:
        profile_args = legacy_windows_vm_args(profile, disk)
        net_device = "rtl8139"
        if profile == "windows98":
            memory_mb = WIN98_MEMORY_MB
    else:
        profile_args = [
            "-machine", "q35,accel=kvm",
            "-cpu", "host",
            "-drive", f"file={disk},format=qcow2,if=virtio",
        ]
        net_device = "virtio-net-pci"

    qemu_cmd = [
        "qemu-system-x86_64",
        "-enable-kvm",
        "-m", str(memory_mb),
        "-smp", str(DEFAULT_VM_VCPUS),
        *profile_args,
        "-cdrom", str(iso),
        "-boot", "order=d",
        "-netdev", "user,id=net0",
        "-device", f"{net_device},netdev=net0",
        "-vnc", f"{VNC_HOST}:{VNC_DISPLAY}",
        "-display", "none",
        "-name", vm_id,
    ]
    qemu = run_logged(qemu_cmd, LOG_DIR / f"vm-{vm_id}.log")
    time.sleep(1)
    if not process_alive(qemu.pid):
        if tpm_pid:
            terminate_pid(tpm_pid, timeout=2)
        raise ApiError(500, {"error": "qemu_failed_to_start", "log": str(LOG_DIR / f"vm-{vm_id}.log")})
    if profile == "windows11" and monitor_path:
        trigger_windows11_dvd_boot(monitor_path)

    websockify_cmd = [
        "websockify",
        "--web", web_dir,
        f"{NOVNC_HOST}:{NOVNC_PORT}",
        f"{VNC_HOST}:{VNC_PORT}",
    ]
    ws = run_logged(websockify_cmd, LOG_DIR / f"websockify-{vm_id}.log")
    time.sleep(1)
    if not process_alive(ws.pid):
        terminate_pid(qemu.pid)
        if tpm_pid:
            terminate_pid(tpm_pid, timeout=2)
        raise ApiError(500, {"error": "websockify_failed_to_start", "log": str(LOG_DIR / f"websockify-{vm_id}.log")})

    vm = {
        "id": vm_id,
        "owner_id": owner,
        "distro_id": did,
        "name": distro_name(iso),
        "profile": profile,
        "memory_mb": memory_mb,
        "vcpus": DEFAULT_VM_VCPUS,
        "qemu_pid": qemu.pid,
        "websockify_pid": ws.pid,
        "tpm_pid": tpm_pid,
        "ovmf_vars": ovmf_vars,
        "monitor": monitor_path,
        "created_at": int(time.time()),
    }
    state["vm"] = vm
    save_state(state)
    return {"vm": response_vm(vm)}


def stop_vm(body):
    state = cleanup_state()
    vm = state.get("vm")
    if not vm:
        return {"stopped": False, "reason": "not_running"}
    requested_id = body.get("id")
    if requested_id and requested_id != vm.get("id"):
        raise ApiError(404, {"error": "vm_not_found"})
    terminate_pid(vm.get("qemu_pid"))
    terminate_pid(vm.get("websockify_pid"), timeout=5)
    terminate_pid(vm.get("tpm_pid"), timeout=2)
    state["vm"] = None
    save_state(state)
    return {"stopped": True, "id": vm.get("id")}


def mc_capacity(owner_id=None):
    state = cleanup_state()
    running = []
    if state.get("minecraft"):
        running.append(response_mc(state["minecraft"]))
    return {
        "capacity": {
            "default_memory_mb": 4096,
            "max_memory_mb": 4096,
            "default_vcpus": 4,
            "max_vcpus": 4,
            "port": MC_PORT,
            "host_reserve_memory_mb": HOST_RESERVE_MB,
        },
        "running": running,
        "owner_id": owner_id or "",
    }


def paper_latest_build(version):
    with urllib.request.urlopen("https://api.papermc.io/v2/projects/paper", timeout=20) as resp:
        project = json.load(resp)
    version = (version or "").strip()
    if not version or version == "latest":
        version = project["versions"][-1]
    elif version not in project.get("versions", []):
        raise ApiError(400, {"error": f"Versione Minecraft non valida: {version}"})
    with urllib.request.urlopen(f"https://api.papermc.io/v2/projects/paper/versions/{version}", timeout=20) as resp:
        meta = json.load(resp)
    return version, meta["builds"][-1]


def ensure_paper_jar(version):
    version, build = paper_latest_build(version)
    jar = MC_JAR_DIR / f"paper-{version}-{build}.jar"
    if jar.exists():
        return version, jar
    url = f"https://api.papermc.io/v2/projects/paper/versions/{version}/builds/{build}/downloads/paper-{version}-{build}.jar"
    tmp = jar.with_suffix(".tmp")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(jar)
    return version, jar


def write_server_properties(server_dir, body):
    props = {
        "server-port": str(MC_PORT),
        "server-ip": "0.0.0.0",
        "motd": body.get("name") or "Minecraft survival",
        "gamemode": body.get("gamemode") or "survival",
        "difficulty": body.get("difficulty") or "normal",
        "max-players": str(int(body.get("maxPlayers") or 8)),
        "pvp": "true" if body.get("pvp", True) else "false",
        "online-mode": "true" if body.get("onlineMode", True) else "false",
        "enable-command-block": "false",
    }
    with (server_dir / "server.properties").open("w", encoding="utf-8") as f:
        for key, value in props.items():
            f.write(f"{key}={value}\n")
    (server_dir / "eula.txt").write_text("eula=true\n", encoding="utf-8")


def response_mc(mc):
    address = f"{PUBLIC_MC_HOST}:{PUBLIC_MC_PORT}"
    return {
        "id": mc["id"],
        "owner_id": mc["owner_id"],
        "status": "running",
        "name": mc["name"],
        "version": mc["version"],
        "port": MC_PORT,
        "public_port": PUBLIC_MC_PORT,
        "address": address,
        "addresses": [address, f"{LAN_HOST}:{MC_PORT}", f"127.0.0.1:{MC_PORT}"],
        "memory_mb": DEFAULT_MC_MEMORY_MB,
        "vcpus": DEFAULT_MC_VCPUS,
    }


def start_minecraft(body):
    state = cleanup_state()
    if active_workload(state):
        raise ApiError(409, {"error": "workload_already_running", "active": active_workload(state)})
    if not shutil.which("java"):
        raise ApiError(503, {"error": "java_unavailable"})
    owner = slug(body.get("owner_id"), "anonymous")
    name = body.get("name") or "Minecraft survival"
    version, jar = ensure_paper_jar(body.get("version") or "")
    server_id = minecraft_server_id(owner)
    server_dir = minecraft_server_dir(owner)
    server_dir.mkdir(parents=True, exist_ok=True)
    write_server_properties(server_dir, body)
    cmd = [
        "java",
        f"-Xms1024M",
        f"-Xmx{DEFAULT_MC_MEMORY_MB}M",
        "-jar", str(jar),
        "nogui",
    ]
    log_path = MC_LOG_DIR / f"{server_id}.log"
    proc = run_logged(cmd, log_path, cwd=server_dir, stdin=subprocess.PIPE)
    time.sleep(2)
    if not process_alive(proc.pid):
        raise ApiError(500, {"error": "minecraft_failed_to_start", "log": str(log_path)})
    MC_PROCS[server_id] = proc
    mc = {
        "id": server_id,
        "owner_id": owner,
        "name": name,
        "version": version,
        "pid": proc.pid,
        "log": str(log_path),
        "created_at": int(time.time()),
    }
    state["minecraft"] = mc
    save_state(state)
    return {"server": response_mc(mc)}


def stop_minecraft(body):
    state = cleanup_state()
    mc = state.get("minecraft")
    if not mc:
        return {"stopped": False, "reason": "not_running"}
    requested_id = body.get("id")
    if requested_id and requested_id != mc.get("id"):
        raise ApiError(404, {"error": "minecraft_not_found"})
    proc = MC_PROCS.get(mc.get("id"))
    if proc and proc.stdin:
        try:
            proc.stdin.write(b"stop\n")
            proc.stdin.flush()
            time.sleep(5)
        except OSError:
            pass
    terminate_pid(mc.get("pid"))
    MC_PROCS.pop(mc.get("id"), None)
    state["minecraft"] = None
    save_state(state)
    return {"stopped": True, "id": mc.get("id")}


def send_minecraft_command(body):
    state = cleanup_state()
    mc = state.get("minecraft")
    if not mc:
        raise ApiError(409, {"error": "Server Minecraft non attivo."})
    owner = slug(body.get("owner_id"), "")
    if owner and owner != mc.get("owner_id"):
        raise ApiError(404, {"error": "minecraft_not_found"})
    command = (body.get("command") or "").strip()
    if not command:
        raise ApiError(400, {"error": "Comando mancante."})
    command = command.lstrip("/")
    proc = MC_PROCS.get(mc.get("id"))
    if not proc or not proc.stdin:
        raise ApiError(409, {"error": "Console non disponibile: riavvia il server Minecraft dal backend e riprova."})
    try:
        proc.stdin.write((command + "\n").encode("utf-8"))
        proc.stdin.flush()
    except OSError as exc:
        raise ApiError(409, {"error": f"Console non disponibile: {exc}. Riavvia il server Minecraft."})
    time.sleep(0.5)
    return {
        "ok": True,
        "command": command,
        "output": tail_text(Path(mc.get("log") or MC_LOG_DIR / f"{mc.get('id')}.log")),
    }


def parse_multipart(content_type, raw):
    if "multipart/form-data" not in (content_type or ""):
        raise ApiError(400, {"error": "multipart/form-data richiesto."})
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + raw
    )
    fields = {}
    files = {}
    for part in message.iter_parts():
        disposition = part.get("Content-Disposition", "")
        if "form-data" not in disposition:
            continue
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files[name] = {"filename": Path(filename).name, "content": payload}
        else:
            fields[name] = payload.decode("utf-8", errors="replace")
    return fields, files


def validate_upload_file(upload, allowed_suffixes):
    if not upload:
        raise ApiError(400, {"error": "File mancante."})
    filename = upload["filename"]
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed_suffixes:
        raise ApiError(400, {"error": f"Formato file non valido: {filename}"})
    content = upload["content"]
    if len(content) > MAX_UPLOAD_BYTES:
        raise ApiError(413, {"error": "File troppo grande."})
    return filename, suffix, content


def safe_zip_member_name(name):
    normalized = name.replace("\\", "/")
    path = Path(normalized)
    return bool(normalized and not path.is_absolute() and ".." not in path.parts)


def safe_extract_zip(zip_path, dest_dir):
    dest_dir = ensure_inside_base(dest_dir)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if not safe_zip_member_name(info.filename):
                raise ApiError(400, {"error": f"Archivio zip non sicuro: {info.filename}"})
            target = ensure_inside_base(dest_dir / info.filename)
            try:
                target.relative_to(dest_dir)
            except ValueError:
                raise ApiError(400, {"error": f"Archivio zip non sicuro: {info.filename}"})
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def extracted_world_root(temp_dir):
    entries = [p for p in temp_dir.iterdir() if p.name not in {".DS_Store", "__MACOSX"}]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return temp_dir


def upload_world(fields, files):
    state = cleanup_state()
    if state.get("minecraft"):
        raise ApiError(409, {"error": "Server Minecraft attivo: fermalo prima di caricare un mondo."})
    owner = slug(fields.get("owner_id"), "anonymous")
    filename, suffix, content = validate_upload_file(files.get("file"), {".zip", ".mcworld"})
    server_dir = minecraft_server_dir(owner)
    ensure_minecraft_base_files(server_dir)
    with tempfile.TemporaryDirectory(dir=BASE_DIR) as tmp_name:
        tmp_dir = ensure_inside_base(Path(tmp_name))
        archive = tmp_dir / filename
        archive.write_bytes(content)
        extract_dir = tmp_dir / "extract"
        extract_dir.mkdir()
        safe_extract_zip(archive, extract_dir)
        source = extracted_world_root(extract_dir)
        world_dir = ensure_inside_base(server_dir / "world")
        if world_dir.exists():
            shutil.rmtree(world_dir)
        shutil.copytree(source, world_dir)
    return {"ok": True, "message": "Mondo caricato. Avvia il server per usarlo."}


def install_jars(fields, files, target_name, mods_message=False):
    owner = slug(fields.get("owner_id"), "anonymous")
    filename, suffix, content = validate_upload_file(files.get("file"), {".zip", ".jar"})
    server_dir = minecraft_server_dir(owner)
    ensure_minecraft_base_files(server_dir)
    target_dir = ensure_inside_base(server_dir / target_name)
    target_dir.mkdir(parents=True, exist_ok=True)
    installed = 0
    if suffix == ".jar":
        (target_dir / filename).write_bytes(content)
        installed = 1
    else:
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as tmp_name:
            archive = ensure_inside_base(Path(tmp_name) / filename)
            archive.write_bytes(content)
            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(".jar"):
                        continue
                    if not safe_zip_member_name(info.filename):
                        raise ApiError(400, {"error": f"Archivio zip non sicuro: {info.filename}"})
                    jar_name = Path(info.filename).name
                    if not jar_name:
                        continue
                    with zf.open(info) as src, (target_dir / jar_name).open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    installed += 1
    payload = {"ok": True, "installed": installed}
    if mods_message:
        payload["message"] = "Mod caricate in mods/, ma il server attuale è Paper: servono Forge/Fabric per usarle."
    return payload


def health_report():
    code_path = ovmf_code_path()
    vars_template = ovmf_vars_path()
    web_dir = novnc_web_dir()
    binaries = {name: shutil.which(name) for name in
                ("qemu-system-x86_64", "qemu-img", "websockify", "swtpm", "java", "tailscale")}
    checks = {
        "kvm": kvm_available(),
        "qemu": bool(binaries["qemu-system-x86_64"] and binaries["qemu-img"]),
        "websockify": bool(binaries["websockify"]),
        "swtpm": bool(binaries["swtpm"]),
        "java": bool(binaries["java"]),
        "ovmf": bool(code_path and vars_template),
        "novnc": bool(web_dir),
        "isos": len(list_distros()),
    }
    # Windows 11 needs UEFI + an emulated TPM; everything else does not.
    checks["windows11_capable"] = checks["kvm"] and checks["ovmf"] and checks["swtpm"]
    return {
        "ok": all(checks[k] for k in ("kvm", "qemu", "websockify", "novnc")),
        "checks": checks,
        "paths": {
            "base_dir": str(BASE_DIR),
            "config_file": str(CONFIG_FILE) if CONFIG_FILE.exists() else None,
            "ovmf_code": str(code_path) if code_path else None,
            "ovmf_vars": str(vars_template) if vars_template else None,
            "novnc_web_dir": web_dir,
            "binaries": binaries,
        },
        "network": {
            "api": f"{API_HOST}:{API_PORT}",
            "lan_host": LAN_HOST,
            "novnc": f"{NOVNC_HOST}:{NOVNC_PORT}",
            "minecraft_local": f"{LAN_HOST}:{MC_PORT}",
            "minecraft_public": f"{PUBLIC_MC_HOST}:{PUBLIC_MC_PORT}",
        },
    }


class ApiError(Exception):
    def __init__(self, status, payload):
        super().__init__(payload)
        self.status = status
        self.payload = payload


class Handler(BaseHTTPRequestHandler):
    server_version = "QuickLLMSandbox/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def send_json(self, status, payload):
        raw = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(raw)

    def read_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > MAX_UPLOAD_BYTES:
            raise ApiError(413, {"error": "Richiesta troppo grande."})
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise ApiError(400, {"error": "invalid_json"})

    def read_raw_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > MAX_UPLOAD_BYTES:
            raise ApiError(413, {"error": "Richiesta troppo grande."})
        return self.rfile.read(length) if length else b""

    def do_OPTIONS(self):
        self.send_json(204, {})

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/health":
                return self.send_json(200, health_report())
            if path == "/api/rag/vms":
                return self.send_json(200, vm_status())
            if path == "/api/rag/minecraft":
                return self.send_json(200, mc_capacity())
            raise ApiError(404, {"error": "not_found"})
        except ApiError as exc:
            self.send_json(exc.status, exc.payload)
        except Exception as exc:
            self.send_json(500, {"error": "internal_error", "detail": str(exc)})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/rag/minecraft/upload-world":
                fields, files = parse_multipart(self.headers.get("Content-Type", ""), self.read_raw_body())
                return self.send_json(200, upload_world(fields, files))
            if path == "/api/rag/minecraft/upload-plugins":
                fields, files = parse_multipart(self.headers.get("Content-Type", ""), self.read_raw_body())
                return self.send_json(200, install_jars(fields, files, "plugins"))
            if path == "/api/rag/minecraft/upload-mods":
                fields, files = parse_multipart(self.headers.get("Content-Type", ""), self.read_raw_body())
                return self.send_json(200, install_jars(fields, files, "mods", mods_message=True))
            body = self.read_body()
            if path == "/api/rag/vms/start":
                return self.send_json(200, start_vm(body))
            if path == "/api/rag/vms/stop":
                return self.send_json(200, stop_vm(body))
            if path == "/api/rag/minecraft/start":
                return self.send_json(200, start_minecraft(body))
            if path == "/api/rag/minecraft/stop":
                return self.send_json(200, stop_minecraft(body))
            if path == "/api/rag/minecraft/command":
                return self.send_json(200, send_minecraft_command(body))
            raise ApiError(404, {"error": "not_found"})
        except ApiError as exc:
            self.send_json(exc.status, exc.payload)
        except Exception as exc:
            self.send_json(500, {"error": "internal_error", "detail": str(exc)})


def main():
    ensure_dirs()
    cleanup_state()
    report = health_report()
    missing = [name for name, ok in report["checks"].items() if ok is False]
    print(f"quickllm sandbox listening on {API_HOST}:{API_PORT}", flush=True)
    print(f"config: {report['paths']['config_file'] or '(nessuno, uso i default)'}", flush=True)
    print(f"minecraft public address: {PUBLIC_MC_HOST}:{PUBLIC_MC_PORT}", flush=True)
    if missing:
        print(f"ATTENZIONE, dipendenze mancanti: {', '.join(missing)}", flush=True)
    server = ThreadingHTTPServer((API_HOST, API_PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
