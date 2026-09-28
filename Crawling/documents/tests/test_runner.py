import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from services.document_loader.discovery import Candidate
from services.document_loader.runner import configuration_digest, load_config, read_state, run_once, select_pending


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.state_path = self.directory / "state.json"
        self.config = {"version":1,"roots":[{"kind":"news","glob":"hdfs://cluster/news/*"}],
                       "company_map":None,"sources":None,"register_sources":True}
        self.connection = MagicMock()
        self.connection.info.transaction_status = 0

    def tearDown(self):
        self.temp.cleanup()

    def news(self, partition, start):
        return Candidate("news", f"hdfs://cluster/news/topic=news.raw/partition={partition}/start={start:020d}")

    def state(self):
        return read_state(self.state_path, configuration_digest(self.config))

    def batch(self, kind, uri, **kwargs):
        candidate = Candidate(kind, uri)
        return {"batch_id": candidate.batch_id, "input_uri":uri, "manifest_sha256":"a"*64, "records":[]}

    def execute(self, candidates, *, receipts=None, commit=False, maximum=25, load=None, discover_errors=None):
        with patch("services.document_loader.runner.committed_receipts", return_value=receipts or {}), \
             patch("services.document_loader.runner.discover_all", return_value=(candidates, discover_errors or [])), \
             patch("services.document_loader.runner.load_source", side_effect=load or self.batch) as source, \
             patch("services.document_loader.runner.normalize_batch", return_value={"records":[],"sources":{}}), \
             patch("services.document_loader.runner.load_documents", return_value={"already_loaded":False}) as loader:
            result = run_once(self.connection, self.config, state_path=self.state_path, commit=commit, max_batches=maximum)
        return result, source, loader

    def test_default_runs_actual_loader_with_commit_false(self):
        result, _, loader = self.execute([self.news(0,1)])
        self.assertEqual(result["mode"], "dry_run")
        self.assertFalse(loader.call_args.kwargs["commit"])
        self.assertEqual(result["succeeded"], 1)

    def test_committed_receipt_is_only_fast_skip_authority(self):
        item = self.news(0,1)
        result, source, loader = self.execute([item], receipts={item.batch_id:item.input_uri})
        self.assertEqual(result["skipped_committed"], 1)
        source.assert_not_called()
        loader.assert_not_called()

    def test_dry_run_success_is_not_skipped_on_next_run(self):
        item = self.news(0,1)
        self.execute([item])
        result, source, _ = self.execute([item])
        self.assertEqual(result["attempted"], 1)
        source.assert_called_once()

    def test_commit_success_without_local_state_uses_db_receipt_after_restart(self):
        item = self.news(0,1)
        first, _, loader = self.execute([item], commit=True)
        self.assertTrue(loader.call_args.kwargs["commit"])
        self.state_path.unlink()
        second, source, _ = self.execute([item], receipts={item.batch_id:item.input_uri}, commit=True)
        self.assertEqual(second["skipped_committed"], 1)
        source.assert_not_called()

    def test_input_failure_continues_and_safe_message_is_reported(self):
        bad, good = self.news(0,10), self.news(1,20)
        def load(kind, uri, **kwargs):
            if uri == bad.input_uri:
                raise ValueError("unregistered or inactive companies: KOSPI/005930")
            return self.batch(kind, uri)
        result, _, loader = self.execute([bad,good], load=load, commit=True)
        self.assertEqual((result["failed"], result["succeeded"]), (1,1))
        self.assertEqual(loader.call_count, 1)
        failure = next(event for event in result["inputs"] if event["status"] == "failed")
        self.assertIn("KOSPI/005930", failure["message"])
        state = self.state()
        self.assertEqual(state["attempts"][bad.input_uri]["failures"], 1)
        self.assertNotIn(good.input_uri, state["attempts"])

    def test_runtime_error_text_is_not_exposed(self):
        def load(*args, **kwargs):
            raise RuntimeError("password=DO_NOT_EXPOSE")
        result, _, _ = self.execute([self.news(0,1)], load=load)
        self.assertNotIn("DO_NOT_EXPOSE", json.dumps(result))
        self.assertEqual(result["inputs"][0]["error_type"], "RuntimeError")

    def test_max_batches_and_next_run_resume_without_successful_state_flag(self):
        items = [self.news(0,n) for n in (1,2,3)]
        first, _, _ = self.execute(items, maximum=1, commit=True)
        done = first["inputs"][0]
        second, _, _ = self.execute(items, receipts={done["batch_id"]:done["input_uri"]}, maximum=1, commit=True)
        self.assertEqual(first["attempted"], 1)
        self.assertEqual(first["deferred"], 2)
        self.assertEqual(second["attempted"], 1)
        self.assertNotEqual(done["input_uri"], second["inputs"][0]["input_uri"])

    def test_latest_news_first_and_partition_round_robin(self):
        items = [self.news(p,n) for p in (0,1,2) for n in (1,5,9)]
        chosen = select_pending(items, self.state(), 6)
        self.assertEqual([(int(item.input_uri.split("partition=")[1][0]),int(item.input_uri.rsplit("=",1)[1])) for item in chosen],
                         [(0,9),(1,9),(2,9),(0,5),(1,5),(2,5)])

    def test_latest_analyzed_news_uses_same_partition_round_robin(self):
        items = [Candidate("news-analyzed",
                 f"hdfs://cluster/analyzed/topic=news.raw/partition={partition}/start={start:020d}")
                 for partition in (0, 1) for start in (1, 9)]
        chosen = select_pending(items, self.state(), 4)
        self.assertEqual([item.input_uri.rsplit("=", 1)[1] for item in chosen],
                         ["00000000000000000009", "00000000000000000009",
                          "00000000000000000001", "00000000000000000001"])

    def test_backlogged_news_does_not_starve_disclosures(self):
        dart = Candidate("dart", "hdfs://cluster/dart/one")
        sec = Candidate("sec", "hdfs://cluster/sec/one")
        chosen = select_pending([self.news(0,n) for n in range(100)]+[dart,sec], self.state(), 3)
        self.assertEqual({item.kind for item in chosen}, {"news","dart","sec"})

    def test_failures_have_retry_quota_but_do_not_starve_new_inputs(self):
        old = [self.news(0,n) for n in range(4)]
        new = [self.news(1,n) for n in range(4)]
        state = self.state()
        state["attempts"] = {item.input_uri:{"failures":5,"last_attempt":1} for item in old}
        selected = select_pending(old+new, state, 4)
        self.assertEqual(len(set(selected).intersection(new)), 3)
        self.assertEqual(len(set(selected).intersection(old)), 1)

    def test_limit_one_alternates_failed_and_fresh(self):
        failed, fresh = self.news(0,1), self.news(1,1)
        state = self.state()
        state["attempts"][failed.input_uri] = {"failures":1,"last_attempt":1}
        self.assertEqual(select_pending([failed,fresh], state, 1), [fresh])
        self.assertEqual(select_pending([failed,fresh], state, 1), [failed])

    def test_changed_configuration_refuses_fast_skips(self):
        self.execute([self.news(0,1)])
        changed = dict(self.config, sources={"news":{"name":"other"}})
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            read_state(self.state_path, configuration_digest(changed))

    def test_config_digest_uses_file_contents_not_just_filename(self):
        mapping = self.directory / "map.json"
        mapping.write_text('{}')
        path = self.directory / "config.json"
        path.write_text(json.dumps({"version":1,"roots":self.config["roots"],"company_map_file":"map.json"}))
        before = configuration_digest(load_config(path))
        mapping.write_text('{"sec": {"123": "ABC"}}')
        after = configuration_digest(load_config(path))
        self.assertNotEqual(before, after)

    def test_configuration_digest_binds_all_loader_validation_and_write_modules(self):
        original = Path.read_bytes
        before = configuration_digest(self.config)
        for target in ("sources.py", "postgres.py", "hdfs.py", "discovery.py"):
            def changed(path, target=target):
                data = original(path)
                return data + b"# changed" if path.name == target else data
            with self.subTest(target=target), patch.object(Path, "read_bytes", changed):
                self.assertNotEqual(before, configuration_digest(self.config))

    def test_discovery_failure_can_coexist_with_committed_success(self):
        result, _, _ = self.execute([self.news(0,1)], commit=True,
                                   discover_errors=[{"root_index":1,"error_type":"RuntimeError"}])
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(result["status"], "partial_failure")

    def test_loaded_identity_must_match_discovery(self):
        def load(kind, uri, **kwargs):
            return self.batch(kind, uri+"-wrong")
        result, _, loader = self.execute([self.news(0,1)], load=load)
        self.assertEqual(result["failed"], 1)
        loader.assert_not_called()

    def test_corrupt_attempt_state_is_rejected(self):
        state = self.state()
        state["attempts"] = {"bad":{"last_attempt":-1,"failures":1}}
        self.state_path.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, "Invalid runner attempt"):
            self.state()


if __name__ == "__main__":
    unittest.main()
