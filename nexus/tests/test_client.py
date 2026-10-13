import json

import pytest

from nexus.bigkinds.client import BigKindsClient, BigKindsError
from nexus.config import load_settings

KEY = "secret-key-1234"
OK = (200, b'{"result":0,"return_object":{"x":1}}')


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, path, body):
        self.calls.append((path, body))
        return self.responses.pop(0)


class FakeClock:
    def __init__(self, values):
        self.values = list(values)

    def __call__(self):
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def make(tmp_path, responses, clock=None):
    transport = FakeTransport(responses)
    sleeps = []
    client = BigKindsClient(KEY, tmp_path / "cache", transport=transport,
                            sleep=sleeps.append, clock=clock or FakeClock([0.0]))
    return client, transport, sleeps


def test_post_returns_return_object(tmp_path):
    client, _, _ = make(tmp_path, [OK])
    assert client.post("/search/news", {"query": "a"}, cache=False) == {"x": 1}


def test_body_contains_key_but_cache_file_does_not(tmp_path):
    client, transport, _ = make(tmp_path, [OK])
    client.post("/search/news", {"query": "a"}, cache=True)
    body = json.loads(transport.calls[0][1].decode("utf-8"))
    assert body["access_key"] == KEY
    assert body["argument"] == {"query": "a"}
    files = list((tmp_path / "cache").rglob("*.json"))
    assert len(files) == 1
    assert KEY not in str(files[0])
    assert KEY not in files[0].read_text(encoding="utf-8")


def test_cache_hit_skips_transport(tmp_path):
    client, transport, _ = make(tmp_path, [OK, OK])
    first = client.post("/search/news", {"query": "a"}, cache=True)
    second = client.post("/search/news", {"query": "a"}, cache=True)
    assert first == second == {"x": 1}
    assert len(transport.calls) == 1


def test_cache_false_always_calls(tmp_path):
    client, transport, _ = make(tmp_path, [OK, OK])
    client.post("/search/news", {"query": "a"}, cache=False)
    client.post("/search/news", {"query": "a"}, cache=False)
    assert len(transport.calls) == 2


def test_min_interval_sleeps(tmp_path):
    client, _, sleeps = make(tmp_path, [OK, OK], clock=FakeClock([100.0, 100.3]))
    client.post("/search/news", {"query": "a"}, cache=False)
    client.post("/search/news", {"query": "b"}, cache=False)
    assert sleeps == [pytest.approx(0.7)]


def test_retry_on_5xx_then_success(tmp_path):
    client, transport, sleeps = make(tmp_path, [(504, b""), (504, b""), OK])
    assert client.post("/search/news", {"query": "a"}, cache=False) == {"x": 1}
    assert len(transport.calls) == 3
    assert 2 in sleeps and 5 in sleeps


def test_gives_up_after_three_retries(tmp_path):
    client, transport, _ = make(tmp_path, [(504, b"")] * 4)
    with pytest.raises(BigKindsError):
        client.post("/search/news", {"query": "a"}, cache=False)
    assert len(transport.calls) == 4


def test_nonzero_result_raises_without_retry(tmp_path):
    client, transport, _ = make(tmp_path, [(200, b'{"result":1,"reason":"bad"}'), OK])
    with pytest.raises(BigKindsError) as err:
        client.post("/search/news", {"query": "a"}, cache=False)
    assert err.value.result == 1
    assert err.value.reason == "bad"
    assert len(transport.calls) == 1


def test_load_settings_reads_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("BIGKINDS_ACCESS_KEY", raising=False)
    (tmp_path / ".env").write_text("OTHER=1\nBIGKINDS_ACCESS_KEY=abc\n", encoding="utf-8")
    settings = load_settings(tmp_path)
    assert settings.bigkinds_key == "abc"
    assert settings.data_dir == tmp_path / "nexus" / "data"
