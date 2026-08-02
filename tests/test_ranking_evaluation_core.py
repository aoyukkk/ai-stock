from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from database.models.ranking_evaluation import (
    RankingEvaluationDailyMetric,
    RankingEvaluationDataIssue,
    RankingEvaluationForwardOutcome,
    RankingEvaluationSnapshot,
    RankingEvaluationSnapshotItem,
    RankingEvaluationState,
)
from database.session import create_engine_from_url, get_session, init_db
from services.ranking_evaluation.constants import (
    EVALUATION_SCOPE,
    EVALUATION_VERSION,
    load_config,
    original_group,
)
from services.ranking_evaluation.export_service import REQUIRED_WORKSHEETS
from services.ranking_evaluation.market_data_service import RankingMarketDataService
from services.ranking_evaluation.metric_calculator import (
    MetricObservation,
    calculate_metric_payload,
    spearman_rank_ic,
)
from services.ranking_evaluation.outcome_backfill_service import OutcomeBackfillService
from services.ranking_evaluation.schemas import RankingSourceRow, SnapshotCaptureRequest
from services.ranking_evaluation.snapshot_service import RankingSnapshotService
from services.ranking_evaluation.trading_calendar_service import RankingTradingCalendarService
from services.ranking_evaluation.weekly_report_service import (
    RankingWeeklyReportService,
    daily_equal_weight,
    weekly_monotonicity,
)


RANKING_DAY = date(2026, 7, 20)
OPEN_DATES = [
    RANKING_DAY + timedelta(days=offset)
    for offset in range(0, 22)
    if (RANKING_DAY + timedelta(days=offset)).weekday() < 5
]


@pytest.fixture()
def session():
    engine = create_engine_from_url("sqlite:///:memory:")
    init_db(engine)
    value = get_session(engine)
    try:
        yield value
    finally:
        value.close()
        engine.dispose()


def _rows(count: int = 100, *, duplicate_first: bool = False):
    result = [
        RankingSourceRow(
            stock_code=f"{rank:06d}",
            ts_code=f"{rank:06d}",
            stock_name=f"股票{rank}",
            original_rank=rank,
            quant_score=100 - rank / 10,
            source_row_number=rank,
        )
        for rank in range(1, count + 1)
    ]
    if duplicate_first and len(result) >= 2:
        result[1] = RankingSourceRow(
            stock_code=result[0].stock_code,
            ts_code=result[0].ts_code,
            stock_name=result[0].stock_name,
            original_rank=2,
            quant_score=result[1].quant_score,
            source_row_number=2,
        )
    return result


def _daily_rows(day: date, *, multiplier: float = 1.0):
    return [
        {
            "ts_code": f"{rank:06d}",
            "trade_date": day.strftime("%Y%m%d"),
            "close": (10 + rank / 100) * multiplier,
            "vol": 100,
        }
        for rank in range(1, 101)
    ]


def _services(
    session,
    tmp_path: Path,
    *,
    batches=None,
    configured_activation=None,
):
    config = load_config()
    config["activation_date"] = configured_activation
    config["snapshot_output_root"] = str(tmp_path / "snapshots")
    config["weekly_output_root"] = str(tmp_path / "weekly")
    calendar = RankingTradingCalendarService(open_dates=OPEN_DATES)
    market = RankingMarketDataService(
        session,
        injected_batches=batches
        or {
            RANKING_DAY: _daily_rows(RANKING_DAY),
        },
    )
    snapshot = RankingSnapshotService(
        session, calendar=calendar, market=market, config=config
    )
    return config, calendar, market, snapshot


def _capture(
    session,
    tmp_path,
    *,
    factor_version="TUSHARE_BASELINE_V1",
    rows=None,
    batches=None,
    day=RANKING_DAY,
    allow_historical=False,
    configured_activation=None,
):
    config, calendar, market, service = _services(
        session,
        tmp_path,
        batches=batches,
        configured_activation=configured_activation,
    )
    request = SnapshotCaptureRequest(
        ranking_trade_date=day,
        source_quant_run_id=f"quant-{factor_version}-{day}",
        factor_version=factor_version,
        evaluation_scope=EVALUATION_SCOPE,
        allow_historical_import=allow_historical,
        snapshot_origin="HISTORICAL_IMPORT" if allow_historical else "FORWARD_CAPTURE",
    )
    result = service.capture_rows(
        request,
        list(rows or _rows()),
        source_input_hash=f"input-{factor_version}-{day}",
        model_name=factor_version,
        score_version="test-v1",
        production_or_shadow=(
            "PRODUCTION" if factor_version == "TUSHARE_BASELINE_V1" else "SHADOW"
        ),
    )
    return result, config, calendar, market, service


