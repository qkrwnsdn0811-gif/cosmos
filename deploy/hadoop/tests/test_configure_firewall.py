"""Pure UFW policy tests; no host firewall or Tailscale setting is changed."""

import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "configure-firewall.py"
SPEC = importlib.util.spec_from_file_location("configure_firewall", SCRIPT)
firewall = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(firewall)


def inventory(retiring=False):
    result = {
        "master": {"name": "cosmos-master", "tailscale_ip": "100.70.0.1"},
        "workers": [
            {"name": f"cosmos-worker-{i}", "tailscale_ip": f"100.70.0.{i + 1}"}
            for i in range(1, 4)
        ],
    }
    if retiring:
        result["retiring_workers"] = [
            {"name": f"cosmos-retiring-{i}", "tailscale_ip": f"100.71.0.{i}"}
            for i in range(1, 4)
        ]
    return result


class FirewallTests(unittest.TestCase):
    def test_master_migration_opens_only_cluster_ports_to_exact_peers(self):
        rules = firewall.desired_rules(inventory(), "cosmos-master", "migration")
        self.assertEqual({rule["peer"] for rule in rules}, {"100.70.0.2", "100.70.0.3", "100.70.0.4"})
        self.assertEqual({rule["port"] for rule in rules}, firewall.MASTER_PORTS | firewall.DN_PORTS)
        self.assertTrue(all(command[4] == "tailscale0" for command in map(firewall.allow_command, rules)))
        self.assertTrue(all("0.0.0.0" not in rule["comment"] for rule in rules))

    def test_account_transition_allows_six_peer_data_nodes(self):
        rules = firewall.desired_rules(inventory(retiring=True), "cosmos-master", "account-transition")
        self.assertEqual(len({rule["peer"] for rule in rules}), 6)
        self.assertEqual({rule["port"] for rule in rules}, firewall.MASTER_PORTS)
        worker_rules = firewall.desired_rules(inventory(retiring=True), "cosmos-worker-1", "account-transition")
        self.assertEqual(len({rule["peer"] for rule in worker_rules}), 6)
        self.assertEqual({rule["port"] for rule in worker_rules}, firewall.WORKER_PORTS)

    def test_final_removes_retiring_workers_from_rules_and_health_checks(self):
        raw = inventory(retiring=True)
        retiring_ips = {node["tailscale_ip"] for node in raw["retiring_workers"]}
        master_rules = firewall.desired_rules(raw, "cosmos-master", "final")
        worker_rules = firewall.desired_rules(raw, "cosmos-worker-1", "final")
        self.assertEqual({rule["peer"] for rule in master_rules},
                         {node["tailscale_ip"] for node in raw["workers"]})
        self.assertEqual({rule["peer"] for rule in worker_rules},
                         {raw["master"]["tailscale_ip"], raw["workers"][1]["tailscale_ip"],
                          raw["workers"][2]["tailscale_ip"]})
        self.assertTrue(retiring_ips.isdisjoint({rule["peer"] for rule in master_rules + worker_rules}))
        _, _, checked_nodes = firewall.topology(raw, "cosmos-master", "final")
        self.assertEqual({node["name"] for node in checked_nodes},
                         {"cosmos-master", "cosmos-worker-1", "cosmos-worker-2", "cosmos-worker-3"})

    def test_actual_ufw_format_is_parsed_and_transport_detected(self):
        output = """Status: active

     To                         Action      From
     --                         ------      ----
[ 1] 22/tcp                     ALLOW IN    121.178.98.25
[ 2] 41641/udp                  ALLOW IN    Anywhere                   # Tailscale encrypted transport
[ 3] 41641/udp (v6)             ALLOW IN    Anywhere (v6)              # Tailscale encrypted transport
"""
        rules = firewall.parse_status(output)
        self.assertEqual(len(rules), 3)
        self.assertTrue(firewall.transport_allowed(rules))

    def test_broad_cluster_port_allow_is_refused(self):
        output = """Status: active

     To                         Action      From
     --                         ------      ----
[ 1] 22/tcp                     ALLOW IN    121.178.98.25
[ 2] 9000/tcp                   ALLOW IN    Anywhere
[ 3] 41641/udp                  ALLOW IN    Anywhere
"""
        rules = firewall.parse_status(output)
        desired = firewall.desired_rules(inventory(), "cosmos-master", "migration")
        with self.assertRaisesRegex(ValueError, "outside an exact peer"):
            firewall.inspect_rules(rules, desired, "cosmos-master", ssh_connection="121.178.98.25 50000 10.0.0.1 22")

    def test_listener_rejects_public_cluster_binding(self):
        with self.assertRaisesRegex(ValueError, "not bound"):
            firewall.listener_report("LISTEN 0 128 0.0.0.0:19870 0.0.0.0:*\n", inventory()["master"], True)
        accepted = firewall.listener_report("LISTEN 0 128 0.0.0.0:9000 0.0.0.0:*\n", inventory()["master"], True)
        self.assertEqual(accepted[0]["binding"], "loopback-compatible RPC; peer firewall required")


if __name__ == "__main__":
    unittest.main()
