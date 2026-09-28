"""Bounded Fundus subprocess integration for the durable news outbox.

Fundus currently requires an older curl-cffi than the Yahoo collector.  Keep it
in an isolated virtual environment and accept only validated JSON from the
worker so the existing collector remains independently recoverable.
"""
from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path

from .common import atomic_json, make_event


class FundusCollector:
    def __init__(
        self,
        state_dir,
        outbox,
        *,
        python,
        worker,
        registry,
        interval_seconds=600,
        scan_limit=300,
        timeout_seconds=300,
    ):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_dir / "scheduler.json"
        self.outbox = outbox
        self.python = str(Path(python))
        self.worker = str(Path(worker))
        self.registry = str(Path(registry))
        self.interval_seconds = interval_seconds
        self.scan_limit = scan_limit
        self.timeout_seconds = timeout_seconds
        if interval_seconds < 60 or scan_limit < 1 or timeout_seconds < 30:
            raise ValueError("invalid Fundus collector bounds")

    def _state(self):
        if not self.state_path.exists():
            return {}
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("invalid Fundus scheduler state")
        return value

    def collect_latest(self, limit=8):
        if not 1 <= limit <= 100:
            raise ValueError("limit must be 1..100")
        state = self._state()
        now = time.time()
        last_attempt = float(state.get("last_attempt_epoch", 0))
        last_success = float(state.get("last_success_epoch", 0))
        due_after = self.interval_seconds if last_success >= last_attempt else 120
        if now - last_attempt < due_after:
            return {
                "mode": "latest",
                "skipped": "not_due",
                "next_attempt_in_seconds": max(0, int(due_after - (now - last_attempt))),
                "enqueued": 0,
                "existing": 0,
            }

        state["last_attempt_epoch"] = now
        atomic_json(self.state_path, state)
        command = [
            self.python,
            self.worker,
            "--registry",
            self.registry,
            "--limit",
            str(limit),
            "--scan-limit",
            str(self.scan_limit),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=self.timeout_seconds,
        )
        if completed.returncode:
            raise RuntimeError((completed.stderr or completed.stdout or "Fundus worker failed")[-2000:])
        try:
            output_lines = [line for line in completed.stdout.splitlines() if line.strip()]
            payload = json.loads(output_lines[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError("Fundus worker returned invalid JSON") from exc
        articles = payload.get("articles")
        if not isinstance(articles, list):
            raise RuntimeError("Fundus worker response has no article list")

        result = {
            "mode": "latest",
            "scanned": int(payload.get("scanned", 0)),
            "matched": len(articles),
            "enqueued": 0,
            "existing": 0,
            "failed": 0,
            "publishers": payload.get("publishers", []),
        }
        run_id = str(uuid.uuid4())
        for article in articles:
            try:
                if self.outbox.seen_url(article["url"]):
                    result["existing"] += 1
                    continue
                metadata = dict(article.get("metadata") or {})
                metadata["collector"] = "fundus"
                event = make_event(
                    source="fundus_" + article["publisher_key"].lower(),
                    region="overseas",
                    language=article.get("language") or "en",
                    url=article["url"],
                    title=article["title"],
                    content=article["content"],
                    organization=article.get("publisher_name") or "",
                    published_at=article.get("published_at"),
                    collected_at=article.get("collected_at"),
                    run_id=run_id,
                    metadata=metadata,
                )
                added = self.outbox.enqueue(event)
                result["enqueued" if added else "existing"] += 1
            except Exception:
                result["failed"] += 1

        state.update(
            last_success_epoch=time.time(),
            last_result={key: value for key, value in result.items() if key != "publishers"},
        )
        atomic_json(self.state_path, state)
        return result

