#!/usr/bin/env python3
"""Run through spark-submit --master yarn --deploy-mode client, never local mode."""

import argparse
import json
import os
import socket

from pyspark import BarrierTaskContext
from pyspark.sql import SparkSession


def inspect_partition(records):
    context = BarrierTaskContext.get()
    context.barrier()  # All three single-slot executors must participate together.
    count = 0
    total = 0
    for record in records:
        total += int(record.split(",", 1)[0])
        count += 1
    yield {
        "partition": context.partitionId(),
        "hostname": socket.gethostname(),
        "fqdn": socket.getfqdn(),
        "node_manager_host": os.environ.get("NM_HOST", ""),
        "spark_local_ip": os.environ.get("SPARK_LOCAL_IP", ""),
        "rows": count,
        "sum": total,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--rows", required=True, type=int)
    parser.add_argument("--expected-workers-json", required=True)
    args = parser.parse_args()
    expected = json.loads(args.expected_workers_json)
    spark = SparkSession.builder.appName("COSMOS distributed verification").getOrCreate()
    try:
        context = spark.sparkContext
        if context.master != "yarn" or not context.applicationId.startswith("application_"):
            raise RuntimeError("This verification must run in YARN, not local or standalone Spark")
        if spark.version != "4.2.0":
            raise RuntimeError(f"Expected Spark 4.2.0, found {spark.version}")
        rows = (context.textFile(args.input, minPartitions=3)
                .repartition(3).barrier().mapPartitions(inspect_partition).collect())
        observed = set()
        for row in rows:
            names = {row[key] for key in ("hostname", "fqdn", "node_manager_host", "spark_local_ip") if row[key]}
            names.update(name.split(".", 1)[0] for name in list(names))
            matches = [worker["name"] for worker in expected
                       if worker["name"] in names or worker["tailscale_ip"] in names]
            if len(matches) != 1:
                raise RuntimeError(f"Executor cannot be matched to one expected Worker: {row}")
            row["worker"] = matches[0]
            observed.add(matches[0])
        if len(rows) != 3 or observed != {worker["name"] for worker in expected}:
            raise RuntimeError(f"All three distinct Workers must execute a task; observed {rows}")
        if any(row["rows"] == 0 for row in rows):
            raise RuntimeError("Each Worker must process actual HDFS records")
        if sum(row["rows"] for row in rows) != args.rows:
            raise RuntimeError("Distributed record count does not match the input")
        if sum(row["sum"] for row in rows) != args.rows * (args.rows - 1) // 2:
            raise RuntimeError("Distributed aggregation result is incorrect")
        # Error-if-exists is deliberate: a verification never replaces existing data.
        spark.createDataFrame(rows).write.mode("error").json(args.output)
        if spark.read.json(args.output).count() != 3:
            raise RuntimeError("Spark HDFS output could not be read back")
        print("COSMOS_SPARK_VERIFY " + json.dumps({
            "application_id": context.applicationId,
            "spark_version": spark.version,
            "workers": sorted(observed),
            "tasks": rows,
            "hdfs_output": args.output,
        }, sort_keys=True), flush=True)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
