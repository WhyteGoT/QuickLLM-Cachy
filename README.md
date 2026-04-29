# QuickLLM Sandbox Worker

Standalone CachyOS worker for QuickLLM VM and Minecraft workloads.

Place bootable ISO files in `isos/`. The API lists available distros dynamically from those files.

Service endpoints:

- `GET /api/rag/vms`
- `POST /api/rag/vms/start`
- `POST /api/rag/vms/stop`
- `GET /api/rag/minecraft`
- `POST /api/rag/minecraft/start`
- `POST /api/rag/minecraft/stop`
- `POST /api/rag/minecraft/command`
- `POST /api/rag/minecraft/upload-world`
- `POST /api/rag/minecraft/upload-plugins`
- `POST /api/rag/minecraft/upload-mods`

Minecraft public address: `cachyos.tail9776fa.ts.net:10000`.

## Minecraft examples

Status:

```bash
curl http://127.0.0.1:8765/api/rag/minecraft
```

Start PaperMC. If `version` is empty or `latest`, the backend uses the latest PaperMC version. If a non-empty version is specified, it must exist in the PaperMC API or the backend returns HTTP 400.

```bash
curl -X POST http://127.0.0.1:8765/api/rag/minecraft/start \
  -H 'Content-Type: application/json' \
  -d '{"owner_id":"test","name":"Test server","version":"latest"}'
```

Run a console command on the active Minecraft server. Send commands without the leading slash.

```bash
curl -X POST http://127.0.0.1:8765/api/rag/minecraft/command \
  -H 'Content-Type: application/json' \
  -d '{"owner_id":"test","command":"say test da console"}'
```

Upload a world archive (`.zip` or `.mcworld`). Stop the server before replacing `world/`.

```bash
curl -X POST http://127.0.0.1:8765/api/rag/minecraft/upload-world \
  -F owner_id=test \
  -F file=@world.zip
```

Upload Paper/Bukkit/Spigot plugins. A `.jar` is copied directly; a `.zip` installs only contained `.jar` files.

```bash
curl -X POST http://127.0.0.1:8765/api/rag/minecraft/upload-plugins \
  -F owner_id=test \
  -F file=@plugin.jar
```

Upload mods into `mods/`. The current server runtime is Paper, so Forge/Fabric mods are stored but require a Forge/Fabric server to be loaded.

```bash
curl -X POST http://127.0.0.1:8765/api/rag/minecraft/upload-mods \
  -F owner_id=test \
  -F file=@mods.zip
```
