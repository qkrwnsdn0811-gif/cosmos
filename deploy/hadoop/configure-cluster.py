#!/usr/bin/env python3
"""Render or install COSMOS Hadoop/YARN configuration without controlling daemons.

No format, block movement, setrep, refreshNodes, firewall or service command is run.
The master NameNode and DataNode storage paths are immutable in this tool.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from string import Template
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET

HADOOP_CONF = "/opt/hadoop/etc/hadoop"
SPARK_CONF = "/opt/spark/conf"
NAMENODE_DIR = "/data/hadoop/namenode"
DATANODE_DIR = "/data/hadoop/datanode"
MANAGED_BEGIN = "# BEGIN COSMOS MANAGED CONFIG"
MANAGED_END = "# END COSMOS MANAGED CONFIG"
TAILSCALE_RANGE = ipaddress.ip_network("100.64.0.0/10")
MANAGED_RUNTIME_PARENTS = (
    "/data/hadoop",
    "/data/yarn",
    "/data/spark",
)
MANAGED_RUNTIME_DIRECTORIES = (
    "/data/hadoop/tmp",
    DATANODE_DIR,
    "/data/yarn/local",
    "/data/yarn/logs",
    "/data/spark/local",
    "/var/log/cosmos-hadoop",
    "/var/log/cosmos-spark",
)
PORTS = {
    "namenode_rpc": 9000,
    "namenode_http": 19870,
    "secondarynamenode_http": 19868,
    "datanode_transfer": 19866,
    "datanode_ipc": 19867,
    "datanode_http": 19864,
    "resourcemanager_scheduler": 18030,
    "resourcemanager_tracker": 18031,
    "resourcemanager_client": 18032,
    "resourcemanager_admin": 18033,
    "resourcemanager_http": 18088,
    "nodemanager_localizer": 18040,
    "nodemanager_container": 18041,
    "nodemanager_http": 18042,
    "spark_driver": 18100,
    "spark_blockmanager": 18101,
    "spark_ui": 18104,
}


def validate_inventory(raw: dict) -> dict:
    inventory = dict(raw)
    inventory.setdefault("cluster_name", "cosmos")
    inventory.setdefault("user", "ubuntu")
    inventory.setdefault("java17_home", "/usr/lib/jvm/java-17-openjdk-amd64")
    inventory.setdefault("java21_home", "/usr/lib/jvm/java-21-openjdk-amd64")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", inventory["cluster_name"]):
        raise ValueError("cluster_name must be a lowercase DNS label")
    if inventory["user"] != "ubuntu":
        raise ValueError("This deployment is configured for the existing ubuntu service user")
    for key in ("java17_home", "java21_home"):
        value = inventory[key]
        if not isinstance(value, str) or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value):
            raise ValueError(f"{key} must be an absolute path without shell metacharacters")
        if ".." in Path(value).parts:
            raise ValueError(f"{key} must not contain parent-directory components")
    if not isinstance(inventory.get("master"), dict):
        raise ValueError("master object is required")
    if not isinstance(inventory.get("workers"), list) or len(inventory["workers"]) != 3:
        raise ValueError("Exactly three workers are required")
    inventory.setdefault("retiring_workers", [])
    if not isinstance(inventory["retiring_workers"], list) or len(inventory["retiring_workers"]) not in (0, 3):
        raise ValueError("retiring_workers must be empty or contain exactly three old-account workers")
    names, addresses = set(), set()
    for node in [inventory["master"], *inventory["workers"], *inventory["retiring_workers"]]:
        name = node.get("name", "")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name):
            raise ValueError("Each node name must be a lowercase DNS label")
        if not isinstance(node.get("tailscale_ip"), str):
            raise ValueError(f"{name}: tailscale_ip must be a dotted IPv4 string")
        address = ipaddress.ip_address(node["tailscale_ip"])
        if address.version != 4 or address not in TAILSCALE_RANGE:
            raise ValueError(f"{name}: an assigned Tailscale IPv4 address is required")
        if name in names or str(address) in addresses:
            raise ValueError("Node names and Tailscale addresses must be unique")
        names.add(name)
        addresses.add(str(address))
    return inventory


def xml_properties(text: str) -> dict[str, str]:
    if not text.strip():
        return {}
    root = ET.fromstring(text)
    if root.tag != "configuration":
        raise ValueError("Expected a Hadoop <configuration> document")
    return {p.findtext("name", ""): p.findtext("value", "") for p in root.findall("property")}


def merge_xml(existing: str, updates: dict[str, str]) -> str:
    root = ET.fromstring(existing) if existing.strip() else ET.Element("configuration")
    if root.tag != "configuration":
        raise ValueError("Expected a Hadoop <configuration> document")
    for prop in list(root.findall("property")):
        if prop.findtext("name") in updates:
            root.remove(prop)
    for name, value in sorted(updates.items()):
        prop = ET.SubElement(root, "property")
        ET.SubElement(prop, "name").text = name
        ET.SubElement(prop, "value").text = str(value)
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def merge_block(existing: str, block: str) -> str:
    if existing.count(MANAGED_BEGIN) != existing.count(MANAGED_END):
        raise ValueError("Incomplete managed block in existing configuration")
    if existing.count(MANAGED_BEGIN) > 1:
        raise ValueError("Multiple managed blocks in existing configuration")
    pattern = re.escape(MANAGED_BEGIN) + r".*?" + re.escape(MANAGED_END)
    preserved = re.sub(pattern, "", existing, flags=re.S).rstrip()
    return (preserved + "\n\n" if preserved else "") + MANAGED_BEGIN + "\n" + block.rstrip() + "\n" + MANAGED_END + "\n"


def merge_spark_properties(existing: str, updates: dict[str, str]) -> str:
    kept = []
    for line in existing.splitlines():
        clean = line.strip()
        key = re.split(r"[\s=]+", clean, maxsplit=1)[0] if clean else ""
        if key not in updates and clean not in (MANAGED_BEGIN, MANAGED_END):
            kept.append(line)
    body = "\n".join(f"{key} {value}" for key, value in sorted(updates.items()))
    return merge_block("\n".join(kept), body)


def merge_hosts(existing: str, nodes: list[dict]) -> str:
    if existing.count(MANAGED_BEGIN) != existing.count(MANAGED_END):
        raise ValueError("Incomplete COSMOS block in /etc/hosts")
    stripped = re.sub(re.escape(MANAGED_BEGIN) + r".*?" + re.escape(MANAGED_END), "", existing, flags=re.S)
    names = {node["name"] for node in nodes}
    lines = []
    for line in stripped.splitlines():
        record, separator, comment = line.partition("#")
        parts = record.split()
        if len(parts) > 1 and any(name in names for name in parts[1:]):
            aliases = [name for name in parts[1:] if name not in names]
            if aliases:
                lines.append("\t".join([parts[0], *aliases]) + (" #" + comment if separator else ""))
            elif separator:
                lines.append("#" + comment)
        else:
            lines.append(line)
    body = "\n".join(f"{n['tailscale_ip']}\t{n['name']}" for n in nodes)
    return merge_block("\n".join(lines), body)


def storage_path(value: str) -> str:
    if "," in value:
        raise ValueError("Multiple existing storage directories require a reviewed migration")
    parsed = urlparse(value)
    if parsed.scheme and parsed.scheme != "file":
        raise ValueError("Unexpected storage directory URI")
    if parsed.netloc not in ("", "localhost"):
        raise ValueError("Unexpected storage directory host")
    return unquote(parsed.path).rstrip("/")


def validate_existing_storage(existing_hdfs: str, is_master: bool) -> None:
    props = xml_properties(existing_hdfs)
    checks = {"dfs.datanode.data.dir": DATANODE_DIR}
    if is_master:
        checks["dfs.namenode.name.dir"] = NAMENODE_DIR
        # An explicit edits path must also remain at the existing NameNode store.
        checks["dfs.namenode.edits.dir"] = NAMENODE_DIR
    for key, expected in checks.items():
        value = props.get(key, "")
        if key == "dfs.namenode.edits.dir" and value == "${dfs.namenode.name.dir}":
            value = props.get("dfs.namenode.name.dir", NAMENODE_DIR)
        if value and storage_path(value) != expected:
            raise ValueError(f"Refusing to change existing {key}; storage migration is outside this tool")


def template(name: str, **values: str) -> str:
    source = Path(__file__).resolve().parent / "templates" / name
    return Template(source.read_text(encoding="utf-8")).substitute(values)


def render_configuration(inventory: dict, node_name: str, mode: str, existing: dict[str, str] | None = None) -> tuple[dict[str, str], dict]:
    inventory = validate_inventory(inventory)
    if mode not in ("migration", "account-transition", "final"):
        raise ValueError("mode must be migration, account-transition or final")
    retiring_workers = inventory["retiring_workers"]
    if mode == "migration" and retiring_workers:
        raise ValueError("migration mode is only for the initial Master plus three active Workers")
    if mode == "account-transition" and len(retiring_workers) != 3:
        raise ValueError("account-transition requires exactly three retiring_workers")
    nodes = [inventory["master"], *inventory["workers"], *retiring_workers]
    node = next((item for item in nodes if item["name"] == node_name), None)
    if node is None:
        raise ValueError("Requested node is not in the inventory")
    existing = existing or {}
    is_master = node == inventory["master"]
    master_ip, node_ip = inventory["master"]["tailscale_ip"], node["tailscale_ip"]
    active_worker_ips = [item["tailscale_ip"] for item in inventory["workers"]]
    retiring_worker_ips = [item["tailscale_ip"] for item in retiring_workers]
    all_worker_ips = active_worker_ips + retiring_worker_ips
    p = PORTS
    replication_tuning = {
        "migration": ("256", "512", "256"),
        "account-transition": ("64", "128", "64"),
        "final": ("8", "16", "8"),
    }[mode]
    files: dict[str, str] = {}
    validate_existing_storage(existing.get(f"{HADOOP_CONF}/hdfs-site.xml", ""), is_master)

    core = {
        "fs.defaultFS": f"hdfs://{master_ip}:{p['namenode_rpc']}",
        "hadoop.tmp.dir": "/data/hadoop/tmp",
        "hadoop.security.authentication": "simple",
    }
    hdfs = {
        "dfs.namenode.name.dir": f"file://{NAMENODE_DIR}",
        "dfs.datanode.data.dir": f"file://{DATANODE_DIR}",
        "dfs.namenode.rpc-address": f"{master_ip}:{p['namenode_rpc']}",
        "dfs.namenode.rpc-bind-host": "0.0.0.0",
        "dfs.namenode.http-address": f"{master_ip}:{p['namenode_http']}",
        "dfs.namenode.http-bind-host": master_ip,
        "dfs.namenode.secondary.http-address": f"{master_ip}:{p['secondarynamenode_http']}",
        "dfs.datanode.address": f"{node_ip}:{p['datanode_transfer']}",
        "dfs.datanode.ipc.address": f"{node_ip}:{p['datanode_ipc']}",
        "dfs.datanode.http.address": f"{node_ip}:{p['datanode_http']}",
        "dfs.datanode.hostname": node_ip,
        "dfs.client.use.datanode.hostname": "true",
        "dfs.datanode.use.datanode.hostname": "true",
        "dfs.namenode.datanode.registration.ip-hostname-check": "false",
        "dfs.hosts": f"{HADOOP_CONF}/dfs.hosts",
        "dfs.hosts.exclude": f"{HADOOP_CONF}/dfs.hosts.exclude",
        "dfs.replication": "2",
        "dfs.namenode.replication.max-streams": replication_tuning[0],
        "dfs.namenode.replication.max-streams-hard-limit": replication_tuning[1],
        "dfs.namenode.replication.work.multiplier.per.iteration": replication_tuning[2],
        "dfs.permissions.enabled": "true",
        "dfs.namenode.safemode.min.datanodes": "1",
    }
    yarn = {
        "yarn.resourcemanager.hostname": master_ip,
        "yarn.resourcemanager.bind-host": master_ip,
        "yarn.resourcemanager.address": f"{master_ip}:{p['resourcemanager_client']}",
        "yarn.resourcemanager.scheduler.address": f"{master_ip}:{p['resourcemanager_scheduler']}",
        "yarn.resourcemanager.resource-tracker.address": f"{master_ip}:{p['resourcemanager_tracker']}",
        "yarn.resourcemanager.admin.address": f"{master_ip}:{p['resourcemanager_admin']}",
        "yarn.resourcemanager.webapp.address": f"{master_ip}:{p['resourcemanager_http']}",
        "yarn.resourcemanager.nodes.include-path": f"{HADOOP_CONF}/yarn.hosts",
        "yarn.resourcemanager.nodes.exclude-path": f"{HADOOP_CONF}/yarn.hosts.exclude",
        "yarn.nodemanager.hostname": node_ip,
        "yarn.nodemanager.bind-host": node_ip,
        "yarn.nodemanager.address": f"{node_ip}:{p['nodemanager_container']}",
        "yarn.nodemanager.localizer.address": f"{node_ip}:{p['nodemanager_localizer']}",
        "yarn.nodemanager.webapp.address": f"{node_ip}:{p['nodemanager_http']}",
        "yarn.nodemanager.resource.memory-mb": "5120",
        "yarn.nodemanager.resource.cpu-vcores": "2",
        "yarn.nodemanager.local-dirs": "/data/yarn/local",
        "yarn.nodemanager.log-dirs": "/data/yarn/logs",
        "yarn.nodemanager.vmem-check-enabled": "false",
        "yarn.nodemanager.pmem-check-enabled": "true",
        "yarn.nodemanager.env-whitelist": "JAVA_HOME,HADOOP_COMMON_HOME,HADOOP_HDFS_HOME,HADOOP_CONF_DIR,HADOOP_YARN_HOME,HADOOP_MAPRED_HOME,PATH,LANG,TZ",
        "yarn.scheduler.minimum-allocation-mb": "512",
        "yarn.scheduler.maximum-allocation-mb": "5120",
        "yarn.scheduler.minimum-allocation-vcores": "1",
        "yarn.scheduler.maximum-allocation-vcores": "2",
        "yarn.log-aggregation-enable": "true",
        "yarn.nodemanager.remote-app-log-dir": "/tmp/logs",
        "yarn.log-aggregation.retain-seconds": "604800",
    }
    mapred = {"mapreduce.framework.name": "yarn"}
    for name, props in (("core-site.xml", core), ("hdfs-site.xml", hdfs), ("yarn-site.xml", yarn), ("mapred-site.xml", mapred)):
        target = f"{HADOOP_CONF}/{name}"
        files[target] = merge_xml(existing.get(target, ""), props)
    files[f"{HADOOP_CONF}/dfs.hosts"] = "\n".join(item["tailscale_ip"] for item in nodes) + "\n"
    hdfs_excluded = [] if mode == "migration" else [master_ip]
    if mode == "final":
        hdfs_excluded.extend(retiring_worker_ips)
    files[f"{HADOOP_CONF}/dfs.hosts.exclude"] = "\n".join(hdfs_excluded) + ("\n" if hdfs_excluded else "")
    files[f"{HADOOP_CONF}/yarn.hosts"] = "\n".join(all_worker_ips) + "\n"
    files[f"{HADOOP_CONF}/yarn.hosts.exclude"] = ("\n".join(retiring_worker_ips) + "\n") if mode == "final" and retiring_worker_ips else ""
    # workers is for Hadoop helper commands only. Daemons are managed by systemd.
    files[f"{HADOOP_CONF}/workers"] = "\n".join(all_worker_ips) + "\n"
    env_values = {
        "java_home": inventory["java17_home"], "hadoop_conf": HADOOP_CONF,
        "node_ip": node_ip, "user": inventory["user"],
    }
    for name in ("hadoop-env.sh", "yarn-env.sh"):
        target = f"{HADOOP_CONF}/{name}"
        files[target] = merge_block(existing.get(target, ""), template(name + ".tmpl", **env_values))
    spark = {
        "spark.master": "yarn", "spark.submit.deployMode": "client",
        "spark.driver.host": master_ip, "spark.driver.bindAddress": master_ip,
        "spark.driver.port": str(p["spark_driver"]),
        "spark.blockManager.port": str(p["spark_blockmanager"]),
        "spark.ui.port": str(p["spark_ui"]), "spark.port.maxRetries": "0",
        "spark.executor.instances": "3", "spark.executor.cores": "1",
        "spark.executor.memory": "2g", "spark.executor.memoryOverhead": "1024m",
        "spark.driver.memory": "2g", "spark.yarn.am.memory": "512m",
        "spark.yarn.am.memoryOverhead": "512m", "spark.yarn.am.cores": "1",
        "spark.dynamicAllocation.enabled": "false", "spark.shuffle.service.enabled": "false",
        "spark.yarn.appMasterEnv.JAVA_HOME": inventory["java21_home"],
        "spark.executorEnv.JAVA_HOME": inventory["java21_home"],
        "spark.pyspark.python": "/usr/bin/python3",
        "spark.pyspark.driver.python": "/usr/bin/python3",
        "spark.yarn.submit.file.replication": "2", "spark.yarn.maxAppAttempts": "1",
        "spark.eventLog.enabled": "true", "spark.eventLog.dir": "hdfs:///spark-history",
        "spark.yarn.am.clientModeTreatDisconnectAsFailed": "true",
        "spark.yarn.am.clientModeExitOnError": "true",
    }
    files[f"{SPARK_CONF}/spark-defaults.conf"] = merge_spark_properties(existing.get(f"{SPARK_CONF}/spark-defaults.conf", ""), spark)
    files[f"{SPARK_CONF}/spark-env.sh"] = merge_block(existing.get(f"{SPARK_CONF}/spark-env.sh", ""), template(
        "spark-env.sh.tmpl", java_home=inventory["java21_home"], hadoop_conf=HADOOP_CONF, node_ip=node_ip))
    files["/etc/cosmos/hadoop/hosts.fragment"] = "\n".join(f"{n['tailscale_ip']}\t{n['name']}" for n in nodes) + "\n"

    installed_daemons = ["namenode", "secondarynamenode", "datanode", "resourcemanager"] if is_master else ["datanode", "nodemanager"]
    desired_daemons = list(installed_daemons)
    if is_master and mode in ("account-transition", "final"):
        desired_daemons.remove("datanode")
    for daemon in installed_daemons:
        binary = "hdfs" if daemon in ("namenode", "secondarynamenode", "datanode") else "yarn"
        condition = "ConditionPathExists=/data/hadoop/namenode/current/VERSION" if daemon == "namenode" else ""
        if not is_master:
            condition = "ConditionPathIsMountPoint=/data"
        files[f"/etc/systemd/system/cosmos-hadoop-{daemon}.service"] = template(
            "hadoop.service.tmpl", daemon=daemon, binary=binary, condition=condition,
            user=inventory["user"], java_home=inventory["java17_home"], hadoop_conf=HADOOP_CONF)
    manifest = {
        "schema_version": 1, "cluster_name": inventory["cluster_name"], "node": node_name,
        "node_ip": node_ip, "role": "master" if is_master else "worker", "mode": mode,
        "ports": PORTS, "master_ip": master_ip, "worker_ips": active_worker_ips,
        "retiring_worker_ips": retiring_worker_ips,
        "expected_datanodes_after_transition": 4 if mode == "migration" else (6 if mode == "account-transition" else 3),
        "expected_nodemanagers": 3 if not retiring_worker_ips or mode == "final" else 6,
        "desired_services": [f"cosmos-hadoop-{name}.service" for name in desired_daemons],
        "preserved_storage": [NAMENODE_DIR, DATANODE_DIR] if is_master else [DATANODE_DIR],
        "required_hdfs_directories": ["/user/ubuntu", "/tmp/logs", "/spark-history"],
        "notes": [
            "No daemon, firewall, format, setrep, refreshNodes, decommission or balancer command was run.",
            "Existing blocks keep their old replication factor until the operator runs a reviewed setrep.",
            "Initial final mode excludes the master for graceful decommission; wait for completion before stopping its DataNode.",
            "Account-transition keeps three active and three retiring Workers together; final then excludes the retiring Workers.",
            "Keep all cluster TCP ports private to the listed Tailscale peers. RPC9000 also serves master loopback clients.",
            "One Spark application at a time uses fixed ports. Each 3GiB executor allocation permits at most one per 5GiB worker.",
        ],
    }
    return files, manifest


def atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".cosmos-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_runtime_directories(account, parents=MANAGED_RUNTIME_PARENTS,
                                directories=MANAGED_RUNTIME_DIRECTORIES) -> None:
    """Make COSMOS-owned runtime paths usable without traversing their contents."""
    for target in (*parents, *directories):
        path = Path(target)
        if path.is_symlink():
            raise ValueError(f"Refusing a symlink runtime directory: {path}")
        try:
            metadata = path.stat()
        except FileNotFoundError:
            # Parents are ordered before their children. Avoid parents=True so
            # every COSMOS-owned component is checked independently.
            path.mkdir(mode=0o750)
            metadata = path.stat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"Runtime path is not a directory: {path}")
        # Repair the directory inode even when it pre-existed as root:root 0700.
        # Never recurse: DataNode blocks and the NameNode directory are untouched.
        os.chown(path, account.pw_uid, account.pw_gid)
        os.chmod(path, stat.S_IMODE(metadata.st_mode) | 0o700)


def install_configuration(inventory: dict, node_name: str, mode: str, backup_root: Path) -> dict:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError("--apply must run as root on the target Linux host")
    inventory = validate_inventory(inventory)
    nodes = [inventory["master"], *inventory["workers"], *inventory["retiring_workers"]]
    node = next((n for n in nodes if n["name"] == node_name), None)
    if node is None:
        raise ValueError("Requested node is not in the inventory")
    addresses = json.loads(subprocess.check_output(["ip", "-j", "address", "show", "dev", "tailscale0"], text=True))
    assigned = {entry["local"] for link in addresses for entry in link.get("addr_info", [])}
    if node["tailscale_ip"] not in assigned:
        raise ValueError("Inventory address is not assigned to this host's tailscale0")
    for executable in ("/opt/hadoop/bin/hdfs", "/opt/hadoop/bin/yarn", "/opt/spark/bin/spark-submit", inventory["java17_home"] + "/bin/java", inventory["java21_home"] + "/bin/java"):
        if not os.access(executable, os.X_OK):
            raise ValueError(f"Required runtime is missing: {executable}")
    is_master = node == inventory["master"]
    version = Path(NAMENODE_DIR) / "current/VERSION"
    version_hash = None
    if is_master:
        if not version.is_file():
            raise ValueError("Existing NameNode VERSION is missing; refusing to provision or format storage")
        version_hash = hashlib.sha256(version.read_bytes()).hexdigest()
    elif not os.path.ismount("/data"):
        raise ValueError("Worker /data EBS filesystem must already be mounted")
    import pwd
    account = pwd.getpwnam(inventory["user"])
    initial_files, _ = render_configuration(inventory, node_name, mode)
    existing = {}
    for target in initial_files:
        path = Path(target)
        if path.is_symlink():
            raise ValueError(f"Refusing to replace a symlink configuration file: {target}")
        if path.exists():
            existing[target] = path.read_text(encoding="utf-8")
    files, manifest = render_configuration(inventory, node_name, mode, existing)
    files["/etc/hosts"] = merge_hosts(Path("/etc/hosts").read_text(encoding="utf-8"), nodes)
    manifest_target = "/etc/cosmos/hadoop/configuration-manifest.json"
    files[manifest_target] = ""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = backup_root / f"{stamp}-{node_name}-{mode}"
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup, 0o700)
    originals = {}
    for target in files:
        path = Path(target)
        if path.exists():
            saved = backup / target.lstrip("/")
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, saved)
            originals[target] = (path.read_bytes(), path.stat())
        else:
            originals[target] = None
    if is_master:
        shutil.copy2(version, backup / "namenode.VERSION")
    manifest["backup_directory"] = str(backup)
    manifest["namenode_version_sha256"] = version_hash
    manifest["files"] = {target: {"existed": value is not None} for target, value in originals.items()}
    files[manifest_target] = json.dumps(manifest, indent=2) + "\n"
    atomic_write(backup / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode(), 0o600)
    # Repair pre-existing restrictive parents as well as leaf directories. This
    # changes only these directory inodes and never recurses into HDFS storage.
    prepare_runtime_directories(account)
    written = []
    try:
        for target, content in files.items():
            atomic_write(Path(target), content.encode("utf-8"))
            written.append(target)
        if is_master and hashlib.sha256(version.read_bytes()).hexdigest() != version_hash:
            raise RuntimeError("NameNode storage identity changed during configuration; inspect before restarting")
    except Exception:
        for target in reversed(written):
            original = originals[target]
            if original is None:
                Path(target).unlink(missing_ok=True)
            else:
                contents, stat = original
                atomic_write(Path(target), contents, stat.st_mode & 0o777)
                os.chown(target, stat.st_uid, stat.st_gid)
        raise
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--mode", choices=("migration", "account-transition", "final"), default="migration")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path, help="Render files under this directory without installing them")
    action.add_argument("--apply", action="store_true", help="Back up and install on this host; never restart daemons")
    parser.add_argument("--backup-root", type=Path, default=Path("/var/backups/cosmos-hadoop"))
    args = parser.parse_args()
    try:
        inventory = json.loads(args.inventory.read_text(encoding="utf-8-sig"))
        if args.apply:
            manifest = install_configuration(inventory, args.node, args.mode, args.backup_root)
        else:
            files, manifest = render_configuration(inventory, args.node, args.mode)
            args.output.mkdir(parents=True, exist_ok=True)
            for target, content in files.items():
                atomic_write(args.output / target.lstrip("/"), content.encode("utf-8"))
            atomic_write(args.output / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
        print(json.dumps(manifest, indent=2))
        return 0
    except (ValueError, OSError, ET.ParseError, subprocess.SubprocessError) as exc:
        print(f"Configuration refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
