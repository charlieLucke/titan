# Titan Service – systemd-Einrichtung

Führt Titan als **systemd-User-Service** unter Linux aus (und unter Ubuntu in
WSL2). Kein Root nötig. Die Pfade unten verwenden `~` und funktionieren so für
jeden Nutzer.

## Voraussetzungen

- Ein laufendes **Qdrant** (gRPC `6334`) und **Ollama** (Phi-4) sowie eine
  NVIDIA-GPU mit CUDA-fähigem `torch` — siehe die Projekt-README.
- **Nur WSL2:** systemd in `/etc/wsl.conf` aktivieren:
  ```ini
  [boot]
  systemd=true
  ```
  Nach der Änderung: `wsl --shutdown` aus PowerShell, dann WSL neu starten. (Auf
  einem nativen Linux-System ist systemd bereits das Init-System — diesen Schritt
  überspringen.)

## Installation

```bash
# Log-Verzeichnis anlegen
mkdir -p ~/projects/titan/logs

# Service-Datei verlinken (oder kopieren)
mkdir -p ~/.config/systemd/user/
ln -sf ~/projects/titan/deploy/titan-service.service \
       ~/.config/systemd/user/titan-service.service

# .env befüllen (vor dem ersten Start erforderlich)
cp ~/projects/titan/.env.example ~/projects/titan/.env
# VAULT_ROOT, QDRANT_HOST, COLLECTION_NAME usw. setzen

# Daemon neu laden + Service aktivieren
systemctl --user daemon-reload
systemctl --user enable --now titan-service

# Status prüfen
systemctl --user status titan-service
curl http://localhost:8765/health
```

> Die mitgelieferte `titan-service.service` verwendet die absoluten Pfade des
> Autors (`/home/<dein-user>/projects/titan/...`). Passe darin
> `WorkingDirectory`, `ExecStart` und die Log-Pfade an deinen Benutzernamen/Ort
> an, bevor du die Unit aktivierst (systemd expandiert `~` innerhalb von
> Unit-Dateien nicht).

## Logs

```bash
tail -f ~/projects/titan/logs/service.log
tail -f ~/projects/titan/logs/service.err.log
journalctl --user -u titan-service -f
```

## Neustart / Stoppen

```bash
systemctl --user restart titan-service
systemctl --user stop titan-service
```
