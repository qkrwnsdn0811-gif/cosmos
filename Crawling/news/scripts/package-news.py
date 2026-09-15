"""Create a deployment archive from an explicit source/runtime allowlist."""
import gzip
import io
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
VENDOR = "vendor/overseas-news-crawler/"
FILES = [
    "README.md", "requirements.txt", "package.json", "package-lock.json", "config/kospi100.json", "docs/NAVER.md",
    "docs/OPERATIONS.md", "docs/PROVENANCE.md", "services/__init__.py",
    *["services/news_pipeline/" + name for name in (
        "__init__.py", "collector.py", "common.py", "overseas.py", "seed_existing.py",
        "writer.py", "domestic_discover.mjs", "domestic.bundle.mjs", "naver.py", "naver_runner.py",
        "naver_delivery.py", "naver_fetch.mjs", "naver-fetch.bundle.mjs")],
    *["lib/" + name for name in ("news-article-extractor.ts", "robots-policy.ts", "public-http-url.ts")],
    *[VENDOR + name for name in ("LICENSE", "NOTICE.md", "lib/__init__.py", "lib/Crawling/__init__.py",
        "lib/Crawling/News/__init__.py", "lib/Crawling/News/overseas.py", "lib/Crawling/News/history.py")],
    *["deployment/" + name for name in ("run-news-python.sh", "run-collector.sh", "run-writer.sh",
        "cosmos-news-collector.service", "cosmos-news-hdfs-writer.service", "news.env.example",
        "run-naver.sh", "cosmos-naver-news@.service", "naver-news.env.example")],
    *["scripts/" + name for name in ("build-domestic.mjs", "package-news.py", "verify_news_kafka_hdfs.py")],
    *["tests/" + name for name in ("test_news_kafka_common.py", "test_news_kafka_collector.py",
        "test_news_kafka_overseas.py", "test_news_kafka_writer.py", "news-kafka-domestic.test.mjs",
        "news-article-extractor.test.mjs", "robots-policy.test.mjs", "test_news_kafka_naver.py",
        "test_news_kafka_naver_delivery.py", "naver-fetch.test.mjs")],
]

def main():
    for name in FILES:
        path = ROOT / name
        if not path.is_file() or path.is_symlink():
            raise SystemExit(f"Missing regular input: {name}; run npm ci and npm run build first")
    output = ROOT / "dist/news-kafka.tar.gz"
    output.parent.mkdir(exist_ok=True)
    with output.open("wb") as stream, gzip.GzipFile(fileobj=stream, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for name in sorted(FILES):
                data = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                entry.mode = 0o755 if name.endswith(".sh") else 0o644
                archive.addfile(entry, io.BytesIO(data))
    print(output)

if __name__ == "__main__":
    main()
