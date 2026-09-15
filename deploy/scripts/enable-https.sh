#!/usr/bin/env bash
# Requires an existing certificate from certbot certonly --webroot.
set -Eeuo pipefail
[[ "$EUID" -eq 0 ]] || { echo 'Run as root' >&2; exit 1; }
cd "$(dirname "$0")/../.."
test -r /etc/letsencrypt/live/j15c205.p.ssafy.io/fullchain.pem
test -r /etc/letsencrypt/live/j15c205.p.ssafy.io/privkey.pem
install -m 0644 deploy/nginx/cosmos.conf /etc/nginx/sites-available/cosmos
ln -sfn /etc/nginx/sites-available/cosmos /etc/nginx/sites-enabled/cosmos
rm -f /etc/nginx/sites-enabled/cosmos-acme
nginx -t
systemctl reload nginx
install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/cosmos-nginx.sh <<'HOOK'
#!/bin/sh
/usr/sbin/nginx -t && /bin/systemctl reload nginx
HOOK
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/cosmos-nginx.sh
systemctl enable --now certbot.timer
