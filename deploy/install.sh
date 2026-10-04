#!/usr/bin/env bash
# Install on Ubuntu (tested target: Ubuntu Server 24.04 on Raspberry Pi).
# Run from the repository root as the user that should own the service:
#   ./deploy/install.sh
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(id -un)"
cd "$DIR"

echo "==> system packages"
APT="sudo apt-get -o DPkg::Lock::Timeout=900"  # wait for unattended-upgrades instead of failing
$APT update -qq
$APT install -y -qq python3-venv bluez
# Raspberry Pi needs pi-bluetooth to bring up the onboard BT chip; harmless to skip elsewhere
$APT install -y -qq pi-bluetooth 2>/dev/null || true
sudo systemctl enable --now bluetooth
# makes time-sync.target wait for an actual NTP sync (collector units order after it)
sudo systemctl enable systemd-time-wait-sync.service
sudo usermod -aG bluetooth "$USER_NAME"

echo "==> python venv"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .

if [ ! -f config.env ]; then
  cp config.env.example config.env
  echo "!! config.env created - set ARANET_ADDRESS (find it with: .venv/bin/aranet-collect --scan)"
fi

echo "==> systemd units"
for unit in aranet-collector.service aranet-collector.timer aranet-dashboard.service aranet-weather.service aranet-weather.timer aranet-dom-forecast.service aranet-dom-forecast.timer aranet-dom-climate.service aranet-dom-climate.timer aranet-forecast.service aranet-forecast.timer aranet-uv.service aranet-uv.timer aranet-air.service aranet-air.timer; do
  sed -e "s|__DIR__|$DIR|g" -e "s|__USER__|$USER_NAME|g" "deploy/$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now aranet-dashboard.service aranet-weather.timer aranet-dom-forecast.timer aranet-dom-climate.timer aranet-forecast.timer aranet-uv.timer aranet-air.timer
sudo systemctl restart aranet-dashboard.service  # pick up code updates on re-runs
if grep -q '^ARANET_ADDRESS=..' config.env; then
  sudo systemctl enable --now aranet-collector.timer
  echo "==> collector timer enabled"
else
  echo "!! collector timer NOT enabled yet: fill ARANET_ADDRESS, then: sudo systemctl enable --now aranet-collector.timer"
fi

PORT="$(grep -E '^ARANET_PORT=' config.env | cut -d= -f2 || true)"
echo "==> dashboard: http://$(hostname).local:${PORT:-8080}/weather/home"
