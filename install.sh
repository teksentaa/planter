#!/usr/bin/env bash
# Installs or updates the Seed Packet Planner sync server. Run from this folder:  sudo ./install.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ "$(id -u)" -ne 0 ]; then echo "Run with sudo: sudo ./install.sh"; exit 1; fi
command -v python3 >/dev/null || { echo "python3 is missing: sudo apt install python3"; exit 1; }

PORT_DEFAULT=8787
# HTTPS port on your Tailscale address. 8443 leaves anything already on the main address (such as Nextcloud) alone.
HTTPS_PORT=${HTTPS_PORT:-8443}

# Packet photo reading: Tesseract text recognition (+ Pillow to clean up photos). Skip with SKIP_TESSERACT=1.
if [ "${SKIP_TESSERACT:-0}" != "1" ] && ! command -v tesseract >/dev/null; then
  echo "Installing Tesseract for packet photo reading..."
  apt-get update -qq >/dev/null; apt-get install -y tesseract-ocr tesseract-ocr-eng python3-pil >/dev/null || echo "Could not install Tesseract; packet photo reading will be off."
fi

id seedplanner >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin seedplanner

mkdir -p /opt/seed-planner/public /var/lib/seed-planner
cp server.py seedsites.py packetreader.py /opt/seed-planner/
cp -r public/. /opt/seed-planner/public/
chown -R root:root /opt/seed-planner
chown seedplanner:seedplanner /var/lib/seed-planner
chmod 750 /var/lib/seed-planner

if [ ! -f /etc/seed-planner.env ]; then
  cat > /etc/seed-planner.env <<EOF
# Optional: paste an Anthropic API key so Claude reads packet photos on every device.
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-sonnet-5-5
PORT=$PORT_DEFAULT
EOF
  chmod 600 /etc/seed-planner.env
fi
if ! grep -q '^VISION_MODEL=' /etc/seed-planner.env; then
  cat >> /etc/seed-planner.env <<EOF
# Optional local vision model for reading packet photos (needs Ollama: https://ollama.com).
# Example: VISION_MODEL=qwen2.5vl:7b   Leave empty to use Tesseract only.
VISION_MODEL=
OLLAMA_URL=http://127.0.0.1:11434
EOF
fi
PORT=$(grep -E '^PORT=' /etc/seed-planner.env | cut -d= -f2)
PORT=${PORT:-$PORT_DEFAULT}

cp seed-planner.service /etc/systemd/system/seed-planner.service
systemctl daemon-reload
systemctl enable seed-planner >/dev/null
systemctl restart seed-planner
sleep 1
if ! curl -fsS "http://127.0.0.1:$PORT/api/state" >/dev/null; then
  echo "The server did not start. See: journalctl -u seed-planner -n 30"; exit 1
fi

if command -v tailscale >/dev/null; then
  tailscale serve --bg --https="$HTTPS_PORT" "$PORT"
  NAME=$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)
  echo
  echo "Seed Planner is running."
  [ -n "$NAME" ] && echo "Open on your phone and PC:  https://$NAME$([ "$HTTPS_PORT" = 443 ] || echo ":$HTTPS_PORT")" || echo "Run 'tailscale serve status' to see the address."
else
  echo "Server running on 127.0.0.1:$PORT, but Tailscale isn't installed, so other devices can't reach it yet."
fi
echo "Database: /var/lib/seed-planner/seeds.db"
VM=$(grep '^VISION_MODEL=' /etc/seed-planner.env | cut -d= -f2)
if command -v tesseract >/dev/null; then
  echo "Packet photo reading: Tesseract ready${VM:+, plus vision model $VM}"
else
  echo "Packet photo reading: off (Tesseract not installed)"
fi
