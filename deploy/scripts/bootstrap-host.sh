#!/usr/bin/env bash
# Run once on the assigned Ubuntu EC2 as root. Does not issue a TLS certificate.
set -Eeuo pipefail
[[ "$EUID" -eq 0 ]] || { echo 'Run as root' >&2; exit 1; }
# shellcheck source=/dev/null
. /etc/os-release
[[ "$ID" = ubuntu ]] || { echo 'Ubuntu is required' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg git nginx certbot openssl openjdk-21-jre-headless python3 shellcheck
install -m 0755 -d /etc/apt/keyrings
if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
    curl --fail --silent --show-error https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod 0644 /etc/apt/keyrings/docker.asc
fi
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' "$(dpkg --print-architecture)" "$VERSION_CODENAME" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
install -d -m 0755 /opt/cosmos
id cosmos-ci >/dev/null 2>&1 || useradd --create-home --home-dir /opt/cosmos/jenkins-agent --shell /bin/bash cosmos-ci
usermod -aG docker cosmos-ci
install -d -m 0750 -o cosmos-ci -g cosmos-ci /opt/cosmos/jenkins-agent
install -d -m 0750 -o root -g cosmos-ci /etc/cosmos
install -d -m 0750 -o cosmos-ci -g cosmos-ci /opt/cosmos/releases /opt/cosmos/checkouts
install -d -m 0755 /var/www/cosmos-acme
if [[ ! -f /etc/cosmos/app.env ]]; then
    umask 077
    {
        printf 'PUBLIC_URL=https://j15c205.p.ssafy.io\n'
        printf 'DB_PASSWORD=%s\n' "$(openssl rand -hex 32)"
        printf 'REDIS_PASSWORD=%s\n' "$(openssl rand -hex 32)"
        printf 'JWT_SECRET=%s\n' "$(openssl rand -hex 48)"
    } > /etc/cosmos/app.env
    chown root:cosmos-ci /etc/cosmos/app.env
    chmod 0640 /etc/cosmos/app.env
fi
# Preserve SSH access and UFW's active state. Docker services publish to loopback only.
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
cat > /etc/nginx/sites-available/cosmos-acme <<'NGINX'
server {
    listen 80;
    server_name j15c205.p.ssafy.io;
    location ^~ /.well-known/acme-challenge/ { root /var/www/cosmos-acme; }
    location / { return 503; }
}
NGINX
if [[ ! -e /etc/nginx/sites-enabled/cosmos ]]; then
    # The default symlink belongs to the newly installed Nginx package.
    rm -f /etc/nginx/sites-enabled/default
    ln -sfn /etc/nginx/sites-available/cosmos-acme /etc/nginx/sites-enabled/cosmos-acme
fi
nginx -t
systemctl enable nginx
systemctl reload nginx
docker version --format '{{.Server.Version}}'
docker compose version
ufw status
