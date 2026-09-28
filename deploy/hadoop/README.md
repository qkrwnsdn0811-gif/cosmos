# COSMOS Hadoop cluster operations

These scripts build and verify the COSMOS HDFS/YARN cluster without formatting an existing NameNode. Keep the real inventory outside Git and pass it with `--inventory`.

## Roles and modes

- `cosmos-master`: existing SSAFY EC2, NameNode, SecondaryNameNode, ResourceManager and Spark driver.
- `cosmos-worker-1..3`: AWS DataNode, NodeManager and Spark executor hosts.
- `migration`: temporarily runs the preserved Master DataNode beside the three Workers.
- `final`: gracefully excludes the Master DataNode and leaves exactly three Workers.
- `account-transition`: overlaps three replacement Workers with three `retiring_workers`. A later `final` render excludes the old three for graceful decommission.

Hadoop/YARN/Spark listeners use Tailscale addresses. `configure-firewall.sh` permits cluster TCP ports only from inventory peers on `tailscale0`, preserves existing management SSH, and switches Tailscale host filtering to `nodivert` only after UFW validation.

## Initial cluster transition

Run the pure tests before touching a host:

```bash
python3 -m unittest discover -s deploy/hadoop/tests -v
```

Prepare each Worker configuration and firewall, but leave its Hadoop daemons disabled and inactive until `--master-start` has preserved the existing namespace:

```bash
sudo python3 deploy/hadoop/configure-firewall.py --inventory /secure/inventory.json --node cosmos-worker-1 --mode migration --apply
sudo python3 deploy/hadoop/configure-cluster.py --inventory /secure/inventory.json --node cosmos-worker-1 --mode migration --apply
sudo systemctl daemon-reload
```

Run the first guarded phase on the Master:

```bash
sudo bash deploy/hadoop/migrate-cluster.sh --master-start --inventory /secure/inventory.json
```

Only after the Master services are healthy, start the prepared services on each of the three Workers:

```bash
sudo systemctl enable --now cosmos-hadoop-datanode cosmos-hadoop-nodemanager
```

Return to the Master for replication and finalization:

```bash
sudo bash deploy/hadoop/migrate-cluster.sh --replicate --inventory /secure/inventory.json
sudo bash deploy/hadoop/migrate-cluster.sh --finalize --inventory /secure/inventory.json
```

`--master-start` pauses the existing writer, saves the namespace, stops the old units, creates and verifies an offline metadata archive, and starts the managed Master units. `--replicate` requires exactly four healthy DataNodes before recursively requesting replication 2. `--finalize` waits for a healthy Master decommission before stopping its DataNode. None of these phases format or delete HDFS data.

The migration state and evidence remain under `/var/lib/cosmos-hadoop-migration`; configuration and NameNode backups remain under `/var/backups/cosmos-hadoop`. Copy the first offline NameNode archive to another host before final decommission.

`--finalize` installs the Master `final` configuration, refreshes the HDFS include/exclude lists, waits for the Master DataNode to become `Decommissioned`, and only then stops it. It deliberately leaves the Master firewall in `migration` mode so the DataNode ports cannot be removed during decommission. After `state.json` says `finalized`, the Master DataNode is inactive, and the whole-filesystem health checks still pass, apply and verify the `final` firewall on the Master:

```bash
sudo python3 deploy/hadoop/configure-firewall.py \
  --inventory /secure/inventory.json --node cosmos-master --mode final --apply
sudo python3 deploy/hadoop/configure-firewall.py \
  --inventory /secure/inventory.json --node cosmos-master --mode final --verify
```

Apply the `final` configuration and firewall on each Worker as well. A configuration apply never restarts a daemon; it reconciles only the managed runtime directory inodes and does not traverse NameNode or DataNode contents. Restart only when a reviewed change requires it. If migration-only NameNode replication tuning was applied dynamically, load the lower `final` values from the installed configuration and wait for reconfiguration to finish:

```bash
sudo -u ubuntu /opt/hadoop/bin/hdfs dfsadmin \
  -reconfig namenode 100.117.115.44:9000 start
sudo -u ubuntu /opt/hadoop/bin/hdfs dfsadmin \
  -reconfig namenode 100.117.115.44:9000 status
```

## Verification

After finalization, create the required service directories as the Hadoop service user and run the end-to-end verifier on the Master. The migration state directory is root-owned, so write the evidence under `/home/ubuntu` first and then install a root-owned copy:

```bash
sudo -u ubuntu /opt/hadoop/bin/hdfs dfs -mkdir -p /user/ubuntu /tmp/logs /spark-history
sudo -u ubuntu /opt/hadoop/bin/hdfs dfs -chown ubuntu:ubuntu /user/ubuntu /spark-history
sudo -u ubuntu /opt/hadoop/bin/hdfs dfs -chmod 1777 /tmp/logs

stamp=$(date -u +%Y%m%dT%H%M%SZ)
evidence="/home/ubuntu/cosmos-verification-${stamp}.json"
sudo -u ubuntu python3 /opt/cosmos/infrastructure/hadoop/verify-cluster.py \
  --inventory /etc/cosmos/hadoop/inventory.json \
  --output "$evidence"
sudo install -o root -g root -m 0644 "$evidence" /var/lib/cosmos-hadoop-migration/
```

The verifier requires precisely three live DataNodes and three running NodeManagers, checks HDFS write/read checksums and two live replicas, then runs a three-executor Spark job in YARN so every Worker processes records. Keep the JSON evidence and its unique HDFS `/cosmos-verification/...` path; a failed run also writes evidence and exits nonzero.

Reboot Workers one at a time, never as a group. After each reboot, confirm that `/data` is the expected ext4 EBS mount, Tailscale is connected, both Worker units are enabled and active, and HDFS/YARN returned to three healthy nodes before rebooting the next Worker.

## AWS lifecycle

`aws-provision.py` records the current account bootstrap. It creates encrypted gp3 disks and restricts SSH and Tailscale transport sources. Its account, VPC, subnet, AMI, profile and key-name constants describe this deployment and must be rediscovered and reviewed before it is used for another account. The 200 GiB data volumes use `DeleteOnTermination=false`; terminating an instance does not stop their cost. Delete an old data volume only after the replacement cluster passes the full verification and the team has made its retention decision.

For an account move, add the old three nodes as `retiring_workers`, render/apply `account-transition`, verify six live DataNodes, then replicate and balance. The current cluster `final` render excludes all three `retiring_workers` together. Keep the firewall in `account-transition` mode and keep all three old DataNodes running until every one reports `Decommissioned`; after stopping them, apply the firewall `final` mode to remove their peer rules and verify the new three Workers alone. A one-by-one retirement requires an explicitly reviewed staged exclude procedure; the current `final` render does not implement that staging. Never format the SSAFY NameNode or terminate old instances before missing/corrupt blocks are zero and the new three Workers pass HDFS and Spark verification.
