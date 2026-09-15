"""Opt-in distributed/reference parity: RDB_LOADER_SPARK_TESTS=1.

Requires a working local Spark/JDK installation. For Windows set JAVA_HOME,
PYSPARK_PYTHON and PYSPARK_DRIVER_PYTHON in the test process environment.
"""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from AI.rdb_loader.aggregate import aggregate_records, utc_timestamp
from AI.rdb_loader.tests.test_aggregate import AS_OF, A, B, feature


@unittest.skipUnless(os.environ.get("RDB_LOADER_SPARK_TESTS") == "1", "Spark integration is opt-in")
class SparkAggregateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession

        cls.spark = (SparkSession.builder.master("local[2]").appName("rdb-loader-parity-test")
                     .config("spark.ui.enabled", "false")
                     .config("spark.ui.showConsoleProgress", "false")
                     .config("spark.sql.shuffle.partitions", "2")
                     .config("spark.sql.session.timeZone", "UTC").getOrCreate())
        cls.spark.sparkContext.setLogLevel("ERROR")

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def frame(self, records):
        from pyspark.sql import types as T

        fields = ["source_company_id", "target_company_id", "relationship_type", "document_id",
                  "document_type", "score", "published_at", "confidence", "impact_direction"]
        schema = T.StructType([T.StructField(name, T.StringType(), True) for name in fields])
        return self.spark.createDataFrame([
            {name: None if row.get(name) is None else str(row[name]) for name in fields}
            for row in records
        ], schema)

    @staticmethod
    def comparable(records):
        rows = []
        for record in records:
            normalized = dict(record)
            for name in ["as_of_at", "period_start", "period_end"]:
                normalized[name] = utc_timestamp(normalized[name])
            rows.append(normalized)
        return sorted(rows, key=lambda row: (row["source_company_id"], row["target_company_id"],
                                            row["relationship_type"], row["window_type"]))

    def test_distributed_scores_dates_nulls_and_dedup_match_reference(self):
        from pyspark.sql import functions as F
        from AI.rdb_loader.spark_aggregate import aggregate_dataframe

        records = [
            feature(1, score="0.0000015", confidence="0.4", impact_direction="POSITIVE"),
            feature(2, score="0.000001", confidence="0.8", impact_direction="NEGATIVE"),
            feature(3, score="0.000003", document_type="DISCLOSURE"),
            feature(4, days=7, score=10), feature(5, days=30, score=20),
            feature(6, days=90, score=30), feature(7, days=91, score=100),
            feature(8, days=0, score=100), feature(9, days=-1, score=100),
            feature(10, days=20, score=0, relationship_type="PARTNER"),
            feature(10, days=20, score=0, relationship_type="PARTNER",
                    source_company_id=B, target_company_id=A),
        ]
        records.append(dict(records[0]))
        expected = self.comparable(aggregate_records(records, as_of_at=AS_OF))
        # Include a timestamp Parquet-style schema, which Spark exposes to
        # Python workers as timezone-free datetime values unless normalized.
        source = self.frame(records)
        for timestamp_column in (False, True):
            with self.subTest(timestamp_column=timestamp_column):
                frame = source.withColumn("published_at", F.to_timestamp("published_at")) if timestamp_column else source
                result = aggregate_dataframe(self.spark, frame, as_of_at=AS_OF)
                try:
                    actual = self.comparable([row.asDict() for row in result.collect()])
                    self.assertEqual(actual, expected)
                finally:
                    result.unpersist()

    def test_conflicting_duplicate_is_rejected(self):
        from AI.rdb_loader.spark_aggregate import aggregate_dataframe

        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            aggregate_dataframe(self.spark, self.frame([feature(score=60), feature(score=70)]), as_of_at=AS_OF)

    def test_many_documents_do_not_double_round_decimal_means(self):
        from AI.rdb_loader.spark_aggregate import aggregate_dataframe

        records = [feature(index + 1, score="0.000001" if index < 5000 else "0",
                           confidence="0.000001" if index < 5000 else "0") for index in range(10001)]
        result = aggregate_dataframe(self.spark, self.frame(records), as_of_at=AS_OF)
        try:
            self.assertEqual(self.comparable([row.asDict() for row in result.collect()]),
                             self.comparable(aggregate_records(records, as_of_at=AS_OF)))
        finally:
            result.unpersist()

    def test_manifest_hash_streams_actual_file_bytes(self):
        from AI.rdb_loader.spark_aggregate import _sha256_file

        payload = bytes(range(256)) * 9000  # Crosses more than one 1 MiB chunk.
        with tempfile.TemporaryDirectory(prefix="spark-sha-") as directory:
            path = Path(directory) / "part.parquet"
            path.write_bytes(payload)
            jpath = self.spark.sparkContext._jvm.org.apache.hadoop.fs.Path(path.as_uri())
            fs = jpath.getFileSystem(self.spark.sparkContext._jsc.hadoopConfiguration())
            self.assertEqual(_sha256_file(self.spark, fs, jpath), hashlib.sha256(payload).hexdigest())

    def test_manifest_utf8_uses_actual_jvm_output_stream(self):
        from AI.rdb_loader.spark_aggregate import _write_text

        jvm = self.spark.sparkContext._jvm

        class JvmFileSystem:
            # FileOutputStream exercises Py4J's real byte[] conversion without
            # requiring Windows-only winutils to create local Hadoop files.
            def create(self, path, overwrite):
                self.asserted_overwrite = overwrite
                return jvm.java.io.FileOutputStream(jvm.java.io.File(path.toUri()))

        with tempfile.TemporaryDirectory(prefix="spark-manifest-") as directory:
            path = Path(directory) / "manifest.json"
            payload = '{"model_version": "뉴스·공시-v1", "record_count": 3}\n'
            fs = JvmFileSystem()
            _write_text(fs, jvm, path.as_uri(), payload)
            self.assertFalse(fs.asserted_overwrite)
            self.assertEqual(path.read_bytes(), payload.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
