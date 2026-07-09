from sqlalchemy import select

import database.models  # noqa: F401
from database.base import Base
from database.models.factor import StockFactorDetail, StockFactorScore
from database.session import create_engine_from_url, get_session
from quant.service import QuantService


def test_quant_service_runs_full_mock_scan_without_persistence() -> None:
    ranking = QuantService().run_quant_scan(top_q=5, persist=False)

    assert ranking.universe_size >= 20
    assert ranking.returned_count == 5
    assert ranking.results[0].rank == 1
    assert ranking.results[0].factor_details


def test_quant_service_top_q_larger_than_universe_returns_all() -> None:
    ranking = QuantService().run_quant_scan(top_q=500, persist=False)

    assert ranking.returned_count == ranking.universe_size


def test_quant_service_persist_true_writes_factor_tables() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        ranking = QuantService().run_quant_scan(top_q=3, persist=True, session=session)

        scores = session.scalars(select(StockFactorScore)).all()
        details = session.scalars(select(StockFactorDetail)).all()
        assert len(scores) == ranking.returned_count
        assert len(details) >= ranking.returned_count
    finally:
        session.close()
        engine.dispose()