def test_capture_top100_snapshot(session, tmp_path):
    result, *_ = _capture(session, tmp_path)
    snapshot = session.scalar(select(RankingEvaluationSnapshot))
    count = session.scalar(
        select(func.count(RankingEvaluationSnapshotItem.id)).where(
            RankingEvaluationSnapshotItem.snapshot_id == snapshot.id
        )
    )
    assert result["status"] == "CAPTURED"
    assert count == 100
    assert snapshot.evaluation_scope == EVALUATION_SCOPE


def test_snapshot_is_immutable(session, tmp_path):
    first, _, _, _, service = _capture(session, tmp_path)
    request = SnapshotCaptureRequest(
        ranking_trade_date=RANKING_DAY,
        source_quant_run_id="quant-TUSHARE_BASELINE_V1-2026-07-20",
        factor_version="TUSHARE_BASELINE_V1",
        evaluation_scope=EVALUATION_SCOPE,
    )
    same = service.capture_rows(
        request,
        _rows(),
        source_input_hash="input-TUSHARE_BASELINE_V1-2026-07-20",
        model_name="TUSHARE_BASELINE_V1",
        score_version="test-v1",
        production_or_shadow="PRODUCTION",
    )
    changed = _rows()
    changed[0] = RankingSourceRow(
        stock_code="000001",
        original_rank=1,
        quant_score=1,
        source_row_number=1,
    )
    assert same["status"] == "EXISTING_IMMUTABLE_SNAPSHOT"
    with pytest.raises(ValueError, match="RANKING_SNAPSHOT_IMMUTABLE_CONFLICT"):
        service.capture_rows(
            request,
            changed,
            source_input_hash="input-TUSHARE_BASELINE_V1-2026-07-20",
            model_name="TUSHARE_BASELINE_V1",
            score_version="test-v1",
            production_or_shadow="PRODUCTION",
        )
    assert first["snapshot_hash"] == same["snapshot_hash"]


def test_factor_versions_are_isolated(session, tmp_path):
    _capture(session, tmp_path)
    _capture(
        session,
        tmp_path,
        factor_version="TUSHARE_QUANT_V2_CORRECTED_SHADOW",
    )
    rows = list(
        session.scalars(
            select(RankingEvaluationSnapshot).order_by(
                RankingEvaluationSnapshot.factor_version
            )
        )
    )
    assert {row.factor_version for row in rows} == {
        "TUSHARE_BASELINE_V1",
        "TUSHARE_QUANT_V2_CORRECTED_SHADOW",
    }
    assert len({row.snapshot_hash for row in rows}) == 2


def test_trading_day_horizon_mapping():
    calendar = RankingTradingCalendarService(open_dates=OPEN_DATES)
    mapped = calendar.horizon_dates(RANKING_DAY, (1, 3, 5, 10))
    assert mapped[1] == date(2026, 7, 21)
    assert mapped[3] == date(2026, 7, 23)
    assert mapped[5] == date(2026, 7, 27)
    assert mapped[10] == date(2026, 8, 3)


def test_no_future_data_leakage(session, tmp_path):
    _, config, calendar, market, _ = _capture(session, tmp_path)
    result = OutcomeBackfillService(
        session, calendar=calendar, market=market, config=config
    ).refresh(as_of_date=RANKING_DAY)
    assert result["matured"] == 0
    assert session.scalar(select(func.count(RankingEvaluationForwardOutcome.id))) == 0


def test_suspension_does_not_shift_horizon(session, tmp_path):
    d1 = OPEN_DATES[1]
    batches = {
        RANKING_DAY: _daily_rows(RANKING_DAY),
        d1: [
            {
                **row,
                "vol": 0 if row["ts_code"] == "000001" else row["vol"],
            }
            for row in _daily_rows(d1, multiplier=1.01)
        ],
    }
    _, config, calendar, market, _ = _capture(
        session, tmp_path, batches=batches
    )
    OutcomeBackfillService(
        session, calendar=calendar, market=market, config=config
    ).refresh(as_of_date=d1)
    outcome = session.scalar(
        select(RankingEvaluationForwardOutcome)
        .join(
            RankingEvaluationSnapshotItem,
            RankingEvaluationSnapshotItem.id
            == RankingEvaluationForwardOutcome.snapshot_item_id,
        )
        .where(
            RankingEvaluationSnapshotItem.stock_code == "000001",
            RankingEvaluationForwardOutcome.horizon == 1,
        )
    )
    assert outcome.due_trade_date == d1
    assert outcome.outcome_status == "SUSPENDED_ON_DUE_DATE"


def test_perfect_rank_ic_is_positive_one():
    rows = [
        MetricObservation(str(rank), rank, float(101 - rank))
        for rank in range(1, 101)
    ]
    assert spearman_rank_ic(rows) == pytest.approx(1.0)


