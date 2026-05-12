# Titan Service – systemd Setup

## Voraussetzungen

WSL2 muss systemd aktiviert haben (`/etc/wsl.conf`):

```ini
[boot]
systemd=true
```

Nach Änderung: `wsl --shutdown` aus PowerShell, dann WSL neu starten.

## Installation

```bash
# Log-Verzeichnis anlegen
mkdir -p ~/projects/titan/logs

# Service-Datei symlinken (oder kopieren)
mkdir -p ~/.config/systemd/user/
ln -sf ~/projects/titan/deploy/titan-service.service \
       ~/.config/systemd/user/titan-service.service

# .env befüllen (Pflicht vor erstem Start)
cp ~/projects/titan/.env.example ~/projects/titan/.env
# VAULT_ROOT, QDRANT_HOST, COLLECTION_NAME etc. setzen

# Daemon neu laden + Service aktivieren
systemctl --user daemon-reload
systemctl --user enable --now titan-service

# Status prüfen
systemctl --user status titan-service
curl http://localhost:8765/health
```

## Logs

```bash
tail -f ~/projects/titan/logs/service.log
tail -f ~/projects/titan/logs/service.err.log
journalctl --user -u titan-service -f
```

## Neustart / Stopp

```bash
systemctl --user restart titan-service
systemctl --user stop titan-service
```
