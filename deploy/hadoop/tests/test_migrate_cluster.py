"""Exercise migration ordering with fake systemd/HDFS and temporary metadata, never a live cluster."""

from argparse import Namespace
import importlib.util
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


path = Path(__file__).parents[1] / "migrate-cluster.py"
spec = importlib.util.spec_from_file_location("migration", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
MASTER = "100.117.115.44"
WORKERS = {"100.80.0.1", "100.80.0.2", "100.80.0.3"}


def status(master_state="In Service", under=0, include_master=True):
    nodes = {worker: "In Service" for worker in WORKERS}
    if include_master:
        nodes[MASTER] = master_state
    return {"nodes": nodes, "missing": 0, "under_replicated": under, "pending": 0, "corrupt": 0}


class FakeMigration(migration.Migration):
    def __init__(self, directory):
        self.args = Namespace(state_dir=directory / "state", backup_root=directory / "backups", timeout=2,
                              poll_interval=1, resume_writer=False)
        self.inventory = {"java17_home": "/unused/java17"}
        self.master = {"name": "cosmos-master", "tailscale_ip": MASTER}
        self.worker_ips = WORKERS
        self.inventory_hash = "test-inventory"
        self.state_file = self.args.state_dir / "state.json"
        self.state = {}
        self.events = []
        self.last_status = {}
        self.current_status = status()
        self.wait_statuses = []
        self.content_size = 103 * 1024**3
        self.validate_storage = lambda *_: None

    def log(self, message, **data):
        self.events.append(("log", message))

    def backup_paths(self):
        return [migration.CONF]

    def command(self, *command, **kwargs):
        self.events.append(command)
        if command[:2] == ("systemctl", "show"):
            output = "LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n"
        else:
            output = ""
        return subprocess.CompletedProcess(command, 0, output, "")

    def hdfs(self, *command, **kwargs):
        self.events.append(("hdfs", *command))
        if command[:2] == ("dfs", "-count"):
            return f"12 35 {self.content_size} /\n"
        if command[0] == "fsck":
            return "The filesystem under path '/' is HEALTHY\n"
        return "OK\n"

    def apply(self, mode):
        # An actual verified metadata tar must already exist before any configuration mutation.
        if not self.state.get("metadata_backup"):
            raise AssertionError("configuration changed before metadata backup")
        backup = Path(self.state["metadata_backup"])
        if migration.sha256(backup) != self.state["metadata_backup_sha256"]:
            raise AssertionError("configuration changed with an invalid backup")
        self.events.append(("apply", mode))
        self.save(mode=mode)

    def status(self):
        return self.current_status

    def wait(self, description, predicate):
        sequence = self.wait_statuses.pop(0) if self.wait_statuses else [self.current_status]
        for item in sequence:
            if predicate(item):
                self.events.append(("wait-success", description))
                self.current_status = item
                return item
        raise RuntimeError("simulated timeout with replicas still unsafe")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cosmos-migrate-tests-")
        directory = Path(self.temp.name)
        self.nn = directory / "namenode"
        (self.nn / "current").mkdir(parents=True)
        (self.nn / "current/VERSION").write_bytes(
            b"#initial timestamp\nnamespaceID=123\nblockpoolID=BP-existing\n"
            b"storageType=NAME_NODE\ncTime=456\nclusterID=existing-cluster\nlayoutVersion=-67\n")
        (self.nn / "current/fsimage_0000000000012345678").write_bytes(b"existing checkpoint")
        self.conf = directory / "conf"
        self.conf.mkdir()
        (self.conf / "hdfs-site.xml").write_text("<configuration/>")
        self.paths = patch.multiple(migration, NN_DIR=self.nn, CONF=self.conf)
        self.paths.start()
        self.runner = FakeMigration(directory)

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def prepare(self):
        self.runner.master_start()
        return self.runner

    def test_checkpoint_offline_backup_precedes_any_configuration_change(self):
        runner = self.prepare()
        events = runner.events
        checkpoint = events.index(("hdfs", "dfsadmin", "-saveNamespace"))
        stopped_nn = events.index(("systemctl", "stop", "hadoop-hdfs-namenode.service"))
        apply_config = events.index(("apply", "migration"))
        self.assertLess(checkpoint, stopped_nn)
        self.assertLess(stopped_nn, apply_config)
        self.assertLess(events.index(("systemctl", "stop", "news-hdfs-sync.timer")), checkpoint)
        self.assertLess(events.index(("systemctl", "stop", "news-hdfs-sync.service")), checkpoint)
        with tarfile.open(runner.state["metadata_backup"]) as archive:
            self.assertIn(b"clusterID=existing-cluster", archive.extractfile("namenode/current/VERSION").read())
        self.assertTrue((self.nn / "current/VERSION").is_file())
        self.assertEqual(runner.state["phase"], "master-started")
        self.assertIn(("systemctl", "restart", migration.SNN), events)

    def test_checkpoint_timestamp_rewrite_preserves_logical_identity(self):
        runner = self.prepare()
        version = self.nn / "current/VERSION"
        version.write_text(version.read_text().replace("#initial timestamp", "#new checkpoint timestamp"))
        self.assertNotEqual(migration.sha256(version), runner.state["namenode_version_sha256"])
        runner.assert_version()

    def test_storage_identity_change_is_refused(self):
        runner = self.prepare()
        version = self.nn / "current/VERSION"
        version.write_text(version.read_text().replace("clusterID=existing-cluster", "clusterID=different"))
        with self.assertRaisesRegex(RuntimeError, "storage identity changed"):
            runner.assert_version()

    def test_legacy_hash_state_adopts_identity_only_from_verified_backup(self):
        runner = self.prepare()
        runner.state.pop("namenode_identity")
        runner.state["namenode_version_sha256"] = "old-checkpoint-byte-hash"
        runner.assert_version()
        self.assertEqual(runner.state["namenode_identity"]["clusterID"], "existing-cluster")
        runner.state.pop("namenode_identity")
        runner.state["metadata_backup_sha256"] = "untrusted"
        with self.assertRaisesRegex(RuntimeError, "cannot be verified"):
            runner.assert_version()

    def test_replication_refuses_to_start_without_all_three_workers(self):
        runner = self.prepare()
        runner.events.clear()
        runner.current_status["nodes"].pop("100.80.0.3")
        with self.assertRaisesRegex(RuntimeError, "exactly three"):
            runner.replicate()
        self.assertFalse(any("-setrep" in event for event in runner.events))

    def test_replication_updates_all_paths_and_preserves_master_copy_service(self):
        runner = self.prepare()
        runner.events.clear()
        runner.replicate()
        self.assertIn(("hdfs", "dfs", "-setrep", "-R", "2", "/"), runner.events)
        self.assertNotIn(("systemctl", "stop", migration.DN), runner.events)
        self.assertEqual(runner.state["phase"], "replicated")

    def test_failed_decommission_never_stops_original_datanode(self):
        runner = self.prepare()
        runner.save(phase="replicated")
        runner.events.clear()
        runner.wait_statuses = [[status("Decommission In Progress", under=1)]]
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            runner.finalize()
        self.assertNotIn(("systemctl", "stop", migration.DN), runner.events)
        self.assertEqual(runner.state["phase"], "decommissioning")
        self.assertTrue(Path(runner.state["metadata_backup"]).exists())

    def test_master_stops_only_after_completed_healthy_decommission(self):
        runner = self.prepare()
        runner.save(phase="replicated")
        runner.events.clear()
        runner.wait_statuses = [[status("Decommission In Progress", under=1), status("Decommissioned")],
                                [status(include_master=False)]]
        runner.finalize()
        safe = runner.events.index(("wait-success", "Waiting for original Master decommission"))
        stop = runner.events.index(("systemctl", "stop", migration.DN))
        self.assertLess(safe, stop)
        self.assertEqual(runner.state["phase"], "finalized")
        self.assertTrue((self.nn / "current/VERSION").is_file())
        self.assertFalse(any(event[:2] == ("systemctl", "start") and event[-1] in migration.WRITERS for event in runner.events))
        self.assertFalse(any(part in ("-format", "-rm", "-rmr", "mkfs", "terminate-instances") for event in runner.events for part in event))

    def test_decreased_existing_data_stops_finalization_before_config_change(self):
        runner = self.prepare()
        runner.save(phase="replicated")
        runner.events.clear()
        runner.content_size -= 1
        with self.assertRaisesRegex(RuntimeError, "content size decreased"):
            runner.finalize()
        self.assertNotIn(("apply", "final"), runner.events)
        self.assertNotIn(("systemctl", "stop", migration.DN), runner.events)

    def test_master_start_rerun_does_not_restart_healthy_services(self):
        runner = self.prepare()
        runner.events.clear()
        runner.master_start()
        self.assertFalse(any(event[:2] in (("systemctl", "stop"), ("systemctl", "restart")) for event in runner.events))

    def test_finalized_rerun_does_not_pause_a_manually_resumed_writer(self):
        runner = self.prepare()
        runner.save(phase="finalized")
        runner.current_status = status(include_master=False)
        runner.events.clear()
        runner.finalize()
        self.assertFalse(any(event[:2] == ("systemctl", "stop") for event in runner.events))

    def test_missing_new_units_are_allowed_before_initial_installation(self):
        with patch.object(self.runner, "command", return_value=subprocess.CompletedProcess(
            [], 1, "LoadState=not-found\nActiveState=inactive\nUnitFileState=\n", "Unit does not exist"
        )):
            self.assertEqual(self.runner.unit(migration.NN)["LoadState"], "not-found")


if __name__ == "__main__":
    unittest.main()
