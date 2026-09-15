#!/usr/bin/env python3
"""Preserve an existing NameNode and move its blocks to three already configured Workers."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request

try:
    import fcntl
except ImportError:  # Pure orchestration tests also run on Windows workstations.
    fcntl = None

NN_DIR = Path("/data/hadoop/namenode")
CONF = Path("/opt/hadoop/etc/hadoop")
OLD_UNITS = ("hadoop-hdfs-secondarynamenode.service", "hadoop-hdfs-datanode.service", "hadoop-hdfs-namenode.service")
WRITERS = ("news-hdfs-sync.timer", "news-hdfs-sync.service")
NN = "cosmos-hadoop-namenode.service"
SNN = "cosmos-hadoop-secondarynamenode.service"
DN = "cosmos-hadoop-datanode.service"
RM = "cosmos-hadoop-resourcemanager.service"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def version_identity_text(text):
    values = {}
    for line in text.splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    keys = ("namespaceID", "blockpoolID", "storageType", "cTime", "clusterID", "layoutVersion")
    missing = [key for key in keys if not values.get(key)]
    if missing:
        raise RuntimeError(f"NameNode VERSION identity fields are missing: {missing}")
    return {key: values[key] for key in keys}


def version_identity(path):
    return version_identity_text(Path(path).read_text())


def get_json(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=15) as response:
        return json.load(response)


def metric(bean, *names):
    for name in names:
        if name in bean:
            return int(bean[name])
    raise RuntimeError(f"Required NameNode metric missing: {names}")


class Migration:
    def __init__(self, args):
        self.args = args
        spec = importlib.util.spec_from_file_location("cosmos_config", args.config_script)
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)
        self.validate_storage = config.validate_existing_storage
        self.inventory = config.validate_inventory(json.loads(args.inventory.read_text()))
        self.master = self.inventory["master"]
        self.worker_ips = {node["tailscale_ip"] for node in self.inventory["workers"]}
        self.inventory_hash = hashlib.sha256(json.dumps(self.inventory, sort_keys=True).encode()).hexdigest()
        self.state_file = args.state_dir / "state.json"
        self.state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        if self.state and self.state.get("inventory_sha256") != self.inventory_hash:
            raise RuntimeError("Migration state belongs to a different inventory; preserve it and review")
        self.last_status = {}

    def log(self, message, **data):
        print(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "message": message, **data}), flush=True)

    def save(self, **values):
        self.state.update(values)
        self.state["inventory_sha256"] = self.inventory_hash
        self.state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.state_file.with_suffix(".next")
        temporary.write_text(json.dumps(self.state, indent=2) + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.state_file)

    def command(self, *command, hadoop=False, timeout=120, allow_failure=False):
        argv = list(command)
        if hadoop:
            argv = ["runuser", "-u", "ubuntu", "--", "env",
                    "JAVA_HOME=" + self.inventory["java17_home"], "HADOOP_CONF_DIR=" + str(CONF),
                    "/opt/hadoop/bin/hdfs", *argv]
        result = subprocess.run(argv, check=False, text=True, capture_output=True, timeout=timeout)
        if result.returncode and not allow_failure:
            raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(argv)}\n{result.stderr[-3000:]}\n{result.stdout[-3000:]}")
        return result

    def hdfs(self, *command, timeout=120):
        return self.command(*command, hadoop=True, timeout=timeout).stdout

    def unit(self, name):
        result = self.command("systemctl", "show", name, "--property=LoadState,ActiveState,UnitFileState", allow_failure=True)
        properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if result.returncode and properties.get("LoadState") != "not-found":
            raise RuntimeError(f"Cannot inspect systemd unit {name}: {result.stderr}")
        if "LoadState" not in properties:
            raise RuntimeError(f"systemd did not report unit state for {name}")
        return properties

    def stop_unit(self, name, disable=False):
        current = self.unit(name)
        if current.get("LoadState") == "not-found":
            return
        self.command("systemctl", "stop", name)
        if disable and current.get("UnitFileState") in ("enabled", "enabled-runtime"):
            flags = ["--runtime"] if current["UnitFileState"] == "enabled-runtime" else []
            self.command("systemctl", "disable", *flags, name)

    def pause_writers(self):
        for name in WRITERS:
            self.stop_unit(name, disable=True)

    def assert_version(self):
        if not (NN_DIR / "current/VERSION").is_file():
            raise RuntimeError("Existing NameNode VERSION is missing; this script never formats a NameNode")
        current = sha256(NN_DIR / "current/VERSION")
        identity = version_identity(NN_DIR / "current/VERSION")
        expected_identity = self.state.get("namenode_identity")
        if expected_identity is not None:
            if expected_identity != identity:
                raise RuntimeError("NameNode storage identity changed; stop and investigate before proceeding")
        elif self.state.get("namenode_version_sha256", current) != current:
            # State from the first script revision recorded only a byte hash. Hadoop
            # rewrites the leading timestamp comment during saveNamespace, so adopt
            # logical identity only when the verified offline archive exactly agrees.
            backup_value = self.state.get("metadata_backup")
            backup_hash = self.state.get("metadata_backup_sha256")
            if not backup_value or not backup_hash:
                raise RuntimeError("NameNode VERSION changed without a recorded logical identity; stop and investigate")
            backup = Path(backup_value)
            if not backup.is_file() or sha256(backup) != backup_hash:
                raise RuntimeError("NameNode VERSION changed and its offline backup cannot be verified")
            with tarfile.open(backup, "r:gz") as archive:
                member = archive.extractfile("namenode/current/VERSION")
                if member is None:
                    raise RuntimeError("Offline backup lacks NameNode VERSION")
                archived_identity = version_identity_text(member.read().decode())
            if archived_identity != identity:
                raise RuntimeError("Current NameNode identity differs from the verified offline backup")
            self.save(namenode_identity=identity,
                      identity_adopted_from_verified_backup_at=datetime.now(timezone.utc).isoformat())
        return current

    def snapshot(self):
        """Create the checkpoint online, then archive metadata with every old HDFS daemon stopped."""
        if self.state.get("metadata_backup"):
            backup = Path(self.state["metadata_backup"])
            if not backup.is_file() or sha256(backup) != self.state["metadata_backup_sha256"]:
                raise RuntimeError("The previous metadata backup is missing or has a different checksum")
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        directory = self.args.backup_root / (stamp + "-migration")
        directory.mkdir(parents=True, mode=0o700)
        os.chmod(directory, 0o700)
        self.save(backup_directory=str(directory))
        # Configuration rollback does not require replacing the live namespace.
        with tarfile.open(directory / "configuration-before.tar.gz", "w:gz") as archive:
            for path in self.backup_paths():
                if path.exists():
                    archive.add(path, arcname=str(path).lstrip("/"))
        self.save(root_counts_before=self.root_counts())
        self.hdfs("dfsadmin", "-safemode", "enter")
        self.hdfs("dfsadmin", "-saveNamespace", timeout=600)
        # No namespace operations occur between saveNamespace and the offline archive.
        # Stop SecondaryNameNode as well, so no concurrent checkpoint upload can race the archive.
        for name in OLD_UNITS:
            self.stop_unit(name)
        # If an earlier conversion already uses the new unit, also ensure it is offline.
        for name in (SNN, DN, NN):
            self.stop_unit(name)
        backup = directory / "namenode.tar.gz"
        with tarfile.open(backup, "w:gz") as archive:
            archive.add(NN_DIR, arcname="namenode")
        with tarfile.open(backup, "r:gz") as archive:
            names = archive.getnames()
            if "namenode/current/VERSION" not in names or not any(re.fullmatch(r"namenode/current/fsimage_\d+", name) for name in names):
                raise RuntimeError("Offline metadata backup does not contain VERSION and a checkpoint fsimage")
        digest = sha256(backup)
        (directory / "SHA256SUMS").write_text(f"{digest}  namenode.tar.gz\n{sha256(directory / 'configuration-before.tar.gz')}  configuration-before.tar.gz\n")
        (directory / "RECOVERY.txt").write_text(
            "All original HDFS blocks remain in /data/hadoop/datanode. Nothing was formatted or deleted.\n"
            "configuration-before.tar.gz preserves original Hadoop/Spark config, hosts and existing units.\n"
            "Review state.json service states before restoring settings and restarting the original units.\n"
            "Never run original and cosmos NameNode/DataNode units concurrently.\n"
            "Do not restore this older fsimage over a namespace after writes have resumed; review edits and cluster ID first.\n"
            "On any failure the script leaves writers paused, existing blocks intact and backups available.\n")
        shutil.copy2(self.state_file, directory / "state-before-transition.json")
        self.save(metadata_backup=str(backup), metadata_backup_sha256=digest,
                  namenode_version_sha256_after_checkpoint=sha256(NN_DIR / "current/VERSION"))
        self.log("Offline NameNode backup verified", path=str(backup), sha256=digest)

    def backup_paths(self):
        paths = [CONF, Path("/opt/spark/conf"), Path("/etc/hosts")]
        return paths + [Path("/etc/systemd/system") / name for name in (*OLD_UNITS, *WRITERS, NN, SNN, DN, RM)]

    def apply(self, mode):
        result = self.command("python3", str(self.args.config_script), "--inventory", str(self.args.inventory),
                              "--node", self.master["name"], "--mode", mode, "--apply", timeout=180)
        manifest = json.loads(result.stdout)
        history = self.state.get("configuration_backups", [])
        history.append(manifest["backup_directory"])
        self.save(configuration_backups=history, mode=mode)
        self.assert_version()

    def status(self):
        base = f"http://{self.master['tailscale_ip']}:19870/jmx?"
        query = lambda name: urllib.parse.urlencode({"qry": f"Hadoop:service=NameNode,name={name}"})
        info = get_json(base + query("NameNodeInfo"))["beans"][0]
        metrics = get_json(base + query("FSNamesystem"))["beans"][0]
        live = json.loads(info["LiveNodes"])
        nodes = {}
        for name, value in live.items():
            address = (value.get("xferaddr") or name).removeprefix("/").split(":", 1)[0]
            nodes[address] = value.get("adminState", "In Service")
        result = {
            "nodes": nodes,
            "missing": metric(info, "NumberOfMissingBlocks"),
            "under_replicated": metric(metrics, "LowRedundancyBlocks", "UnderReplicatedBlocks"),
            "pending": metric(metrics, "PendingReconstructionBlocks", "PendingReplicationBlocks"),
            "corrupt": metric(metrics, "CorruptBlocks"),
        }
        self.last_status = result
        return result

    def wait(self, description, predicate):
        deadline = time.monotonic() + self.args.timeout
        while True:
            try:
                status = self.status()
                self.log(description, **status)
                if predicate(status):
                    return status
            except (OSError, KeyError, IndexError) as error:
                self.log("Waiting for NameNode management endpoint", error=str(error))
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Timed out: {description}; last status={self.last_status}")
            time.sleep(self.args.poll_interval)

    @staticmethod
    def healthy(status):
        return not any(status[key] for key in ("missing", "under_replicated", "pending", "corrupt"))

    def root_counts(self):
        output = self.hdfs("dfs", "-count", "/", timeout=300).strip().split()
        if len(output) != 4 or output[3] != "/":
            raise RuntimeError("Unexpected HDFS root count output")
        return {"directories": int(output[0]), "files": int(output[1]), "bytes": int(output[2])}

    def fsck(self, label):
        output = self.hdfs("fsck", "/", "-openforwrite", timeout=600)
        path = self.args.state_dir / (label + ".fsck.txt")
        path.write_text(output)
        if not re.search(r"is HEALTHY|Status:\s*HEALTHY", output):
            raise RuntimeError(f"HDFS is not healthy; retained report: {path}")
        if "OPENFORWRITE" in output:
            raise RuntimeError("HDFS contains files still open for writing; close writers/leases before continuing")
        counts = self.root_counts()
        baseline = self.state["root_counts_before"]
        if counts["files"] < baseline["files"] or counts["bytes"] < baseline["bytes"]:
            raise RuntimeError("Existing HDFS file count or content size decreased; original Master DataNode will be retained")
        self.save(**{label + "_counts": counts})

    def master_start(self):
        if self.state.get("phase") in ("replicated", "decommissioning", "finalized"):
            raise RuntimeError("Migration already advanced; use the appropriate next phase, never restart the original topology")
        if self.state.get("phase") == "master-started":
            current = self.status()
            if self.master["tailscale_ip"] in current["nodes"] and current["missing"] == 0 and all(
                self.unit(name).get("ActiveState") == "active" for name in (NN, SNN, DN, RM)
            ):
                self.log("Master migration services are already healthy; no services were changed")
                return
        self.validate_storage((CONF / "hdfs-site.xml").read_text(), True)
        self.assert_version()
        if "services_before" not in self.state:
            self.save(namenode_version_sha256=self.assert_version(),
                      namenode_identity=version_identity(NN_DIR / "current/VERSION"),
                      services_before={name: self.unit(name) for name in (*OLD_UNITS, *WRITERS, NN, SNN, DN, RM)})
        self.pause_writers()
        self.snapshot()
        for name in OLD_UNITS:
            self.stop_unit(name, disable=True)
        self.apply("migration")
        self.command("systemctl", "daemon-reload")
        self.command("systemctl", "enable", NN, SNN, DN, RM)
        for name in (NN, DN, SNN, RM):
            self.command("systemctl", "restart", name, timeout=180)
        self.wait("Waiting for the preserved Master DataNode", lambda state: self.master["tailscale_ip"] in state["nodes"] and state["missing"] == 0)
        self.hdfs("dfsadmin", "-safemode", "wait", timeout=600)
        self.save(phase="master-started")
        self.log("Master migration services started; configure/start all three Workers separately, then run --replicate")

    def replicate(self):
        if self.state.get("phase") not in ("master-started", "replicating", "replicated"):
            raise RuntimeError("Complete --master-start before requesting replication")
        self.assert_version()
        self.pause_writers()
        expected = self.worker_ips | {self.master["tailscale_ip"]}
        current = self.status()
        if set(current["nodes"]) != expected or any(value != "In Service" for value in current["nodes"].values()):
            raise RuntimeError("Replication requires the original Master and exactly three expected Workers in service")
        if current["missing"] or current["corrupt"]:
            raise RuntimeError("Existing missing/corrupt blocks must be investigated before replication")
        self.fsck("before-replication")
        self.save(phase="replicating")
        # Recursive root scope covers every existing dataset, including paths added by other teams.
        # This changes desired replication only; it never removes files or blocks manually.
        output = self.hdfs("dfs", "-setrep", "-R", "2", "/", timeout=self.args.timeout)
        (self.args.state_dir / "setrep.log").write_text(output)
        self.wait("Waiting for replication 2", lambda state: set(state["nodes"]) == expected and self.healthy(state))
        self.fsck("after-replication")
        self.save(phase="replicated")
        self.log("Every closed HDFS file requested replication 2; zero missing/corrupt/under-replicated/pending blocks")

    def finalize(self):
        if self.state.get("phase") not in ("replicated", "decommissioning", "finalized"):
            raise RuntimeError("Complete --replicate before decommissioning the original Master")
        self.assert_version()
        if self.state.get("phase") == "finalized":
            current = self.status()
            if set(current["nodes"]) != self.worker_ips or not self.healthy(current):
                raise RuntimeError("Previously finalized cluster is no longer healthy")
            if self.args.resume_writer:
                self.resume_writer()
            self.log("Cluster is already finalized", writer_resume_requested=self.args.resume_writer)
            return
        self.pause_writers()
        if not self.state.get("decommission_completed"):
            current = self.status()
            if not self.healthy(current) or not self.worker_ips.issubset(current["nodes"]):
                raise RuntimeError("All Worker replicas must be healthy before decommission starts")
            self.fsck("before-decommission")
            self.apply("final")
            self.save(phase="decommissioning")
            self.hdfs("dfsadmin", "-refreshNodes")
            self.wait("Waiting for original Master decommission",
                      lambda state: self.worker_ips.issubset(state["nodes"])
                      and state["nodes"].get(self.master["tailscale_ip"]) == "Decommissioned" and self.healthy(state))
            self.fsck("after-decommission")
            self.save(decommission_completed=True)
        # Only this point may stop the last original local copy's service; files remain on disk.
        self.stop_unit(DN, disable=True)
        self.wait("Waiting for precisely three live Workers (heartbeat expiry can take several minutes)",
                  lambda state: set(state["nodes"]) == self.worker_ips and self.healthy(state))
        self.fsck("final")
        self.save(phase="finalized")
        if self.args.resume_writer:
            self.resume_writer()
        self.log("Final cluster healthy; original blocks and metadata/configuration backups retained", writer_resumed=self.args.resume_writer)

    def resume_writer(self):
        for name in WRITERS:
            original = self.state["services_before"].get(name, {})
            if original.get("UnitFileState") in ("enabled", "enabled-runtime"):
                flags = ["--runtime"] if original["UnitFileState"] == "enabled-runtime" else []
                self.command("systemctl", "enable", *flags, name)
            if original.get("ActiveState") in ("active", "activating", "reloading"):
                self.command("systemctl", "start", name)
        self.save(writer_resume_requested=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    phase = parser.add_mutually_exclusive_group(required=True)
    phase.add_argument("--master-start", action="store_true")
    phase.add_argument("--replicate", action="store_true")
    phase.add_argument("--finalize", action="store_true")
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--config-script", type=Path, default=Path(__file__).with_name("configure-cluster.py"))
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/cosmos-hadoop-migration"))
    parser.add_argument("--backup-root", type=Path, default=Path("/var/backups/cosmos-hadoop"))
    parser.add_argument("--timeout", type=int, default=21600, help="Maximum seconds for replication/decommission polling")
    parser.add_argument("--poll-interval", type=int, default=30)
    parser.add_argument("--resume-writer", action="store_true", help="Only with --finalize; original broken writer stays paused by default")
    args = parser.parse_args()
    if sys.platform != "linux" or fcntl is None:
        parser.error("Real migration must run on the Linux Master; importing the module for tests is portable")
    if os.geteuid() != 0:
        parser.error("Run on the existing Master as root")
    if args.resume_writer and not args.finalize:
        parser.error("--resume-writer is only valid with --finalize")
    if args.timeout < 1 or args.poll_interval < 1:
        parser.error("Timeout and poll interval must be positive")
    os.umask(0o077)
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.state_dir / "migration.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        migration = Migration(args)
        try:
            if args.master_start:
                migration.master_start()
            elif args.replicate:
                migration.replicate()
            else:
                migration.finalize()
            migration.save(last_error=None)
        except Exception as error:
            migration.save(last_error=str(error))
            migration.log("Migration stopped; no data deleted, writers stay paused; inspect retained backups/state", error=str(error))
            raise SystemExit(1)


if __name__ == "__main__":
    main()
