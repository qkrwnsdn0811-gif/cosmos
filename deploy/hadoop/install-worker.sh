#!/usr/bin/env bash
# Ubuntu 24.04 AWS Worker prerequisites only. No keys, login, NameNode format, or service startup.
set -Eeuo pipefail
script_dir="$(cd "$(dirname "$0")" && pwd)"
volume_id='' node_name='' management_cidr='' format_empty=false
while (($#)); do
    case "$1" in
        --volume-id) volume_id="${2:?missing volume ID}"; shift 2 ;;
        --node-name) node_name="${2:?missing node name}"; shift 2 ;;
        --management-cidr) management_cidr="${2:?missing management CIDR}"; shift 2 ;;
        --format-empty) format_empty=true; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
[[ "$EUID" -eq 0 ]] || { echo 'Run as root' >&2; exit 1; }
[[ "$volume_id" =~ ^vol-([0-9a-f]{8}|[0-9a-f]{17})$ ]] || { echo 'Expected EBS volume ID required' >&2; exit 2; }
[[ "$node_name" =~ ^cosmos-worker-[1-3]$ ]] || { echo 'Expected cosmos-worker-1, 2, or 3' >&2; exit 2; }
python3 - "$management_cidr" <<'PY'
import ipaddress, sys
network = ipaddress.ip_network(sys.argv[1], strict=True)
if network.version != 4 or network.prefixlen < 24:
    raise SystemExit('Use an explicit management IPv4 CIDR /24 or narrower')
PY
# shellcheck source=/dev/null
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 && "$(uname -m)" == x86_64 ]] || {
    echo 'This installer requires Ubuntu 24.04 x86_64' >&2; exit 1;
}
id ubuntu >/dev/null
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl python3 nvme-cli e2fsprogs util-linux ufw \
    openjdk-17-jdk-headless openjdk-21-jdk-headless

# Establish SSH access before activating the firewall. Hadoop ports remain closed.
ufw allow from "$management_cidr" to any port 22 proto tcp
ufw default deny incoming
ufw default allow outgoing
ufw allow 41641/udp comment 'Tailscale encrypted transport'
ufw --force enable

install -d -m 0755 /usr/share/keyrings
curl --fail --silent --show-error --retry 3 --connect-timeout 10 --max-time 120 \
    https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg \
    -o /usr/share/keyrings/tailscale-archive-keyring.gpg
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/ubuntu noble main' \
    > /etc/apt/sources.list.d/tailscale.list
apt-get update
apt-get install -y tailscale
systemctl enable --now tailscaled

volume_args=(--volume-id "$volume_id")
if "$format_empty"; then volume_args+=(--format-empty); fi
python3 "$script_dir/prepare-worker-volume.py" "${volume_args[@]}"
install -d -m 0750 -o ubuntu -g ubuntu /data/hadoop/datanode /data/yarn/local /data/yarn/logs
hostnamectl set-hostname "$node_name"

cache=/var/cache/cosmos-install
install -d -m 0755 "$cache"
staging=''
cleanup() {
    if [[ -n "$staging" && "$staging" == /opt/.cosmos-extract.* && -d "$staging" ]]; then
        rm -rf -- "$staging"
    fi
}
trap cleanup EXIT

install_apache() {
    local family="$1" version="$2" archive="$3" top_dir="$4" link="$5" base expected actual destination
    if [[ "$family" == hadoop ]]; then
        base="https://downloads.apache.org/hadoop/common/hadoop-$version"
    else
        base="https://downloads.apache.org/spark/spark-$version"
    fi
    curl --fail --silent --show-error --retry 3 --connect-timeout 10 --max-time 120 \
        "$base/$archive.sha512" -o "$cache/$archive.sha512"
    expected="$(python3 - "$cache/$archive.sha512" <<'PY'
import pathlib, re, sys
hashes = re.findall(r'(?<![0-9A-Fa-f])[0-9A-Fa-f]{128}(?![0-9A-Fa-f])', pathlib.Path(sys.argv[1]).read_text())
if len(hashes) != 1:
    raise SystemExit('Expected exactly one SHA512 digest in the Apache checksum')
print(hashes[0].lower())
PY
)"
    actual=''
    [[ ! -f "$cache/$archive" ]] || actual="$(sha512sum "$cache/$archive" | cut -d ' ' -f 1)"
    if [[ "$actual" != "$expected" ]]; then
        curl --fail --silent --show-error --retry 3 --connect-timeout 15 --max-time 2400 \
            "$base/$archive" -o "$cache/$archive.part"
        printf '%s  %s\n' "$expected" "$cache/$archive.part" | sha512sum --check --status
        mv "$cache/$archive.part" "$cache/$archive"
    fi
    destination="/opt/$top_dir"
    if [[ -e "$destination" ]]; then
        [[ -f "$destination/.cosmos-sha512" && "$(cat "$destination/.cosmos-sha512")" == "$expected" ]] || {
            echo "Existing $destination is not this verified installation; preserved for review" >&2; return 1;
        }
    else
        staging="$(mktemp -d /opt/.cosmos-extract.XXXXXX)"
        tar --extract --gzip --file "$cache/$archive" --directory "$staging" --no-same-owner
        [[ -d "$staging/$top_dir" ]] || { echo 'Unexpected Apache archive layout' >&2; return 1; }
        printf '%s\n' "$expected" > "$staging/$top_dir/.cosmos-sha512"
        mv "$staging/$top_dir" "$destination"
        cleanup
        staging=''
    fi
    if [[ -e "$link" || -L "$link" ]]; then
        [[ -L "$link" && "$(readlink -f "$link")" == "$destination" ]] || {
            echo "Existing $link is preserved; expected symlink to $destination" >&2; return 1;
        }
    else
        ln -s "$destination" "$link"
    fi
}
install_apache hadoop 3.5.0 hadoop-3.5.0.tar.gz hadoop-3.5.0 /opt/hadoop
install_apache spark 4.2.0 spark-4.2.0-bin-hadoop3.tgz spark-4.2.0-bin-hadoop3 /opt/spark

JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 /opt/hadoop/bin/hadoop version
JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 /opt/spark/bin/spark-submit --version
findmnt --mountpoint /data --output SOURCE,UUID,SIZE,AVAIL,TARGET
ufw status
echo 'Worker prerequisites installed. Join Tailscale and apply cluster configuration before starting Hadoop services.'
