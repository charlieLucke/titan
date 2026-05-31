# Titan Service – systemd setup

Runs Titan as a **systemd user service** on Linux (and Ubuntu under WSL2). No root
needed. Paths below use `~`, so they work for any user.

## Prerequisites

- A running **Qdrant** (gRPC `6334`) and **Ollama** (Phi-4), and an NVIDIA GPU with a
  CUDA-enabled `torch` — see the project README.
- **WSL2 only:** enable systemd in `/etc/wsl.conf`:
  ```ini
  [boot]
  systemd=true
  ```
  After changing it: `wsl --shutdown` from PowerShell, then restart WSL. (On a native
  Linux box systemd is already the init system — skip this step.)

## Installation

```bash
# Create the log directory
mkdir -p ~/projects/titan/logs

# Symlink the service file (or copy it)
mkdir -p ~/.config/systemd/user/
ln -sf ~/projects/titan/deploy/titan-service.service \
       ~/.config/systemd/user/titan-service.service

# Fill in .env (required before the first start)
cp ~/projects/titan/.env.example ~/projects/titan/.env
# set VAULT_ROOT, QDRANT_HOST, COLLECTION_NAME etc.

# Reload the daemon + enable the service
systemctl --user daemon-reload
systemctl --user enable --now titan-service

# Check status
systemctl --user status titan-service
curl http://localhost:8765/health
```

> The shipped `titan-service.service` uses the author's absolute paths
> (`/home/<your-user>/projects/titan/...`). Edit `WorkingDirectory`, `ExecStart` and
> the log paths in it to match your username/location before enabling the unit
> (systemd does not expand `~` inside unit files).

## Logs

```bash
tail -f ~/projects/titan/logs/service.log
tail -f ~/projects/titan/logs/service.err.log
journalctl --user -u titan-service -f
```

## Restart / stop

```bash
systemctl --user restart titan-service
systemctl --user stop titan-service
```
