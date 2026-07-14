from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from backend.core.runtime_paths import tushare_cache_root
from typing import Any

from stock_codes import normalize_ts_code


@dataclass(frozen=True)
class ConceptLookupResult:
    stock_code: str
    raw_source_count: int
    normalized_source_count: int
    inferred_count: int
    normalized_tags: list[str]
    source_status: str


class TushareConceptIndex:
    """Build a local stock-to-THS reverse index without per-stock API calls."""

    def __init__(self, cache_root: Path | str | None = None) -> None:
        self.cache_root = Path(cache_root) if cache_root is not None else tushare_cache_root()
        self._names, self._members, self._raw_counts = self._load()

    def lookup(self, stock_code: str) -> ConceptLookupResult:
        code = normalize_ts_code(stock_code)
        tags = list(self._members.get(code, []))
        return ConceptLookupResult(
            stock_code=code,
            raw_source_count=int(self._raw_counts.get(code, 0)),
            normalized_source_count=len(tags),
            inferred_count=0,
            normalized_tags=tags,
            source_status="VERIFIED_STRUCTURED" if tags else "UNKNOWN",
        )

    def audit(self) -> dict[str, Any]:
        return {
            "concept_count": len(self._names),
            "mapped_stock_count": len(self._members),
            "raw_membership_count": sum(self._raw_counts.values()),
            "normalized_membership_count": sum(len(value) for value in self._members.values()),
            "source_status": "VERIFIED_STRUCTURED" if self._members else "UNKNOWN",
        }

    def _load(self) -> tuple[dict[str, str], dict[str, list[str]], dict[str, int]]:
        names: dict[str, str] = {}
        for path in self.cache_root.rglob("*ths_index*.json"):
            for row in _records(path):
                concept_code = str(row.get("ts_code") or "").strip().upper()
                name = str(row.get("name") or row.get("concept_name") or "").strip()
                if concept_code and name and name.lower() != "concept":
                    names[concept_code] = name

        members: dict[str, list[str]] = {}
        raw_counts: dict[str, int] = {}
        for path in self.cache_root.rglob("*ths_member*.json"):
            for row in _records(path):
                concept_code = str(row.get("ts_code") or row.get("concept_code") or "").strip().upper()
                member_code = str(row.get("con_code") or row.get("stock_code") or "").strip()
                try:
                    stock_code = normalize_ts_code(member_code)
                except ValueError:
                    continue
                raw_counts[stock_code] = raw_counts.get(stock_code, 0) + 1
                tag = names.get(concept_code)
                if not tag:
                    continue
                values = members.setdefault(stock_code, [])
                if tag not in values:
                    values.append(tag)
        for values in members.values():
            values.sort()
        return names, members, raw_counts


def _records(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    rows = value.get("records") or value.get("data") or []
    return [item for item in rows if isinstance(item, dict)]
