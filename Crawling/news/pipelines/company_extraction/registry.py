from collections import defaultdict
import hashlib
import json
from pathlib import Path

DEFAULT_REGISTRY = Path(__file__).resolve().parents[2] / "data/company_extraction/registry.json"


class Registry:
    def __init__(self, path=DEFAULT_REGISTRY):
        self.path = Path(path)
        raw = self.path.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.data = json.loads(raw.decode("utf-8-sig"))
        self.version = self.data["universe_version"]
        if self.data.get("schema_version") != "1" or not isinstance(self.version, str) or not self.version:
            raise ValueError("unsupported registry schema or empty universe version")
        self.companies = {c["company_id"]: c for c in self.data["companies"]}
        if len(self.companies) != len(self.data["companies"]):
            raise ValueError("duplicate company_id")
        self.securities = defaultdict(list)
        seen = set()
        for security in self.data["securities"]:
            code = (security["market"], security["ticker"])
            if not isinstance(security["ticker"], str) or code in seen:
                raise ValueError("ticker must be a unique market-qualified string")
            seen.add(code)
            if security["company_id"] not in self.companies:
                raise ValueError("security refers to missing company")
            if type(security.get("in_scope")) is not bool:
                raise ValueError("security.in_scope must be a boolean")
            if security["market"] != self.companies[security["company_id"]]["market"]:
                raise ValueError("company and security markets differ")
            self.securities[security["company_id"]].append(security)
        self.aliases = self.data["aliases"]
        alias_ids = set()
        for alias in self.aliases:
            if alias["company_id"] not in self.companies or not alias["alias"].strip():
                raise ValueError("invalid alias reference/text")
            if alias["alias_id"] in alias_ids:
                raise ValueError("duplicate alias_id")
            alias_ids.add(alias["alias_id"])
            if alias.get("risk_level") not in {"safe", "contextual", "blocked"}:
                raise ValueError("invalid alias risk_level")
            if alias.get("alias_type") not in {"official", "short", "ticker", "group", "brand"}:
                raise ValueError("invalid alias_type")
            if alias.get("match_policy", "name") not in {"name", "ticker", "context"}:
                raise ValueError("invalid alias match_policy")
            if alias.get("ticker") and not any(s["ticker"] == alias["ticker"] for s in self.securities[alias["company_id"]]):
                raise ValueError("alias refers to missing security")

    def in_scope(self, company_id):
        return [s for s in self.securities[company_id] if s.get("in_scope", True)]
