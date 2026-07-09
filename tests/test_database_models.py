from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

import database.models  # noqa: F401
from database.base import Base
from database.models.ai import (
    AIAnalysisResult,
    DecisionSnapshot,
    PredictionRecord,
    StockAIScore,
)
from database.models.factor import StockFactorScore
from database.models.market import StockMarketData
from database.models.memory import AgentMemoryNote, MemoryLink, StrategyPlaybook
from database.models.news import News, NewsStockRelation
from database.models.order_plan import OrderPlan, OrderPriceCandidate
from database.models.review import DailyReview
from database.models.stock import StockMaster
from database.models.system import LLMUsage
from database.models.trading import Position, TradeOrder, TradeRecord, TradingAccount
from database.session import create_engine_from_url, get_session


def test_core_models_can_insert_and_query_in_sqlite_memory() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)
    now = datetime.now(timezone.utc)
    today = date.today()

    try:
        stock = StockMaster(
            code="000001",
            name="Ping An Bank",
            market="SZ",
            industry="Bank",
            list_date=today,
            status="NORMAL",
        )
        session.add(stock)

        market_data = StockMarketData(
            stock_code="000001",
            datetime=now,
            open=Decimal("10.0000"),
            high=Decimal("10.5000"),
            low=Decimal("9.9000"),
            close=Decimal("10.2000"),
            pre_close=Decimal("10.0000"),
            volume=1000000,
            amount=Decimal("10200000.00"),
            turnover_rate=Decimal("1.2300"),
            change_percent=Decimal("2.0000"),
            limit_up_price=Decimal("11.0000"),
            limit_down_price=Decimal("9.0000"),
        )
        factor_score = StockFactorScore(
            stock_code="000001",
            date=today,
            technical_score=Decimal("80.0000"),
            capital_score=Decimal("75.0000"),
            emotion_score=Decimal("70.0000"),
            momentum_score=Decimal("72.0000"),
            risk_score=Decimal("20.0000"),
            total_score=Decimal("73.5000"),
            factor_version="test-v1",
        )
        news = News(
            title="Test news",
            content="Structured test news only.",
            source="mock",
            publish_time=now,
            importance=Decimal("80.0000"),
            sentiment="POSITIVE",
            raw_json={"mock": True},
        )
        session.add_all([market_data, factor_score, news])
        session.flush()

        relation = NewsStockRelation(
            news_id=news.id,
            stock_code="000001",
            industry="Bank",
            impact_direction="POSITIVE",
            impact_score=Decimal("75.0000"),
            confidence=Decimal("0.8000"),
            reason="mock relation",
        )
        llm_usage = LLMUsage(
            provider="mock",
            model_name="mock-fast",
            agent_name="test_agent",
            task="unit_test",
            total_tokens=0,
            status="ok",
        )
        snapshot = DecisionSnapshot(
            stock_code="000001",
            snapshot_time=now,
            market_data_json={"close": "10.20"},
            factor_json={"total_score": "73.5"},
            news_json={"count": 1},
            agent_result_json={},
            memory_json={},
            order_price_json={},
            final_score=Decimal("73.5000"),
            risk_level="LOW",
            recommendation="WATCH",
        )
        session.add_all([relation, llm_usage, snapshot])
        session.flush()

        ai_result = AIAnalysisResult(
            stock_code="000001",
            agent_name="technical_agent",
            model_name="mock-fast",
            score=Decimal("76.0000"),
            direction="WATCH",
            confidence=Decimal("0.7000"),
            analysis_time=now,
            llm_usage_id=llm_usage.id,
        )
        ai_score = StockAIScore(
            stock_code="000001",
            time=now,
            final_score=Decimal("76.0000"),
            recommendation="WATCH",
            risk_level="LOW",
            confidence=Decimal("0.7000"),
            decision_snapshot_id=snapshot.id,
        )
        order_plan = OrderPlan(
            stock_code="000001",
            plan_date=today,
            plan_session="POST_MARKET",
            side="BUY",
            strategy_type="BALANCED",
            recommended_price=Decimal("10.1000"),
            status="DRAFT",
            decision_snapshot_id=snapshot.id,
        )
        session.add_all([ai_result, ai_score, order_plan])
        session.flush()

        prediction = PredictionRecord(
            stock_code="000001",
            prediction_time=now,
            prediction_horizon_days=1,
            expected_direction="UP",
            score=Decimal("76.0000"),
            confidence=Decimal("0.7000"),
            recommendation="WATCH",
            data_snapshot_id=snapshot.id,
            order_plan_id=order_plan.id,
        )
        candidate = OrderPriceCandidate(
            order_plan_id=order_plan.id,
            price_type="BALANCED",
            price=Decimal("10.1000"),
            score=Decimal("75.0000"),
            fill_probability=Decimal("0.6000"),
        )
        account = TradingAccount(
            name="AI Simulation",
            type="AI_SIMULATION",
            cash=Decimal("1000000.00"),
            total_asset=Decimal("1000000.00"),
            initial_cash=Decimal("1000000.00"),
        )
        session.add_all([prediction, candidate, account])
        session.flush()

        position = Position(
            account_id=account.id,
            stock_code="000001",
            quantity=100,
            cost_price=Decimal("10.1000"),
            available_quantity=0,
            buy_date=today,
        )
        trade_order = TradeOrder(
            account_id=account.id,
            stock_code="000001",
            action="BUY",
            order_price=Decimal("10.1000"),
            order_quantity=100,
            filled_quantity=100,
            status="FILLED",
            submit_time=now,
            filled_time=now,
            order_plan_id=order_plan.id,
        )
        trade_record = TradeRecord(
            account_id=account.id,
            stock_code="000001",
            action="BUY",
            price=Decimal("10.1000"),
            quantity=100,
            amount=Decimal("1010.00"),
            order_status="FILLED",
            time=now,
            prediction_record_id=prediction.id,
            order_plan_id=order_plan.id,
        )
        memory_a = AgentMemoryNote(
            agent_name="technical_agent",
            stock_code="000001",
            memory_type="short_term",
            layer="stock",
            title="test memory A",
            content="mock memory",
            quality_score=Decimal("0.9000"),
        )
        memory_b = AgentMemoryNote(
            agent_name="risk_agent",
            stock_code="000001",
            memory_type="reflection",
            layer="strategy",
            title="test memory B",
            content="mock reflection",
            quality_score=Decimal("0.8000"),
        )
        playbook = StrategyPlaybook(
            name="High open watch only",
            scenario="mock scenario",
            workflow="mock workflow",
            status="ACTIVE",
        )
        daily_review = DailyReview(
            date=today,
            account_id=account.id,
            market_summary="mock market",
            ai_summary="mock ai",
        )
        session.add_all(
            [
                position,
                trade_order,
                trade_record,
                memory_a,
                memory_b,
                playbook,
                daily_review,
            ]
        )
        session.flush()

        memory_link = MemoryLink(
            source_memory_id=memory_a.id,
            target_memory_id=memory_b.id,
            relation_type="EVIDENCE",
            strength=Decimal("0.5000"),
        )
        session.add(memory_link)
        session.commit()

        assert session.scalar(select(StockMaster).where(StockMaster.code == "000001"))
        assert session.scalar(select(StockMarketData).where(StockMarketData.stock_code == "000001"))
        assert session.scalar(select(StockFactorScore).where(StockFactorScore.stock_code == "000001"))
        assert session.scalar(select(NewsStockRelation).where(NewsStockRelation.stock_code == "000001"))
        assert session.scalar(select(AIAnalysisResult).where(AIAnalysisResult.stock_code == "000001"))
        assert session.scalar(select(OrderPriceCandidate).where(OrderPriceCandidate.order_plan_id == order_plan.id))
        assert session.scalar(select(TradeRecord).where(TradeRecord.account_id == account.id))
        assert session.scalar(select(MemoryLink).where(MemoryLink.source_memory_id == memory_a.id))
        assert session.scalar(select(StrategyPlaybook).where(StrategyPlaybook.name == "High open watch only"))
    finally:
        session.close()
        engine.dispose()
