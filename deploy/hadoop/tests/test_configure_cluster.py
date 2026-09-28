"""Pure configuration tests; these never access a cluster or install files."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import stat
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "configure-cluster.py"
SPEC = importlib.util.spec_from_file_location("configure_cluster", SCRIPT)
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)


def inventory():
    return {
        "master": {"name": "cosmos-master", "tailscale_ip": "100.70.0.1"},
        "workers": [
            {"name": f"cosmos-worker-{i}", "tailscale_ip": f"100.70.0.{i + 1}"}
            for i in range(1, 4)
        ],
    }


def transition_inventory():
    result = inventory()
    result["retiring_workers"] = [
        {"name": f"cosmos-retiring-{i}", "tailscale_ip": f"100.71.0.{i}"}
        for i in range(1, 4)
    ]
    return result


class ClusterConfigurationTests(unittest.TestCase):
    def test_master_storage_and_loopback_compatibility(self):
        original = config.merge_xml("", {
            "dfs.namenode.name.dir": "file:///data/hadoop/namenode",
            "dfs.datanode.data.dir": "/data/hadoop/datanode",
            "dfs.blocksize": "134217728",
        })
        files, _ = config.render_configuration(inventory(), "cosmos-master", "migration", {
            f"{config.HADOOP_CONF}/hdfs-site.xml": original,
        })
        hdfs = config.xml_properties(files[f"{config.HADOOP_CONF}/hdfs-site.xml"])
        core = config.xml_properties(files[f"{config.HADOOP_CONF}/core-site.xml"])
        self.assertEqual(hdfs["dfs.namenode.name.dir"], "file:///data/hadoop/namenode")
        self.assertEqual(hdfs["dfs.datanode.data.dir"], "file:///data/hadoop/datanode")
        self.assertEqual(hdfs["dfs.namenode.rpc-bind-host"], "0.0.0.0")
        self.assertEqual(hdfs["dfs.namenode.rpc-address"], "100.70.0.1:9000")
        self.assertEqual(hdfs["dfs.namenode.replication.max-streams"], "256")
        self.assertEqual(hdfs["dfs.namenode.replication.max-streams-hard-limit"], "512")
        self.assertEqual(core["fs.defaultFS"], "hdfs://100.70.0.1:9000")
        self.assertEqual(hdfs["dfs.blocksize"], "134217728")

    def test_rejects_storage_relocation_before_rendering(self):
        for key in ("dfs.namenode.name.dir", "dfs.namenode.edits.dir", "dfs.datanode.data.dir"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "Refusing to change"):
                config.validate_existing_storage(config.merge_xml("", {key: "/other/data"}), True)

    def test_final_decommissions_without_removing_master_from_allowlist(self):
        migration, migration_manifest = config.render_configuration(inventory(), "cosmos-master", "migration")
        final, final_manifest = config.render_configuration(inventory(), "cosmos-master", "final")
        self.assertEqual(migration[f"{config.HADOOP_CONF}/dfs.hosts.exclude"], "")
        self.assertEqual(final[f"{config.HADOOP_CONF}/dfs.hosts.exclude"], "100.70.0.1\n")
        self.assertEqual(final[f"{config.HADOOP_CONF}/dfs.hosts"], migration[f"{config.HADOOP_CONF}/dfs.hosts"])
        self.assertEqual(migration_manifest["expected_datanodes_after_transition"], 4)
        self.assertEqual(final_manifest["expected_datanodes_after_transition"], 3)
        self.assertNotIn("cosmos-hadoop-datanode.service", final_manifest["desired_services"])
        self.assertIn("/etc/systemd/system/cosmos-hadoop-datanode.service", final)
        final_hdfs = config.xml_properties(final[f"{config.HADOOP_CONF}/hdfs-site.xml"])
        self.assertEqual(final_hdfs["dfs.namenode.replication.max-streams"], "8")

    def test_master_keeps_secondary_namenode_checkpoints(self):
        files, manifest = config.render_configuration(inventory(), "cosmos-master", "final")
        hdfs = config.xml_properties(files[f"{config.HADOOP_CONF}/hdfs-site.xml"])
        self.assertEqual(hdfs["dfs.namenode.secondary.http-address"], "100.70.0.1:19868")
        self.assertIn("cosmos-hadoop-secondarynamenode.service", manifest["desired_services"])
        unit = files["/etc/systemd/system/cosmos-hadoop-secondarynamenode.service"]
        self.assertIn("/opt/hadoop/bin/hdfs secondarynamenode", unit)

    def test_account_transition_overlaps_six_workers_then_excludes_old_three(self):
        overlap, overlap_manifest = config.render_configuration(
            transition_inventory(), "cosmos-master", "account-transition")
        final, final_manifest = config.render_configuration(
            transition_inventory(), "cosmos-master", "final")
        active = [f"100.70.0.{i + 1}" for i in range(1, 4)]
        retiring = [f"100.71.0.{i}" for i in range(1, 4)]
        self.assertEqual(overlap[f"{config.HADOOP_CONF}/dfs.hosts.exclude"], "100.70.0.1\n")
        self.assertEqual(final[f"{config.HADOOP_CONF}/dfs.hosts.exclude"].splitlines(), ["100.70.0.1", *retiring])
        self.assertEqual(overlap[f"{config.HADOOP_CONF}/yarn.hosts"].splitlines(), active + retiring)
        self.assertEqual(final[f"{config.HADOOP_CONF}/yarn.hosts.exclude"].splitlines(), retiring)
        self.assertEqual(overlap_manifest["expected_datanodes_after_transition"], 6)
        self.assertEqual(overlap_manifest["expected_nodemanagers"], 6)
        self.assertEqual(final_manifest["expected_datanodes_after_transition"], 3)
        self.assertEqual(final_manifest["expected_nodemanagers"], 3)
        self.assertNotIn("cosmos-hadoop-datanode.service", overlap_manifest["desired_services"])

    def test_account_transition_requires_three_retiring_workers(self):
        with self.assertRaisesRegex(ValueError, "exactly three retiring_workers"):
            config.render_configuration(inventory(), "cosmos-master", "account-transition")

    def test_workers_advertise_only_their_tailscale_address(self):
        for node in inventory()["workers"]:
            files, manifest = config.render_configuration(inventory(), node["name"], "migration")
            hdfs = config.xml_properties(files[f"{config.HADOOP_CONF}/hdfs-site.xml"])
            yarn = config.xml_properties(files[f"{config.HADOOP_CONF}/yarn-site.xml"])
            self.assertEqual(hdfs["dfs.datanode.hostname"], node["tailscale_ip"])
            self.assertEqual(yarn["yarn.nodemanager.hostname"], node["tailscale_ip"])
            self.assertEqual(yarn["yarn.nodemanager.resource.memory-mb"], "5120")
            self.assertEqual(yarn["yarn.nodemanager.resource.cpu-vcores"], "2")
            self.assertEqual(set(manifest["desired_services"]), {"cosmos-hadoop-datanode.service", "cosmos-hadoop-nodemanager.service"})
            unit = files["/etc/systemd/system/cosmos-hadoop-datanode.service"]
            self.assertIn("ConditionPathIsMountPoint=/data", unit)
            self.assertNotIn("format", unit)

    def test_spark_java_matches_driver_am_and_executors(self):
        files, _ = config.render_configuration(inventory(), "cosmos-master", "migration")
        props = dict(line.split(None, 1) for line in files[f"{config.SPARK_CONF}/spark-defaults.conf"].splitlines() if line and not line.startswith("#"))
        java21 = "/usr/lib/jvm/java-21-openjdk-amd64"
        self.assertEqual(props["spark.executorEnv.JAVA_HOME"], java21)
        self.assertEqual(props["spark.yarn.appMasterEnv.JAVA_HOME"], java21)
        self.assertIn(java21, files[f"{config.SPARK_CONF}/spark-env.sh"])
        self.assertIn("java-17", files[f"{config.HADOOP_CONF}/hadoop-env.sh"])
        self.assertEqual(props["spark.master"], "yarn")
        self.assertEqual(props["spark.submit.deployMode"], "client")
        self.assertEqual(props["spark.executor.instances"], "3")
        self.assertEqual(props["spark.executor.memory"], "2g")
        self.assertGreater(2048 + int(props["spark.executor.memoryOverhead"].rstrip("m")), 5120 / 2)
        self.assertNotIn("spark.yarn.am.port", props)

    def test_existing_restrictive_runtime_parents_are_repaired_without_recursion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "data" / "spark"
            runtime = parent / "local"
            runtime.mkdir(parents=True)
            untouched = runtime / "existing-shuffle-data"
            untouched.write_text("preserve")
            parent.chmod(0o700)
            runtime.chmod(0o500)
            chowns, chmods = [], []

            with mock.patch.object(config.os, "chown", create=True,
                                   side_effect=lambda path, uid, gid: chowns.append((Path(path), uid, gid))), \
                    mock.patch.object(config.os, "chmod", side_effect=lambda path, mode: chmods.append((Path(path), mode))):
                config.prepare_runtime_directories(
                    SimpleNamespace(pw_uid=1000, pw_gid=1000),
                    parents=(parent,), directories=(runtime,))

            self.assertEqual(chowns, [(parent, 1000, 1000), (runtime, 1000, 1000)])
            self.assertEqual([path for path, _ in chmods], [parent, runtime])
            self.assertTrue(all(mode & stat.S_IRWXU == stat.S_IRWXU for _, mode in chmods))
            self.assertNotIn(untouched, [path for path, *_ in chowns] + [path for path, _ in chmods])
            self.assertEqual(untouched.read_text(), "preserve")

    def test_missing_runtime_parent_and_leaf_are_created_in_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "data" / "yarn"
            parent.parent.mkdir()
            runtime = parent / "local"
            with mock.patch.object(config.os, "chown", create=True), \
                    mock.patch.object(config.os, "chmod"):
                config.prepare_runtime_directories(
                    SimpleNamespace(pw_uid=1000, pw_gid=1000),
                    parents=(parent,), directories=(runtime,))
            self.assertTrue(parent.is_dir())
            self.assertTrue(runtime.is_dir())

    def test_runtime_directory_rejects_non_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            regular = root / "regular-file"
            regular.write_text("preserve")
            with self.assertRaisesRegex(ValueError, "not a directory"):
                config.prepare_runtime_directories(
                    SimpleNamespace(pw_uid=1000, pw_gid=1000), parents=(regular,), directories=())

    def test_runtime_directory_rejects_symlinks(self):
        with mock.patch.object(config.Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(ValueError, "symlink"):
                config.prepare_runtime_directories(
                    SimpleNamespace(pw_uid=1000, pw_gid=1000),
                    parents=(Path("/data/spark"),), directories=())

    def test_invalid_inventory_cannot_render_configuration(self):
        bad = inventory()
        bad["workers"][0]["tailscale_ip"] = "127.0.0.1"
        with self.assertRaises(ValueError):
            config.validate_inventory(bad)
        duplicate = inventory()
        duplicate["workers"][0]["tailscale_ip"] = duplicate["master"]["tailscale_ip"]
        with self.assertRaises(ValueError):
            config.validate_inventory(duplicate)
        injection = inventory()
        injection["java21_home"] = "/opt/java;touch /tmp/unwanted"
        with self.assertRaises(ValueError):
            config.validate_inventory(injection)

    def test_hosts_preserves_other_entries_and_replaces_loopback_alias(self):
        original = "127.0.0.1 localhost\n127.0.1.1 cosmos-worker-1 unrelated\n10.0.0.5 existing-service\n"
        nodes = [inventory()["master"], *inventory()["workers"]]
        updated = config.merge_hosts(original, nodes)
        self.assertIn("127.0.0.1 localhost", updated)
        self.assertIn("10.0.0.5 existing-service", updated)
        self.assertIn("127.0.1.1\tunrelated", updated)
        self.assertNotIn("127.0.1.1 cosmos-worker-1", updated)
        self.assertEqual(updated.count("cosmos-worker-1"), 1)
        self.assertEqual(config.merge_hosts(updated, nodes), updated)

    def test_render_is_idempotent_and_preserves_unmanaged_settings(self):
        source = {f"{config.SPARK_CONF}/spark-defaults.conf": "spark.sql.shuffle.partitions 12\nspark.master local[2]\n"}
        once, _ = config.render_configuration(inventory(), "cosmos-master", "migration", source)
        twice, _ = config.render_configuration(inventory(), "cosmos-master", "migration", once)
        self.assertEqual(once, twice)
        self.assertIn("spark.sql.shuffle.partitions 12", twice[f"{config.SPARK_CONF}/spark-defaults.conf"])
        self.assertNotIn("local[2]", twice[f"{config.SPARK_CONF}/spark-defaults.conf"])


if __name__ == "__main__":
    unittest.main()
