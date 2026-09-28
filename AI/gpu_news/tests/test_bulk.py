import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from bulk import BATCH_FILES, INDEX_NAME, _read_index
from worker import WorkerError


def _archive(work: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(fileobj=io.BytesIO(), mode="w") as _:
        pass
    for name, data in members.items():
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


class ArchiveIntegrityTests(unittest.TestCase):
    """A stream cut on a tar block boundary reads as a clean end of archive.

    The transport once dropped mid-upload and publish went looking for
    index.json inside an empty directory; 1,500 GPU-enriched batches were
    discarded behind a FileNotFoundError. The check names the real cause.
    """

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="cosmos-test-"))

    def test_a_missing_index_names_the_transfer(self):
        with self.assertRaises(WorkerError) as caught:
            _read_index(self.work)
        self.assertIn("incomplete", str(caught.exception))
        self.assertIn("transfer", str(caught.exception))

    def test_an_index_naming_absent_batches_is_rejected_with_counts(self):
        index = [{"name": "000000"}, {"name": "000001"}, {"name": "000002"}]
        members = {INDEX_NAME: json.dumps(index).encode()}
        for name in BATCH_FILES:                       # only the first batch arrived whole
            members[f"000000/{name}"] = b"x"
        members["000001/data.parquet"] = b"x"          # the second, partially
        _archive(self.work, members)
        with self.assertRaises(WorkerError) as caught:
            _read_index(self.work)
        self.assertIn("2 of 3", str(caught.exception))
        self.assertIn("000001", str(caught.exception))

    def test_a_complete_archive_returns_its_index(self):
        index = [{"name": "000000", "input": "/in", "output": "/out"}]
        members = {INDEX_NAME: json.dumps(index).encode()}
        for name in BATCH_FILES:
            members[f"000000/{name}"] = b"x"
        _archive(self.work, members)
        self.assertEqual(_read_index(self.work), index)


if __name__ == "__main__":
    unittest.main()
