"""Paths and secrets for the NEXUS pipeline."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    repo_root: Path
    data_dir: Path
    bigkinds_key: str


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def load_settings(repo_root: Path | None = None) -> Settings:
    """Environment variables win over the repo-root .env file."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    key = os.environ.get("BIGKINDS_ACCESS_KEY") or _read_dotenv(root / ".env").get("BIGKINDS_ACCESS_KEY", "")
    return Settings(repo_root=root, data_dir=root / "nexus" / "data", bigkinds_key=key)
