"""Topology checks cannot pass with duplicate, unexpected, or partially live Workers."""

import importlib.util
import json
from pathlib import Path
import unittest


path = Path(__file__).parents[1] / "verify-cluster.py"
spec = importlib.util.spec_from_file_location("verify_cluster", path)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)
WORKERS = [{"name": f"cosmos-worker-{number}", "tailscale_ip": f"100.80.0.{number}"} for number in range(1, 4)]


class TopologyTests(unittest.TestCase):
    def test_accepts_named_and_tailscale_nodes(self):
        observed = [["cosmos-worker-1.example.ts.net:19866"], ["100.80.0.2:19866"], ["cosmos-worker-3"]]
        self.assertEqual(verify.match_workers(observed, WORKERS), [worker["name"] for worker in WORKERS])

    def test_rejects_missing_duplicate_or_unexpected_workers(self):
        for observed in (
            [["cosmos-worker-1"], ["cosmos-worker-2"]],
            [["cosmos-worker-1"], ["cosmos-worker-2"], ["cosmos-worker-2"]],
            [["cosmos-worker-1"], ["cosmos-worker-2"], ["cosmos-master"]],
        ):
            with self.subTest(observed=observed), self.assertRaises(RuntimeError):
                verify.match_workers(observed, WORKERS)

    def test_management_url_is_limited_to_inventory_master(self):
        master = {"name": "cosmos-master", "tailscale_ip": "100.117.115.44"}
        self.assertEqual(verify.web_url("0.0.0.0:19870", master), "http://100.117.115.44:19870")
        with self.assertRaises(ValueError):
            verify.web_url("example.com:19870", master)

    def test_hadoop_35_health_metrics_are_required_and_zero(self):
        responses = {
            "NameNodeInfo": {"LiveNodes": json.dumps({}), "NumberOfMissingBlocks": 0},
            "FSNamesystem": {
                "LowRedundancyBlocks": 0,
                "PendingReconstructionBlocks": 0,
                "CorruptBlocks": 0,
            },
        }

        def fetch(url):
            name = "NameNodeInfo" if "NameNodeInfo" in url else "FSNamesystem"
            return {"beans": [responses[name]]}

        info, health = verify.namenode_health("http://100.70.0.1:19870", fetch)
        self.assertIn("LiveNodes", info)
        self.assertEqual(set(health.values()), {0})

        for bean, metric in (
            ("NameNodeInfo", "NumberOfMissingBlocks"),
            ("FSNamesystem", "LowRedundancyBlocks"),
            ("FSNamesystem", "PendingReconstructionBlocks"),
            ("FSNamesystem", "CorruptBlocks"),
        ):
            with self.subTest(missing=metric):
                saved = responses[bean].pop(metric)
                with self.assertRaisesRegex(RuntimeError, metric):
                    verify.namenode_health("http://100.70.0.1:19870", fetch)
                responses[bean][metric] = saved

    def test_positive_health_metric_is_rejected(self):
        def fetch(url):
            if "NameNodeInfo" in url:
                return {"beans": [{"NumberOfMissingBlocks": 0}]}
            return {"beans": [{"LowRedundancyBlocks": 1,
                                "PendingReconstructionBlocks": 0,
                                "CorruptBlocks": 0}]}

        with self.assertRaisesRegex(RuntimeError, "low_redundancy_blocks"):
            verify.namenode_health("http://100.70.0.1:19870", fetch)

    def test_replica_verification_requires_two_live_copies_for_every_block(self):
        healthy = "0. blk_1 len=1 Live_repl=2\n1. blk_2 len=1 Live_repl=2\nStatus: HEALTHY\n"
        self.assertEqual(verify.verified_replicas(healthy, "Spark output"), [2, 2])
        for report in (
            "Status: HEALTHY\n",
            "0. blk_1 len=1 Live_repl=1\nStatus: HEALTHY\n",
            "0. blk_1 len=1 Live_repl=2\nStatus: CORRUPT\n",
        ):
            with self.subTest(report=report), self.assertRaises(RuntimeError):
                verify.verified_replicas(report, "Spark output")


if __name__ == "__main__":
    unittest.main()
