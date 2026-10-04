#!/usr/bin/env bash
# Set up the dashboard on a public server that receives the database from the Pi.
# Run as root on the server:
#   deploy/server/setup.sh <domain> <https-port> "<pi public key>"
# Needs an existing Let's Encrypt certificate for <domain> (/etc/letsencrypt/live/<domain>/).
# Prints the generated dashboard password once (user "aranet").
set -euo pipefail

DOMAIN="$1"; PORT="$2"; PI_KEY="$3"
APP_PORT=8091
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA=/home/aranet/data          # Pi pushes aranet.db here (write-only for the Pi key)
WEATHER=/home/aranet/weather    # the server collects the weather feed itself

[ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ] || { echo "no certificate for $DOMAIN"; exit 1; }

echo "==> user 'aranet': receives the database, write-only rsync into $DATA"
id aranet >/dev/null 2>&1 || useradd --create-home --shell /bin/bash aranet
passwd -l aranet >/dev/null
install -d -o aranet -g aranet -m 755 "$DATA" "$WEATHER"
install -d -o aranet -g aranet -m 700 /home/aranet/.ssh
echo "restrict,command=\"/usr/bin/rrsync -wo $DATA\" $PI_KEY" > /home/aranet/.ssh/authorized_keys
chown aranet:aranet /home/aranet/.ssh/authorized_keys; chmod 600 /home/aranet/.ssh/authorized_keys

echo "==> app"
APT="apt-get -o DPkg::Lock::Timeout=900"
$APT install -y -qq python3-venv rsync nginx >/dev/null
# nginx must not take port 80: certbot renews with its standalone server there
rm -f /etc/nginx/sites-enabled/default
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q -e "$DIR"
cat > "$DIR/config.env" <<CFG
ARANET_DB=$DATA/aranet.db
ARANET_WEATHER_DB=$WEATHER/weather.db
ARANET_HOST=127.0.0.1
ARANET_PORT=$APP_PORT
CFG
for unit in aranet-dashboard.service aranet-weather.service aranet-weather.timer aranet-dom-forecast.service aranet-dom-forecast.timer aranet-dom-climate.service aranet-dom-climate.timer aranet-forecast.service aranet-forecast.timer aranet-uv.service aranet-uv.timer aranet-air.service aranet-air.timer; do
  sed -e "s|__DIR__|$DIR|g" -e "s|__USER__|aranet|g" "$DIR/deploy/$unit" > "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now aranet-dashboard.service aranet-weather.timer aranet-dom-forecast.timer aranet-dom-climate.timer aranet-forecast.timer aranet-uv.timer aranet-air.timer
systemctl restart aranet-dashboard.service

echo "==> nginx on https://$DOMAIN:$PORT"
if [ ! -s /etc/nginx/aranet.htpasswd ]; then
  PASS="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)"
  echo "aranet:$(openssl passwd -6 "$PASS")" > /etc/nginx/aranet.htpasswd
  echo "DASHBOARD_PASSWORD=$PASS"
fi
chown root:www-data /etc/nginx/aranet.htpasswd; chmod 640 /etc/nginx/aranet.htpasswd
sed -e "s|__DOMAIN__|$DOMAIN|g" -e "s|__PORT__|$PORT|g" -e "s|__APP_PORT__|$APP_PORT|g" \
  "$DIR/deploy/server/nginx-site.conf" > /etc/nginx/sites-available/aranet
ln -sf /etc/nginx/sites-available/aranet /etc/nginx/sites-enabled/aranet
nginx -t
systemctl enable nginx >/dev/null 2>&1
systemctl restart nginx

echo "==> reload nginx when certbot renews the certificate"
cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'HOOK'
#!/bin/sh
systemctl reload nginx
HOOK
chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
echo "done"
