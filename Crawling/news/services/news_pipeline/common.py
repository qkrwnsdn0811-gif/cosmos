"""Shared event contract and a durable local producer outbox.

Collection progress can advance after enqueue commits. Delivery progress only
advances after Kafka acknowledges. Payloads remain locally recoverable afterward.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("article URL must be absolute HTTP(S)")
    # Domestic sources use query-string article identifiers. Preserve the query.
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def atomic_json(path: str | Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    if os.name != "nt":
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def make_event(*, source: str, region: str, language: str, url: str,
               title: str, content: str, organization: str = "",
               published_at: str | None = None, collected_at: str | None = None,
               run_id: str = "", metadata: dict | None = None) -> dict:
    if region not in {"domestic", "overseas"}:
        raise ValueError("region must be domestic or overseas")
    if not source or not title.strip() or len(content.strip()) < 80:
        raise ValueError("source, title and article body (80+ characters) required")
    url = canonical_url(url)
    metadata = dict(metadata or {})
    if published_at:
        try:
            published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            if published.tzinfo is None:
                raise ValueError("unknown source timezone")
            published_at = published.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError, AttributeError):
            metadata["published_at_original"] = published_at
            metadata["published_at_unresolved"] = True
            published_at = None
    collected = datetime.fromisoformat((collected_at or utcnow()).replace("Z", "+00:00"))
    if collected.tzinfo is None:
        raise ValueError("collection timestamp requires timezone")
    body_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    identity = f"{source}\n{url}\n{body_hash}"
    event = {
        "schema_version": 1,
        "event_id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "source": source, "region": region, "language": language,
        "url": url, "url_hash": url_hash(url), "title": title,
        "content": content, "content_hash": body_hash,
        "organization": organization or "", "published_at": published_at,
        "collected_at": collected.astimezone(timezone.utc).isoformat(), "run_id": run_id or str(uuid.uuid4()),
        "metadata": metadata,
    }
    # Keep below the broker's default max.message.bytes without truncating bodies.
    if len(json.dumps(event, ensure_ascii=False).encode("utf-8")) > 900_000:
        raise ValueError("article event exceeds 900000 bytes; retain for explicit handling")
    return event


class Outbox:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=60)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=60000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS seen_urls (
                url_hash TEXT PRIMARY KEY, origin TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS outbox (
                event_id TEXT PRIMARY KEY,
                url_hash TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL, region TEXT NOT NULL,
                message_key TEXT NOT NULL, payload TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL, delivered_at TEXT,
                kafka_partition INTEGER, kafka_offset INTEGER,
                attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT
            );
            CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(state,created_at);
            CREATE TABLE IF NOT EXISTS seed_files (
                path TEXT PRIMARY KEY, size INTEGER NOT NULL,
                rows INTEGER NOT NULL, seeded_at TEXT NOT NULL
            );
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def seen_url(self, url: str) -> bool:
        value = url_hash(canonical_url(url))
        return self.db.execute("SELECT 1 FROM seen_urls WHERE url_hash=?", (value,)).fetchone() is not None

    def enqueue(self, event: dict) -> bool:
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        hashed = url_hash(canonical_url(event["url"]))
        with self.db:
            if self.db.execute("SELECT 1 FROM seen_urls WHERE url_hash=?", (hashed,)).fetchone():
                return False
            self.db.execute("""INSERT INTO outbox
                (event_id,url_hash,source,region,message_key,payload,created_at)
                VALUES (?,?,?,?,?,?,?)""", (event["event_id"], hashed, event["source"],
                event["region"], f"{event['source']}:{hashed}", payload, utcnow()))
            self.db.execute("INSERT INTO seen_urls VALUES (?,?)", (hashed, "outbox"))
        return True

    def stats(self) -> dict:
        return {
            "seen_urls": self.db.execute("SELECT COUNT(*) FROM seen_urls").fetchone()[0],
            "outbox": [dict(r) for r in self.db.execute("""SELECT state,region,COUNT(*) AS rows,
                MIN(created_at) AS oldest,MAX(delivered_at) AS last_delivered
                FROM outbox GROUP BY state,region""")],
            "pending": self.db.execute("SELECT COUNT(*) FROM outbox WHERE state='pending'").fetchone()[0],
            "payload_bytes": self.db.execute("SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM outbox").fetchone()[0],
        }

    def publish(self, producer, topic: str = "news.raw", limit: int = 100) -> dict:
        rows = self.db.execute("SELECT * FROM outbox WHERE state='pending' ORDER BY created_at,event_id LIMIT ?", (limit,)).fetchall()
        deliveries = []
        errors = []
        for row in rows:
            event_id = row["event_id"]
            def callback(error, message, eid=event_id):
                if error is not None:
                    errors.append((str(error), eid))
                else:
                    deliveries.append((utcnow(), message.partition(), message.offset(), eid))
            try:
                producer.produce(topic, key=row["message_key"].encode(),
                                 value=row["payload"].encode(), on_delivery=callback)
                producer.poll(0)
            except Exception as exc:
                errors.append((f"{type(exc).__name__}: {exc}"[:1000], event_id))
        remaining = producer.flush(35)
        with self.db:
            self.db.executemany("""UPDATE outbox SET state='sent',delivered_at=?,
                kafka_partition=?,kafka_offset=?,attempts=attempts+1,last_error=NULL
                WHERE event_id=?""", deliveries)
            self.db.executemany("UPDATE outbox SET attempts=attempts+1,last_error=? WHERE event_id=? AND state='pending'", errors)
        if remaining:
            raise RuntimeError(f"Kafka delivery unresolved for {remaining} event(s); outbox retained")
        return {"acknowledged": len(deliveries), "errors": len(errors)}


def kafka_producer(bootstrap: str):
    from confluent_kafka import Producer
    return Producer({"bootstrap.servers": bootstrap, "client.id": "cosmos-news-outbox",
        "enable.idempotence": True, "acks": "all", "compression.type": "lz4",
        "delivery.timeout.ms": 30000, "request.timeout.ms": 10000,
        "message.max.bytes": 1000000, "queue.buffering.max.kbytes": 16384})
