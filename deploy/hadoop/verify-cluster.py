#!/usr/bin/env python3
"""Verify the live cluster and retain all evidence in a new, unique HDFS directory."""

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET


def settings(directory, name):
    tree = ET.parse(directory / name)
    return {item.findtext("name"): item.findtext("value") for item in tree.findall("property")}


def get_json(url):
    # Cluster management traffic must not be sent through a workstation HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=15) as response:
        return json.load(response)


def jmx_bean(base_url, name, fetch=None):
    fetch = fetch or get_json
    query = urllib.parse.urlencode({"qry": f"Hadoop:service=NameNode,name={name}"})
    beans = fetch(f"{base_url}/jmx?{query}").get("beans", [])
    if len(beans) != 1:
        raise RuntimeError(f"NameNode {name} bean is missing or ambiguous")
    return beans[0]


def required_metric(bean, name):
    if name not in bean:
        raise RuntimeError(f"Required NameNode metric is missing: {name}")
    try:
        return int(bean[name])
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"Required NameNode metric is not an integer: {name}") from error


def namenode_health(base_url, fetch=None):
    info = jmx_bean(base_url, "NameNodeInfo", fetch)
    metrics = jmx_bean(base_url, "FSNamesystem", fetch)
    health = {
        "missing_blocks": required_metric(info, "NumberOfMissingBlocks"),
        "low_redundancy_blocks": required_metric(metrics, "LowRedundancyBlocks"),
        "pending_reconstruction_blocks": required_metric(metrics, "PendingReconstructionBlocks"),
        "corrupt_blocks": required_metric(metrics, "CorruptBlocks"),
    }
    unhealthy = {name: value for name, value in health.items() if value != 0}
    if unhealthy:
        raise RuntimeError(f"NameNode block health is not clean: {unhealthy}")
    return info, health


def verified_replicas(fsck, label):
    if not re.search(r"is HEALTHY|Status:\s*HEALTHY", fsck):
        raise RuntimeError(f"{label} is not healthy in HDFS")
    replicas = [int(value) for value in re.findall(r"Live_repl=(\d+)", fsck)]
    if not replicas or any(value != 2 for value in replicas):
        raise RuntimeError(f"{label} blocks do not have precisely two live replicas")
    return replicas


def run(command, env, timeout=180):
    result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n"
                           f"{result.stdout[-4000:]}\n{result.stderr[-4000:]}")
    return result.stdout


def identity(host):
    return host.strip().removeprefix("/").split(":", 1)[0].rstrip(".")


def match_workers(host_sets, workers):
    matched = []
    for hosts in host_sets:
        aliases = {identity(host) for host in hosts if host}
        aliases.update(host.split(".", 1)[0] for host in list(aliases))
        matches = [worker["name"] for worker in workers
                   if worker["name"] in aliases or worker["tailscale_ip"] in aliases]
        if len(matches) != 1:
            raise RuntimeError(f"Live node does not match one expected Worker: {sorted(aliases)}")
        matched.extend(matches)
    if len(matched) != 3 or set(matched) != {worker["name"] for worker in workers}:
        raise RuntimeError(f"Expected precisely three distinct live Workers; got {matched}")
    return sorted(matched)


def web_url(address, master):
    host, separator, port = address.rpartition(":")
    if not separator or not port.isdecimal():
        raise ValueError(f"Invalid cluster HTTP address: {address}")
    if host in ("0.0.0.0", "localhost", "127.0.0.1"):
        host = master["tailscale_ip"]
    if host not in (master["name"], master["tailscale_ip"]):
        raise ValueError("Management address must identify the inventory Master")
    return f"http://{host}:{port}"


