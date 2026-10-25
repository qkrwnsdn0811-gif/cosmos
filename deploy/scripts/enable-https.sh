#!/usr/bin/env bash
# Requires an existing certificate from certbot certonly --webroot.
set -Eeuo pipefail
[[ "$EUID" -eq 0 ]] || { echo 'Run as root' >&2; exit 1; }
cd "$(dirname "$0")/../.."
# Same COSMOS_DOMAIN as bootstrap-host.sh; the default keeps the SSAFY EC2 setup.
domain="${COSMOS_DOMAIN:-j15c205.p.ssafy.io}"
[[ "$domain" =~ ^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$ ]] || { echo 'Invalid COSMOS_DOMAIN' >&2; exit 2; }
test -r "/etc/letsencrypt/live/$domain/fullchain.pem"
test -r "/etc/letsencrypt/live/$domain/privkey.pem"
# cosmos.conf names the SSAFY host; that name is the slot replaced by this host's domain.
sed "s/j15c205\.p\.ssafy\.io/$domain/g" deploy/nginx/cosmos.conf > /etc/nginx/sites-available/cosmos
chmod 0644 /etc/nginx/sites-available/cosmos
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
