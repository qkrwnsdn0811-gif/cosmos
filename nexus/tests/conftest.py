import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def synthetic_docs():
    """Synthetic BigKinds documents (made-up companies and text, real response shape)."""
    return json.loads((FIXTURES / "bigkinds_docs.json").read_text(encoding="utf-8"))["documents"]
