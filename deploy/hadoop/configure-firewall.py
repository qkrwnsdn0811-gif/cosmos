#!/usr/bin/env python3
"""Reconcile COSMOS host-only UFW rules; no SSH, service or tailnet ACL changes.

The installed firewall must already be active and explicitly allow management SSH.
Only this tool's validated, named TCP rules may be removed. New rules precede any
removal. Existing broad Hadoop allows cause a refusal, never automatic deletion.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

SPEC = importlib.util.spec_from_file_location("cosmos_cluster_config", Path(__file__).with_name("configure-cluster.py"))
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)
PORTS = config.PORTS
PREFIX = "cosmos-hadoop-v1:"
MODES = ("migration", "final", "account-transition")
DN_PORTS = {PORTS[k] for k in ("datanode_transfer", "datanode_ipc", "datanode_http")}
WORKER_PORTS = DN_PORTS | {PORTS[k] for k in ("nodemanager_localizer", "nodemanager_container", "nodemanager_http", "spark_blockmanager")}
MASTER_PORTS = {PORTS[k] for k in ("namenode_rpc", "namenode_http", "resourcemanager_scheduler", "resourcemanager_tracker", "resourcemanager_client", "resourcemanager_admin", "resourcemanager_http", "spark_driver", "spark_blockmanager", "spark_ui")}
PROTECTED_PORTS = set(PORTS.values())


def command(args: list[str]) -> str:
    result = subprocess.run(args, check=True, capture_output=True, text=True,
                            timeout=60, env={**os.environ, "LC_ALL": "C"})
    return result.stdout


def topology(raw: dict, node_name: str, mode: str) -> tuple[dict, dict, list[dict]]:
    inventory = config.validate_inventory(raw)
    if mode not in MODES:
        raise ValueError("Invalid firewall mode")
    all_nodes = [inventory["master"], *inventory["workers"], *inventory.get("retiring_workers", [])]
    node = next((n for n in all_nodes if n["name"] == node_name), None)
    if node is None:
        raise ValueError("Requested node is not in inventory")
    # Retiring Workers overlap with the replacement cluster only while data is
    # moving. Once final mode is applied, neither firewall rules nor Tailscale
    # health checks on active hosts should retain those old-account peers.
    nodes = ([inventory["master"], *inventory["workers"]]
             if mode == "final" else all_nodes)
    return inventory, node, nodes


def desired_rules(raw: dict, node_name: str, mode: str) -> list[dict]:
    inventory, node, nodes = topology(raw, node_name, mode)
    is_master = node == inventory["master"]
    ports = MASTER_PORTS | (DN_PORTS if mode == "migration" else set()) if is_master else WORKER_PORTS
    # Retiring hosts must remain reachable until decommission has finished.
    peers = [n for n in nodes if n != node]
    return [{"port": port, "peer": peer["tailscale_ip"],
             "comment": f"{PREFIX}{node_name}:tcp:{port}:{peer['tailscale_ip']}"}
            for peer in peers for port in sorted(ports)]


def allow_command(rule: dict) -> list[str]:
    return ["ufw", "allow", "in", "on", "tailscale0", "proto", "tcp", "from", rule["peer"],
            "to", "any", "port", str(rule["port"]), "comment", rule["comment"]]


def parse_status(output: str) -> list[dict]:
    if not re.search(r"^Status: active\s*$", output, re.M):
        raise ValueError("UFW must already be active; preserve explicit SSH access before running this tool")
    rules = []
    for line in output.splitlines():
        match = re.match(r"^\[\s*(\d+)\]\s+(.+?)\s{2,}(ALLOW|LIMIT|DENY|REJECT)\s+(IN|OUT|FWD)\s{2,}(.+?)\s*$", line)
        if not match:
            if line.lstrip().startswith("["):
                raise ValueError("Unrecognized UFW rule output; refusing unsafe reconciliation")
            continue
        source, _, comment = match[5].partition(" # ")
        rules.append({"index": int(match[1]), "target": match[2].strip(),
                      "action": f"{match[3]} {match[4]}", "source": source.strip(),
                      "comment": comment.strip()})
    return rules


def target_parts(rule: dict) -> tuple[str, str | None, bool]:
    target = rule["target"]
    v6 = "(v6)" in target or "(v6)" in rule["source"]
    target = target.replace(" (v6)", "")
    endpoint, separator, interface = target.partition(" on ")
    return endpoint, interface if separator else None, v6


def tcp_ports(endpoint: str, read=command) -> set[int]:
    if endpoint == "Anywhere":
        return set(range(1, 65536))
    match = re.fullmatch(r"([0-9,:]+)(?:/(tcp|udp))?", endpoint)
    if not match:
        # Resolve named UFW application profiles rather than guessing their ports.
        profile = read(["ufw", "app", "info", endpoint])
        found = re.search(r"^Ports?:\s*\n?\s*([0-9,:/a-z|]+)", profile, re.M)
        if not found:
            raise ValueError(f"Cannot prove allowed application profile is safe: {endpoint}")
        ports = set()
        for part in found[1].split("|"):
            ports.update(tcp_ports(part, read))
        return ports
    if match[2] == "udp":
        return set()
    ports = set()
    for item in match[1].split(","):
        limits = [int(n) for n in item.split(":")]
        if len(limits) > 2 or not all(1 <= n <= 65535 for n in limits):
            raise ValueError("Unrecognized UFW port specification")
        start, end = limits[0], limits[-1]
        if start > end:
            raise ValueError("Invalid UFW port range")
        ports.update(range(start, end + 1))
    return ports


def rule_matches(actual: dict, desired: dict) -> bool:
    endpoint, interface, v6 = target_parts(actual)
    return (actual["action"] == "ALLOW IN" and not v6 and interface == "tailscale0"
            and endpoint == f"{desired['port']}/tcp" and actual["source"] == desired["peer"])


def owned_rule(rule: dict, node_name: str) -> bool:
    prefix = f"{PREFIX}{node_name}:tcp:"
    if not rule["comment"].startswith(prefix):
        return False
    suffix = rule["comment"][len(prefix):]
    match = re.fullmatch(r"(\d+):(\d+\.\d+\.\d+\.\d+)", suffix)
    if not match:
        raise ValueError("Malformed managed comment; no rule was deleted")
    address = ipaddress.ip_address(match[2])
    desired = {"port": int(match[1]), "peer": str(address)}
    if (desired["port"] not in PROTECTED_PORTS or address not in config.TAILSCALE_RANGE
            or not rule_matches(rule, desired)):
        raise ValueError("Managed comment does not match its narrow rule; no rule was deleted")
    return True


def inspect_rules(rules: list[dict], desired: list[dict], node_name: str, read=command,
                  ssh_connection: str = "") -> None:
    ssh_allowed = False
    source_ip = ipaddress.ip_address(ssh_connection.split()[0]) if ssh_connection else None
    for rule in rules:
        is_owned = owned_rule(rule, node_name)
        if rule["action"] not in ("ALLOW IN", "LIMIT IN"):
            continue
        endpoint, interface, v6 = target_parts(rule)
        ports = tcp_ports(endpoint, read)
        if 22 in ports and not v6 and interface != "tailscale0":
            source = rule["source"]
            allowed_source = source in ("Anywhere", "0.0.0.0/0")
            if not allowed_source:
                try:
                    network = ipaddress.ip_network(source, strict=False)
                    allowed_source = (source_ip in network) if source_ip else network.version == 4
                except ValueError:
                    pass
            ssh_allowed |= allowed_source
        if not (ports & PROTECTED_PORTS) or interface == "lo":
            continue
        if is_owned:
            continue
        if not any(rule_matches(rule, expected) for expected in desired):
            raise ValueError(f"Existing rule [{rule['index']}] permits cluster ports outside an exact peer/interface rule; review it manually")
    if not ssh_allowed:
        raise ValueError("An explicit IPv4 management SSH allow/limit rule is required (matching SSH_CONNECTION when available)")


def transport_allowed(rules: list[dict]) -> bool:
    return any(target_parts(r) == ("41641/udp", None, False)
               and r["action"] == "ALLOW IN" and r["source"] in ("Anywhere", "0.0.0.0/0") for r in rules)


def reconcile(desired: list[dict], node_name: str, run=command, ssh_connection: str = "") -> list[dict]:
    before = parse_status(run(["ufw", "status", "numbered"]))
    inspect_rules(before, desired, node_name, run, ssh_connection)
    for rule in desired:
        if not any(rule_matches(actual, rule) for actual in before):
            run(allow_command(rule))
    if not transport_allowed(before):
        run(["ufw", "allow", "41641/udp", "comment", PREFIX + "transport"])
    # Delete by freshly resolved number only after validating the exact owned rule.
    # A separate operator must not edit UFW concurrently; the CLI has no transaction API.
    for _ in range(1000):
        current = parse_status(run(["ufw", "status", "numbered"]))
        inspect_rules(current, desired, node_name, run, ssh_connection)
        stale = [r for r in current if owned_rule(r, node_name)
                 and not any(rule_matches(r, expected) for expected in desired)]
        if not stale:
            break
        selected = max(stale, key=lambda r: r["index"])
        confirmed = parse_status(run(["ufw", "status", "numbered"]))
        if confirmed != current:
            raise ValueError("UFW rules changed concurrently; stopped without deleting the selected rule")
        run(["ufw", "--force", "delete", str(selected["index"])])
    else:
        raise ValueError("Too many stale rules; review manually")
    current = parse_status(run(["ufw", "status", "numbered"]))
    inspect_rules(current, desired, node_name, run, ssh_connection)
    if not transport_allowed(current) or any(not any(rule_matches(r, wanted) for r in current) for wanted in desired):
        raise ValueError("Required peer/transport rule is missing; Tailscale settings were not changed")
    run(["ufw", "default", "deny", "incoming"])
    # Existing outgoing/default route policy is preserved. Installation normally
    # supplies allow outgoing; it is checked before switching Tailscale below.
    return current


def validate_host(node: dict, run=command) -> dict:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError("--apply/--verify must run as root on the target Linux host")
    for executable in ("ufw", "tailscale", "ip", "ss"):
        if not shutil.which(executable):
            raise ValueError(f"Missing required command: {executable}")
    links = json.loads(run(["ip", "-j", "address", "show", "dev", "tailscale0"]))
    addresses = {a["local"] for link in links for a in link.get("addr_info", [])}
    if node["tailscale_ip"] not in addresses:
        raise ValueError("Inventory IP is not assigned to this host's tailscale0")
    help_result = subprocess.run(["tailscale", "set", "--help"], check=True, capture_output=True,
                                 text=True, timeout=60, env={**os.environ, "LC_ALL": "C"})
    if "--netfilter-mode" not in help_result.stdout + help_result.stderr:
        raise ValueError("Installed Tailscale lacks set --netfilter-mode support")
    # Keep private daemon preferences in memory and never write/print this object.
    prefs = json.loads(run(["tailscale", "debug", "prefs"]))
    if prefs.get("AdvertiseRoutes") or prefs.get("ExitNodeID") or prefs.get("ExitNodeIP"):
        raise ValueError("This firewall is for host-only Tailscale; a subnet/exit-node needs a separate routing policy")
    return prefs


def listener_report(output: str, node: dict, is_master: bool) -> list[dict]:
    listeners = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        endpoint = fields[3]
        host, separator, port = endpoint.rpartition(":")
        if not separator or not port.isdigit() or int(port) not in PROTECTED_PORTS:
            continue
        host = host.strip("[]")
        if host == node["tailscale_ip"] or host in ("127.0.0.1", "::1"):
            classification = "tailscale" if host == node["tailscale_ip"] else "loopback"
        elif is_master and int(port) == PORTS["namenode_rpc"] and host in ("*", "0.0.0.0", "::"):
            classification = "loopback-compatible RPC; peer firewall required"
        else:
            raise ValueError(f"Cluster listener {endpoint} is not bound to this node's Tailscale/loopback IP")
        listeners.append({"address": host, "port": int(port), "binding": classification})
    return listeners


def switch_netfilter(peers: list[str], run=command) -> None:
    # Rules and management access have been verified before this function.
    try:
        run(["tailscale", "set", "--netfilter-mode=nodivert"])
        prefs = json.loads(run(["tailscale", "debug", "prefs"]))
        if prefs.get("NetfilterMode") not in (1, "nodivert"):
            raise ValueError("Tailscale did not retain nodivert")
        for peer in peers:
            run(["tailscale", "ping", "--c=1", "--timeout=5s", "--until-direct=false", peer])
    except Exception as exc:
        try:
            run(["tailscale", "set", "--netfilter-mode=on"])
        except Exception as rollback:
            raise RuntimeError("Tailscale switch failed and restoring netfilter=on also failed; use existing management SSH") from rollback
        raise RuntimeError("Tailscale switch/ping failed; restored netfilter=on. UFW peer isolation is not yet effective") from exc


def verify_policy(desired: list[dict], node_name: str, run=command, ssh_connection: str = "") -> list[dict]:
    current = parse_status(run(["ufw", "status", "numbered"]))
    inspect_rules(current, desired, node_name, run, ssh_connection)
    if any(not any(rule_matches(r, wanted) for r in current) for wanted in desired) or not transport_allowed(current):
        raise ValueError("Missing required UFW peer/transport rules")
    verbose = run(["ufw", "status", "verbose"])
    if not re.search(r"Default: deny \(incoming\), allow \(outgoing\)", verbose):
        raise ValueError("Expected UFW default deny incoming and allow outgoing")
    return current


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--mode", choices=MODES, default="migration")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true", help="Print intended rules only; no host commands")
    action.add_argument("--apply", action="store_true", help="Apply on this host, then switch Tailscale to nodivert")
    action.add_argument("--verify", action="store_true", help="Read-only host/rule/listener verification")
    args = parser.parse_args()
    try:
        raw = json.loads(args.inventory.read_text(encoding="utf-8-sig"))
        inventory, node, nodes = topology(raw, args.node, args.mode)
        desired = desired_rules(inventory, args.node, args.mode)
        if args.plan:
            print(json.dumps({"node": args.node, "mode": args.mode,
                              "peer_rules": [allow_command(r) for r in desired],
                              "transport": ["ufw", "allow", "41641/udp"],
                              "tailscale_netfilter": "nodivert",
                              "requires": ["active UFW", "existing management SSH rule", "assigned tailscale0 IPv4", "host-only routing"],
                              "note": "Only validated stale cosmos-hadoop-v1 rules are deleted; no actual commands executed"}, indent=2))
            return 0
        prefs = validate_host(node)
        ssh_connection = os.environ.get("SSH_CONNECTION", "")
        before = command(["ufw", "status", "numbered"])
        inspect_rules(parse_status(before), desired, args.node, command, ssh_connection)
        listeners = listener_report(command(["ss", "-H", "-ltn"]), node, node == inventory["master"])
        backup = None
        if args.apply:
            # Advisory lock prevents two copies of this script from reconciling together.
            import fcntl
            with open("/run/lock/cosmos-hadoop-firewall.lock", "w", encoding="utf-8") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
                backup = Path("/var/backups/cosmos-hadoop-firewall") / f"{stamp}-{args.node}"
                backup.mkdir(parents=True, mode=0o700)
                os.chmod(backup, 0o700)
                (backup / "ufw-status-before.txt").write_text(before, encoding="utf-8")
                (backup / "ufw-added-before.txt").write_text(command(["ufw", "show", "added"]), encoding="utf-8")
                (backup / "netfilter-mode-before.json").write_text(json.dumps({"NetfilterMode": prefs.get("NetfilterMode")}) + "\n", encoding="utf-8")
                reconcile(desired, args.node, command, ssh_connection)
                verify_policy(desired, args.node, command, ssh_connection)
                switch_netfilter([n["tailscale_ip"] for n in nodes if n != node])
        else:
            verify_policy(desired, args.node, command, ssh_connection)
            if prefs.get("NetfilterMode") not in (1, "nodivert"):
                raise ValueError("Tailscale netfilter must be nodivert for UFW peer restrictions to take effect")
        print(json.dumps({"node": args.node, "mode": args.mode, "verified_peer_rules": len(desired),
                          "tailscale_netfilter": "nodivert", "listeners": listeners,
                          "backup_directory": str(backup) if backup else None,
                          "note": "tailscale ping checks overlay health, not TCP policy. Validate actual peer/public TCP reachability before starting jobs."}, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"Firewall refused/failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
