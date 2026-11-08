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

## Wächter nach GPU-Reset

Nach einem Treiber-Reset (TDR) meldet `/health` `cuda_ok: false`, und nur ein Neustart
hilft. `titan-watchdog.timer` prüft alle zwei Minuten und startet titan neu, wenn das
Power-Limit noch höchstens 200 W beträgt und der letzte Neustart über 15 Minuten her ist.
Steht das Limit nach dem Reset wieder auf 285 W, startet er nicht und schreibt die
Anweisung ins Log: in Windows als Admin `nvidia-smi -pl 200`, dann neu starten.

```bash
ln -sf ~/projects/titan/deploy/titan-watchdog.service ~/.config/systemd/user/
ln -sf ~/projects/titan/deploy/titan-watchdog.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now titan-watchdog.timer
tail ~/projects/titan/logs/watchdog.log
```

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