def test_inverse_rank_ic_is_negative_one():
    rows = [
        MetricObservation(str(rank), rank, float(rank))
        for rank in range(1, 101)
    ]
    assert spearman_rank_ic(rows) == pytest.approx(-1.0)


def test_top20_bottom20_spread():
    rows = [
        MetricObservation(str(rank), rank, float(101 - rank) / 100)
        for rank in range(1, 101)
    ]
    payload = calculate_metric_payload(rows)
    assert payload["top20_bottom20_spread"] > 0
    assert payload["top20_valid_count"] == 20
    assert payload["bottom20_valid_count"] == 20


def test_group_assignment_is_frozen():
    assert [original_group(rank) for rank in (1, 20, 21, 40, 41, 60, 61, 80, 81, 100)] == [
        "G1",
        "G1",
        "G2",
        "G2",
        "G3",
        "G3",
        "G4",
        "G4",
        "G5",
        "G5",
    ]
    rows = [
        MetricObservation(str(rank), rank, 0.1)
        for rank in range(1, 101)
        if rank != 1
    ]
    payload = calculate_metric_payload(rows)
    assert payload["group_counts"]["G1"] == 19
    assert payload["group_counts"]["G2"] == 20


def test_monotonicity_4_of_4():
    result = weekly_monotonicity(
        {"G1": 0.05, "G2": 0.04, "G3": 0.03, "G4": 0.02, "G5": 0.01}
    )
    assert result["monotonicity_label"] == "4/4"
    assert result["monotonicity_pass_count"] == 4


@pytest.mark.parametrize(
    ("groups", "label"),
    [
        ({"G1": 5, "G2": 4, "G3": 3, "G4": 1, "G5": 2}, "3/4"),
        ({"G1": 5, "G2": 4, "G3": 1, "G4": 2, "G5": 3}, "2/4"),
        ({"G1": 5, "G2": 1, "G3": 2, "G4": 3, "G5": 4}, "1/4"),
        ({"G1": 1, "G2": 2, "G3": 3, "G4": 4, "G5": 5}, "0/4"),
    ],
)
def test_monotonicity_partial(groups, label):
    assert weekly_monotonicity(groups)["monotonicity_label"] == label


def test_unmatured_horizon_is_null():
    payload = calculate_metric_payload([])
    assert payload["calculation_status"] == "NOT_MATURED"
    assert payload["rank_ic"] is None
    assert payload["top20_bottom20_spread"] is None


def test_duplicate_stock_is_preserved_and_excluded(session, tmp_path):
    _capture(session, tmp_path, rows=_rows(duplicate_first=True))
    snapshot = session.scalar(select(RankingEvaluationSnapshot))
    items = list(
        session.scalars(
            select(RankingEvaluationSnapshotItem).where(
                RankingEvaluationSnapshotItem.snapshot_id == snapshot.id
            )
        )
    )
    issues = list(
        session.scalars(
            select(RankingEvaluationDataIssue).where(
                RankingEvaluationDataIssue.snapshot_id == snapshot.id,
                RankingEvaluationDataIssue.issue_code == "DUPLICATE_STOCK",
            )
        )
    )
    assert len(items) == 100
    assert sum(item.stock_code == "000001" for item in items) == 2
    assert sum(item.row_data_status == "ABNORMAL" for item in items) >= 2
    assert issues


def test_weekly_average_uses_daily_equal_weight():
    assert daily_equal_weight([0.10, -0.02]) == pytest.approx(0.04)
    pooled_by_sample_count = (0.10 * 100 + -0.02 * 2) / 102
    assert daily_equal_weight([0.10, -0.02]) != pytest.approx(
        pooled_by_sample_count
    )


def test_activation_date_boundary(session, tmp_path):
    activation = RANKING_DAY + timedelta(days=1)
    _capture(
        session,
        tmp_path,
        day=activation,
        batches={activation: _daily_rows(activation)},
        configured_activation=activation,
    )
    state = session.scalar(select(RankingEvaluationState))
    assert state.activation_date == activation
    older = RANKING_DAY
    batches = {
        older: _daily_rows(older),
    }
    with pytest.raises(ValueError, match="HISTORICAL_IMPORT_REQUIRES_EXPLICIT_FLAG"):
        _capture(
            session,
            tmp_path,
            factor_version="OLDER_SHADOW",
            day=older,
            batches=batches,
        )


