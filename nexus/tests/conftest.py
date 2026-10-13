import json
import os
import uuid
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def synthetic_docs():
    """Synthetic BigKinds documents (made-up companies and text, real response shape)."""
    return json.loads((FIXTURES / "bigkinds_docs.json").read_text(encoding="utf-8"))["documents"]


@pytest.fixture
def pg():
    """A throwaway database with V1..V10 applied. Needs TEST_DATABASE_URL (an admin DB URL)."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    import psycopg
    from nexus.load.postgres import MIGRATIONS_DIR, apply_migrations_for_tests, ensure_relationship_types

    name = f"nexus_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    conn = psycopg.connect(url.rsplit("/", 1)[0] + "/" + name)
    try:
        apply_migrations_for_tests(conn, MIGRATIONS_DIR)
        ensure_relationship_types(conn)
        conn.commit()
        yield conn
    finally:
        conn.close()
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE {name} WITH (FORCE)")
