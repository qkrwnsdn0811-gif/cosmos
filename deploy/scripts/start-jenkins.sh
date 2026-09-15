#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$EUID" -eq 0 ]] || { echo 'Run as root' >&2; exit 1; }
cd "$(dirname "$0")/../.."
install -d -m 0700 -o 1000 -g 1000 /etc/cosmos/jenkins
for name in admin-user admin-password webhook-token; do
    target="/etc/cosmos/jenkins/$name"
    if [[ ! -f "$target" ]]; then
        umask 077
        if [[ "$name" = admin-user ]]; then printf 'cosmos-admin\n' > "$target"; else openssl rand -hex 32 > "$target"; fi
        chown 1000:1000 "$target"
        chmod 0600 "$target"
    fi
done
install -d -m 0755 /opt/cosmos/jenkins
cp -a deploy/jenkins/. /opt/cosmos/jenkins/
docker compose -f /opt/cosmos/jenkins/compose.yml up -d --build --wait --wait-timeout 300
docker exec cosmos-jenkins cat /var/jenkins_home/cosmos-agent.secret > /etc/cosmos/agent.secret
chown root:cosmos-ci /etc/cosmos/agent.secret
chmod 0640 /etc/cosmos/agent.secret
curl --fail --silent --show-error http://127.0.0.1:18090/jenkins/jnlpJars/agent.jar -o /opt/cosmos/jenkins-agent/agent.jar
chown cosmos-ci:cosmos-ci /opt/cosmos/jenkins-agent/agent.jar
cat > /etc/systemd/system/cosmos-jenkins-agent.service <<'UNIT'
[Unit]
Description=COSMOS Jenkins WebSocket build agent
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
User=cosmos-ci
Group=cosmos-ci
SupplementaryGroups=docker
WorkingDirectory=/opt/cosmos/jenkins-agent
ExecStart=/usr/bin/java -Xmx256m -jar /opt/cosmos/jenkins-agent/agent.jar -url http://127.0.0.1:18090/jenkins/ -secret @/etc/cosmos/agent.secret -name cosmos-agent -webSocket -workDir /opt/cosmos/jenkins-agent
Restart=always
RestartSec=10
UMask=0027

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now cosmos-jenkins-agent
systemctl restart cosmos-jenkins-agent
echo 'Jenkins ready; add the project Deploy Token as credential cosmos-git-read in the UI'