def verify(args, report):
    inventory = json.loads(args.inventory.read_text())
    workers = inventory["workers"]
    if len(workers) != 3 or len({worker["name"] for worker in workers}) != 3:
        raise ValueError("Inventory must contain exactly three distinct Workers")
    for node in [inventory["master"], *workers]:
        if ipaddress.ip_address(node["tailscale_ip"]) not in ipaddress.ip_network("100.64.0.0/10"):
            raise ValueError("Inventory cluster addresses must be Tailscale IPv4 addresses")
    if len({worker["tailscale_ip"] for worker in workers}) != 3:
        raise ValueError("Workers must have distinct Tailscale IPs")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", inventory.get("user", "ubuntu")):
        raise ValueError("Invalid Hadoop user")
    conf = args.hadoop_home / "etc/hadoop"
    hdfs_site = settings(conf, "hdfs-site.xml")
    yarn_site = settings(conf, "yarn-site.xml")
    core_site = settings(conf, "core-site.xml")
    filesystem = core_site["fs.defaultFS"].rstrip("/")
    if not filesystem.startswith("hdfs://"):
        raise ValueError("fs.defaultFS must reference HDFS")
    hadoop_env = dict(os.environ, JAVA_HOME=inventory.get("java17_home", "/usr/lib/jvm/java-17-openjdk-amd64"),
                      HADOOP_HOME=str(args.hadoop_home), HADOOP_CONF_DIR=str(conf))
    spark_env = dict(hadoop_env, JAVA_HOME=inventory.get("java21_home", "/usr/lib/jvm/java-21-openjdk-amd64"),
                     SPARK_HOME=str(args.spark_home), PYSPARK_PYTHON="/usr/bin/python3")
    hdfs = str(args.hadoop_home / "bin/hdfs")
    namenode = web_url(hdfs_site["dfs.namenode.http-address"], inventory["master"])
    rm = web_url(yarn_site["yarn.resourcemanager.webapp.address"], inventory["master"])
    info, health = namenode_health(namenode)
    report["hdfs_health_before_spark"] = health
    live = json.loads(info["LiveNodes"])
    report["live_datanodes"] = match_workers(
        [[name, item.get("xferaddr", ""), item.get("infoAddr", "")] for name, item in live.items()], workers)
    for name, item in live.items():
        if item.get("adminState") not in (None, "In Service"):
            raise RuntimeError(f"DataNode {name} is not in normal service")
    node_response = get_json(f"{rm}/ws/v1/cluster/nodes?states=RUNNING")
    running = (node_response.get("nodes") or {}).get("node", [])
    report["live_nodemanagers"] = match_workers([[node["nodeHostName"]] for node in running], workers)
    report["datanode_report"] = run([hdfs, "dfsadmin", "-report", "-live"], hadoop_env)

    path = "/cosmos-verification/" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex
    report["hdfs_path"] = filesystem + path
    rows = 100000
    with tempfile.TemporaryDirectory(prefix="cosmos-verify-") as temp:
        temp_path = Path(temp)
        source = temp_path / "input.csv"
        with source.open("w") as handle:
            for number in range(rows):
                handle.write(f"{number},{hashlib.sha256(str(number).encode()).hexdigest()}\n")
        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        run([hdfs, "dfs", "-mkdir", "-p", "/cosmos-verification"], hadoop_env)
        run([hdfs, "dfs", "-mkdir", path], hadoop_env)
        remote = path + "/input.csv"
        run([hdfs, "dfs", "-Ddfs.replication=2", "-Ddfs.blocksize=1048576", "-put", str(source), remote], hadoop_env)
        run([hdfs, "dfs", "-setrep", "-w", "2", remote], hadoop_env)
        readback = temp_path / "readback.csv"
        run([hdfs, "dfs", "-get", remote, str(readback)], hadoop_env)
        if hashlib.sha256(readback.read_bytes()).hexdigest() != original_hash:
            raise RuntimeError("HDFS readback SHA256 differs from the original file")
        report["input_sha256"] = original_hash
        report["hdfs_checksum"] = run([hdfs, "dfs", "-checksum", remote], hadoop_env).strip()
        replication = run([hdfs, "dfs", "-stat", "%r", remote], hadoop_env).strip()
        if replication != "2":
            raise RuntimeError(f"HDFS requested replication is {replication}, expected 2")
        fsck = run([hdfs, "fsck", remote, "-files", "-blocks", "-locations"], hadoop_env)
        replicas = verified_replicas(fsck, "Verification input")
        report["input_fsck"] = fsck
        report["actual_block_replication"] = replicas
        command = [str(args.spark_home / "bin/spark-submit"), "--master", "yarn", "--deploy-mode", "client",
                   "--num-executors", "3", "--executor-cores", "1", "--executor-memory", "2816m",
                   "--driver-memory", "1g", "--conf", "spark.executor.memoryOverhead=512m",
                   "--conf", "spark.dynamicAllocation.enabled=false", "--conf", "spark.speculation=false",
                   "--conf", "spark.task.cpus=1", "--conf", "spark.scheduler.barrier.maxConcurrentTasksCheck.maxFailures=3",
                   "--conf", "spark.scheduler.barrier.maxConcurrentTasksCheck.interval=5s",
                   "--conf", "spark.yarn.appMasterEnv.JAVA_HOME=" + spark_env["JAVA_HOME"],
                   "--conf", "spark.executorEnv.JAVA_HOME=" + spark_env["JAVA_HOME"],
                   "--conf", "spark.executorEnv.PYSPARK_PYTHON=/usr/bin/python3",
                   str(Path(__file__).with_name("spark-verify.py")),
                   "--input", filesystem + remote, "--output", filesystem + path + "/spark-output",
                   "--rows", str(rows), "--expected-workers-json", json.dumps(workers)]
        output = run(command, spark_env, timeout=args.spark_timeout)
        lines = [line.removeprefix("COSMOS_SPARK_VERIFY ") for line in output.splitlines()
                 if line.startswith("COSMOS_SPARK_VERIFY ")]
        if len(lines) != 1:
            raise RuntimeError("Spark verification did not return a unique result")
        report["spark"] = json.loads(lines[0])
        spark_output = path + "/spark-output"
        output_fsck = run([hdfs, "fsck", spark_output, "-files", "-blocks", "-locations"], hadoop_env)
        report["spark_output_fsck"] = output_fsck
        report["spark_output_actual_block_replication"] = verified_replicas(output_fsck, "Spark output")
        _, health = namenode_health(namenode)
        report["hdfs_health_after_spark"] = health
        final_fsck = run([hdfs, "fsck", path, "-files", "-blocks", "-locations"], hadoop_env)
        if not re.search(r"is HEALTHY|Status:\s*HEALTHY", final_fsck):
            raise RuntimeError("Final verification output is not healthy in HDFS")
        report["final_fsck"] = final_fsck


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New local JSON evidence file; never overwritten")
    parser.add_argument("--hadoop-home", type=Path, default=Path("/opt/hadoop"))
    parser.add_argument("--spark-home", type=Path, default=Path("/opt/spark"))
    parser.add_argument("--spark-timeout", type=int, default=600)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output evidence file already exists; select a new filename")
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "success": False}
    code = 1
    try:
        verify(args, report)
        report["success"] = True
        code = 0
    except Exception as error:
        report["error"] = str(error)
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        print(json.dumps({"success": report["success"], "evidence": str(args.output),
                          "hdfs_path": report.get("hdfs_path"), "error": report.get("error")}, ensure_ascii=False))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