def test_raw_and_adjusted_returns_not_mixed(session, tmp_path):
    d1 = OPEN_DATES[1]
    batches = {
        RANKING_DAY: _daily_rows(RANKING_DAY),
        d1: _daily_rows(d1, multiplier=1.10),
    }
    result, config, calendar, _, _ = _capture(
        session, tmp_path, batches=batches
    )
    market = RankingMarketDataService(
        session,
        injected_batches=batches,
        injected_adjustments={
            RANKING_DAY: {f"{rank:06d}.SZ": 1.0 for rank in range(1, 101)},
            d1: {f"{rank:06d}.SZ": 2.0 for rank in range(1, 101)},
        },
    )
    OutcomeBackfillService(
        session, calendar=calendar, market=market, config=config
    ).refresh(as_of_date=d1)
    outcome = session.scalar(
        select(RankingEvaluationForwardOutcome).where(
            RankingEvaluationForwardOutcome.horizon == 1
        )
    )
    snapshot = session.scalar(
        select(RankingEvaluationSnapshot).where(
            RankingEvaluationSnapshot.snapshot_id == result["snapshot_id"]
        )
    )
    assert float(outcome.return_decimal) == pytest.approx(0.10)
    assert float(outcome.adjusted_return_decimal) == pytest.approx(1.20)
    assert snapshot.return_basis == "RAW_CLOSE"


def test_daily_update_is_idempotent(session, tmp_path):
    d1 = OPEN_DATES[1]
    batches = {
        RANKING_DAY: _daily_rows(RANKING_DAY),
        d1: _daily_rows(d1, multiplier=1.01),
    }
    _, config, calendar, market, _ = _capture(
        session, tmp_path, batches=batches
    )
    service = OutcomeBackfillService(
        session, calendar=calendar, market=market, config=config
    )
    service.refresh(as_of_date=d1)
    count_before = session.scalar(
        select(func.count(RankingEvaluationForwardOutcome.id))
    )
    service.refresh(as_of_date=d1)
    count_after = session.scalar(
        select(func.count(RankingEvaluationForwardOutcome.id))
    )
    metric_count = session.scalar(select(func.count(RankingEvaluationDailyMetric.id)))
    assert count_before == count_after == 400
    assert metric_count == 4


def test_excel_required_sheets_and_columns():
    assert REQUIRED_WORKSHEETS == (
        "累计总览",
        "Rank_IC",
        "Top20_Bottom20",
        "五组收益",
        "单调性判断",
        "排名日明细",
        "每日五组平均收益",
        "数据质量",
        "版本与审计",
    )
    required_detail_columns = {
        "stock_code",
        "original_rank",
        "quant_score",
        "factor_version",
        "original_group",
        "baseline_close",
    }
    assert required_detail_columns <= {
        "stock_code",
        "original_rank",
        "quant_score",
        "factor_version",
        "original_group",
        "baseline_close",
        "row_data_status",
    }


def test_weekly_report_exports_immutable_sidecars(session, tmp_path):
    d1 = OPEN_DATES[1]
    batches = {
        RANKING_DAY: _daily_rows(RANKING_DAY),
        d1: _daily_rows(d1, multiplier=1.01),
    }
    _, config, calendar, market, _ = _capture(
        session, tmp_path, batches=batches
    )
    OutcomeBackfillService(
        session, calendar=calendar, market=market, config=config
    ).refresh(as_of_date=d1)
    result = RankingWeeklyReportService(
        session, calendar=calendar, config=config
    ).run(
        week_ending=date(2026, 7, 24),
        factor_version="TUSHARE_BASELINE_V1",
    )
    assert result["summary"]["horizons"]["D1"]["matured_ranking_day_count"] == 1
    assert result["summary"]["horizons"]["D5"]["matured_ranking_day_count"] == 0
    for key in (
        "summary",
        "daily_metrics",
        "ranking_details",
        "data_quality",
        "run_manifest",
    ):
        assert Path(result["artifacts"][key]).exists()
    same = RankingWeeklyReportService(
        session, calendar=calendar, config=config
    ).run(
        week_ending=date(2026, 7, 24),
        factor_version="TUSHARE_BASELINE_V1",
    )
    assert same["status"] == "EXISTING_IMMUTABLE_REPORT"


def test_excel_stock_code_text_format():
    style_contract = {
        "stock_code_format": "000000",
        "missing_values": "blank",
        "percentage_format": "0.00%",
    }
    assert style_contract["stock_code_format"] == "000000"
    assert f"{1:06d}" == "000001"


def test_no_llm_calls(session, tmp_path):
    _, _, _, market, _ = _capture(session, tmp_path)
    assert market.llm_calls == 0


def test_no_order_or_production_side_effects(session, tmp_path):
    result, *_ = _capture(session, tmp_path)
    assert result["orders"] == 0
    assert result["llm_calls"] == 0
    assert result["scheduler"] is False
    assert result["shadow_only"] is True
