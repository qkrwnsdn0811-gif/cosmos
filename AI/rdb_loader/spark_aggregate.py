"""spark-submit producer: normalized evidence Parquet -> loader snapshot.

Example (the output must be a new, immutable run directory)::

    spark-submit AI/rdb_loader/spark_aggregate.py \
      --input hdfs://namenode:9000/features/run_id=.../data \
      --output hdfs://namenode:9000/relationship-aggregates/run_id=... \
      --snapshot-id 10000000-0000-0000-0000-000000000001 \
      --as-of-at 2026-09-15T00:00:00Z --model-version relation-features-v1

The root _SUCCESS is written only after Parquet, row counts and SHA256 manifest
are complete. Spark's data/_SUCCESS alone never authorizes publication.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

try:
    from .aggregate import (DOCUMENT_KEY_FIELDS, FEATURE_FIELDS, FORMULA_VERSION,
                            RELATIONSHIP_FIELDS, WINDOWS, normalize_feature, utc_timestamp)
except ImportError:  # spark-submit runs this file directly.
    from aggregate import (DOCUMENT_KEY_FIELDS, FEATURE_FIELDS, FORMULA_VERSION,
                           RELATIONSHIP_FIELDS, WINDOWS, normalize_feature, utc_timestamp)


def aggregate_dataframe(spark, frame, *, as_of_at):
    """Validate distributed evidence, deduplicate, and aggregate without collect."""
    from pyspark.sql import functions as F, types as T

    spark.conf.set("spark.sql.session.timeZone", "UTC")
    cutoff = utc_timestamp(as_of_at)
    required = set(FEATURE_FIELDS[:-2])
    if missing := required - set(frame.columns):
        raise ValueError(f"Missing feature fields: {', '.join(sorted(missing))}")
    for name in ("confidence", "impact_direction"):
        if name not in frame.columns:
            frame = frame.withColumn(name, F.lit(None))
    # Spark timestamps become naive Python datetimes in an RDD. Format them in
    # the UTC Spark session before the shared strict timestamp validator runs.
    if isinstance(frame.schema["published_at"].dataType, T.TimestampType):
        frame = frame.withColumn("published_at", F.date_format("published_at", "yyyy-MM-dd'T'HH:mm:ss.SSSSSSXXX"))
    decimal = T.DecimalType(18, 6)
    schema = T.StructType([
        T.StructField(name, T.TimestampType() if name == "published_at" else
                      decimal if name in {"score", "confidence"} else T.StringType(),
                      nullable=name in {"confidence", "impact_direction"})
        for name in FEATURE_FIELDS
    ])
    normalized = spark.createDataFrame(frame.select(*FEATURE_FIELDS).rdd.map(
        lambda row: normalize_feature(row.asDict())), schema).cache()
    try:
        # Validate all rows before filtering dates, and reject conflicting input
        # rather than selecting an arbitrary duplicate by partition order.
        normalized.count()
        identity = normalized.groupBy("document_id").agg(
            F.countDistinct(F.struct("document_type", "published_at")).alias("variants"))
        if identity.filter(F.col("variants") > 1).limit(1).count():
            raise ValueError("Conflicting document identity in feature input")
        duplicates = normalized.groupBy(*DOCUMENT_KEY_FIELDS).agg(
            F.countDistinct(F.struct(*FEATURE_FIELDS)).alias("variants"))
        if duplicates.filter(F.col("variants") > 1).limit(1).count():
            raise ValueError("Conflicting duplicate document/relationship feature")
        evidence = normalized.dropDuplicates(list(DOCUMENT_KEY_FIELDS)).filter(
            (F.col("published_at") >= F.lit(cutoff - timedelta(days=90))) &
            (F.col("published_at") < F.lit(cutoff)))
        windows = spark.createDataFrame([
            (name, cutoff - timedelta(days=days), cutoff) for name, days in WINDOWS.items()
        ], "window_type string, period_start timestamp, period_end timestamp")
        grid = evidence.select(*RELATIONSHIP_FIELDS).distinct().crossJoin(F.broadcast(windows))
        expanded = evidence.crossJoin(F.broadcast(windows)).filter(F.col("published_at") >= F.col("period_start"))
        keys = [*RELATIONSHIP_FIELDS, "window_type", "period_start", "period_end"]
        # Decimal avg adds only four fractional places. Rounding that result
        # again to six can cross a half-unit boundary for 10,000+ documents.
        # Sum integer micro-units and round the exact quotient/remainder once.
        news = F.when(F.col("document_type") == "NEWS", F.col("score"))
        disclosure = F.when(F.col("document_type") == "DISCLOSURE", F.col("score"))
        def units(value):
            return (value * 1000000).cast(T.DecimalType(38, 0))

        means = expanded.groupBy(*keys).agg(
            F.sum(units(news)).alias("_news_units"), F.count(news).alias("_news_count"),
            F.sum(units(disclosure)).alias("_disclosure_units"), F.count(disclosure).alias("_disclosure_count"),
            F.sum(units(F.col("confidence"))).alias("_confidence_units"), F.count("confidence").alias("_confidence_count"),
            F.count("document_id").alias("evidence_count"),
            F.collect_set("impact_direction").alias("directions"))
        from decimal import Decimal

        for prefix, name in (("news", "news_score"), ("disclosure", "disclosure_score"), ("confidence", "confidence")):
            total = F.col(f"_{prefix}_units")
            count = F.col(f"_{prefix}_count")
            divisor = F.when(count > 0, count).cast(T.DecimalType(20, 0))
            remainder = F.pmod(total, divisor)
            integral = ((total - remainder) / divisor).cast(T.DecimalType(18, 0))
            rounded = integral + F.when(remainder * 2 >= divisor, 1).otherwise(0)
            means = means.withColumn(name, (rounded * F.lit(Decimal("0.000001"))).cast(decimal))
            means = means.drop(f"_{prefix}_units", f"_{prefix}_count")
        result = (grid.join(means, keys, "left")
                  .withColumn("evidence_count", F.coalesce(F.col("evidence_count"), F.lit(0)))
                  .withColumn("impact_direction", F.when(F.size("directions") == 1, F.element_at("directions", 1)))
                  .drop("directions")
                  .withColumn("score", F.round(F.coalesce(
                      (F.col("news_score") + F.col("disclosure_score")) / 2,
                      F.col("news_score"), F.col("disclosure_score")), 6).cast(decimal))
                  .withColumn("as_of_at", F.lit(cutoff))
                  .withColumn("formula_version", F.lit(FORMULA_VERSION)))
        # Ordinary Spark Parquet timestamps carry no timezone metadata. Keep
        # explicit UTC strings so a PyArrow reader cannot interpret them in the
        # loader host's local timezone or reject them as ambiguous timestamps.
        for name in ("period_start", "period_end", "as_of_at"):
            result = result.withColumn(name, F.date_format(name, "yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'"))
        # Materialize before releasing validated input; no driver-size bound.
        result = result.cache()
        result.count()
        return result
    finally:
        normalized.unpersist()


def _write_text(fs, jvm, destination: str, text: str) -> None:
    stream = fs.create(jvm.org.apache.hadoop.fs.Path(destination), False)
    try:
        stream.write(bytearray(text.encode("utf-8")))
    finally:
        stream.close()


def _sha256_file(spark, fs, path) -> str:
    """Hash through JVM streaming I/O; never bring a Parquet part into Python."""
    jvm = spark.sparkContext._jvm
    digest = jvm.java.security.MessageDigest.getInstance("SHA-256")
    stream = jvm.java.security.DigestInputStream(fs.open(path), digest)
    buffer = spark.sparkContext._gateway.new_array(jvm.byte, 1024 * 1024)
    try:
        while stream.read(buffer) != -1:
            pass
    finally:
        stream.close()
    return "".join(f"{int(value) & 0xff:02x}" for value in digest.digest())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="Normalized evidence feature Parquet path")
    ap.add_argument("--output", required=True, help="New immutable HDFS snapshot root")
    ap.add_argument("--snapshot-id", required=True, type=UUID)
    ap.add_argument("--as-of-at", required=True, type=utc_timestamp)
    ap.add_argument("--model-version", required=True)
    args = ap.parse_args()
    if not args.model_version.strip() or len(args.model_version) > 50:
        ap.error("--model-version must contain 1..50 characters")

    from pyspark.sql import SparkSession

    spark = (SparkSession.builder.appName("relationship-aggregate-rdb-v1")
             .config("spark.sql.session.timeZone", "UTC").getOrCreate())
    try:
        spark.sparkContext.addPyFile(str(Path(__file__).with_name("aggregate.py")))
        jvm = spark.sparkContext._jvm
        output_path = jvm.org.apache.hadoop.fs.Path(args.output)
        fs = output_path.getFileSystem(spark.sparkContext._jsc.hadoopConfiguration())
        root = fs.makeQualified(output_path).toString().rstrip("/")
        uri = urlsplit(root)
        if uri.scheme != "hdfs" or not uri.netloc or uri.path in {"", "/"}:
            raise ValueError("--output must resolve to an absolute HDFS snapshot directory")
        if fs.exists(output_path):
            raise ValueError(f"Output already exists; use a new run directory: {root}")
        result = aggregate_dataframe(spark, spark.read.parquet(args.input), as_of_at=args.as_of_at)
        result.write.mode("errorifexists").parquet(f"{root}/data")
        # Verify persisted output, not only the producer's in-memory result.
        saved = spark.read.parquet(f"{root}/data")
        counts = {name: 0 for name in WINDOWS}
        counts.update({row["window_type"]: row["count"] for row in saved.groupBy("window_type").count().collect()})
        files = []
        iterator = fs.listFiles(jvm.org.apache.hadoop.fs.Path(f"{root}/data"), True)
        while iterator.hasNext():
            path = iterator.next().getPath()
            if path.getName().endswith(".parquet"):
                relative = path.toString()[len(root) + 1:]
                files.append({"path": relative, "sha256": _sha256_file(spark, fs, path)})
        manifest = {
            "schema_version": 1, "status": "SUCCEEDED", "snapshot_mode": "FULL",
            "snapshot_id": str(args.snapshot_id), "as_of_at": args.as_of_at.isoformat(),
            "formula_version": FORMULA_VERSION, "model_version": args.model_version,
            "hdfs_uri": root, "windows": list(WINDOWS),
            "record_count": sum(counts.values()), "window_counts": counts,
            "files": sorted(files, key=lambda item: item["path"]),
        }
        _write_text(fs, jvm, f"{root}/manifest.json", json.dumps(manifest, indent=2) + "\n")
        _write_text(fs, jvm, f"{root}/_SUCCESS", "")
        print(json.dumps(manifest, indent=2))
        result.unpersist()
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
