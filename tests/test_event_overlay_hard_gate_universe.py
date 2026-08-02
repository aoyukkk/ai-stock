from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from event_overlay.service import (
    EventOverlayShadowService,
    _parse_hard_gate_reasons,
)


FACTOR_VERSION = "TUSHARE_QUANT_V2_CORRECTED_SHADOW"
TRADE_DATE = date(2026, 7, 31)


def _service() -> EventOverlayShadowService:
    service = object.__new__(EventOverlayShadowService)
    service.config = {"screening": {"input_top_n": 100}}
    return service


def _write_universe(
    root: Path,
    *,
    count: int = 105,
    factor_overrides: dict[int, str] | None = None,
    gate_overrides: dict[int, tuple[object, object]] | None = None,
    score_overrides: dict[int, object] | None = None,
    include_trade_date: bool = False,
) -> Path:
    output_dir = root / "outputs" / "quant_v2_validation" / TRADE_DATE.isoformat()
    output_dir.mkdir(parents=True)
    source_path = output_dir / "quant_v2_validation.json"
    source_path.write_text("{}", encoding="utf-8")
    fields = [
        "rank",
        "stock_code",
        "stock_name",
        "total_score",
        "hard_gate",
        "hard_gate_reasons",
        "factor_version",
    ]
    if include_trade_date:
        fields.append("trade_date")
    with (output_dir / "v2_full_universe.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for rank in range(1, count + 1):
            hard_gate, reasons = (gate_overrides or {}).get(rank, (False, "[]"))
            row = {
                "rank": rank,
                "stock_code": f"{rank:06d}",
                "stock_name": f"股票{rank}",
                "total_score": (score_overrides or {}).get(
                    rank, 100 - rank / 10
                ),
                "hard_gate": hard_gate,
                "hard_gate_reasons": reasons,
                "factor_version": (factor_overrides or {}).get(rank, FACTOR_VERSION),
            }
            if include_trade_date:
                row["trade_date"] = TRADE_DATE.strftime("%Y%m%d")
            writer.writerow(row)
    return source_path


def _source() -> dict[str, str]:
    return {
        "trade_date": TRADE_DATE.isoformat(),
        "factor_version": FACTOR_VERSION,
    }


def test_full_universe_refills_top100_after_hard_gate_exclusions(tmp_path: Path) -> None:
    source_path = _write_universe(
        tmp_path,
        gate_overrides={
            1: (True, "['LIMIT_UP_NOT_BUYABLE']"),
            # A non-empty Python-list string remains disqualifying even if the
            # boolean field is incorrectly False.
            3: (False, "['DATA_GATE']"),
        },
    )

    universe_path, rows = _service()._load_eligible_universe(
        source_path, _source(), TRADE_DATE
    )

    assert universe_path.name == "v2_full_universe.csv"
    assert len(rows) == 100
    assert [row["rank"] for row in rows[:3]] == [2, 4, 5]
    assert 1 not in {row["rank"] for row in rows}
    assert 3 not in {row["rank"] for row in rows}
    assert all(row["hard_gate"] is False for row in rows)
    assert all(row["hard_gate_reasons"] == [] for row in rows)


def test_full_universe_rejects_less_than_100_eligible_rows(tmp_path: Path) -> None:
    source_path = _write_universe(
        tmp_path,
        count=100,
        gate_overrides={1: (True, "['LIMIT_UP_NOT_BUYABLE']")},
    )

    with pytest.raises(ValueError, match="HARD_GATE_FREE_UNIVERSE_INSUFFICIENT:99/100"):
        _service()._load_eligible_universe(source_path, _source(), TRADE_DATE)


def test_full_universe_rejects_factor_version_mismatch(tmp_path: Path) -> None:
    source_path = _write_universe(
        tmp_path,
        factor_overrides={105: "WRONG_FACTOR_VERSION"},
    )

    with pytest.raises(ValueError, match="FULL_UNIVERSE_FACTOR_VERSION_MISMATCH"):
        _service()._load_eligible_universe(source_path, _source(), TRADE_DATE)


def test_full_universe_rejects_trade_date_mismatch(tmp_path: Path) -> None:
    source_path = _write_universe(tmp_path, include_trade_date=True)
    source = _source()
    source["trade_date"] = "2026-07-30"

    with pytest.raises(ValueError, match="FULL_UNIVERSE_TRADE_DATE_MISMATCH"):
        _service()._load_eligible_universe(source_path, source, TRADE_DATE)


def test_full_universe_missing_file_fails_closed(tmp_path: Path) -> None:
    source_path = (
        tmp_path
        / "outputs"
        / "quant_v2_validation"
        / TRADE_DATE.isoformat()
        / "quant_v2_validation.json"
    )
    source_path.parent.mkdir(parents=True)
    source_path.write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="V2_FULL_UNIVERSE_NOT_FOUND"):
        _service()._load_eligible_universe(source_path, _source(), TRADE_DATE)


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity", "bad"])
def test_full_universe_rejects_invalid_score_before_search(
    tmp_path: Path,
    invalid: str,
) -> None:
    source_path = _write_universe(
        tmp_path,
        score_overrides={1: invalid},
    )
    with pytest.raises(ValueError, match="FULL_UNIVERSE_TOTAL_SCORE_INVALID"):
        _service()._load_eligible_universe(source_path, _source(), TRADE_DATE)


def test_full_universe_rejects_rank_gap_before_search(tmp_path: Path) -> None:
    source_path = _write_universe(tmp_path)
    universe_path = source_path.parent / "v2_full_universe.csv"
    with universe_path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows[-1]["rank"] = str(len(rows) + 1)
    with universe_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match="RANK_SEQUENCE_INVALID"):
        _service()._load_eligible_universe(source_path, _source(), TRADE_DATE)


def test_selected_top20_with_hard_gate_reason_fails_closed() -> None:
    item = SimpleNamespace(
        stock_code="000001",
        selected_top20=True,
        hard_gate_reasons="['LIMIT_UP_NOT_BUYABLE']",
        raw_quant={"hard_gate": False},
    )

    with pytest.raises(RuntimeError, match="V3_SELECTED_TOP20_HARD_GATE_VIOLATION:000001"):
        EventOverlayShadowService._assert_ranked_hard_gate_integrity([item])


def test_python_list_literal_hard_gate_reasons_are_parsed() -> None:
    assert _parse_hard_gate_reasons("['A', 'B']") == ["A", "B"]
    assert _parse_hard_gate_reasons("[]") == []
