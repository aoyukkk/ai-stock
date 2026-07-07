from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from database.base import Base
from database.models import (
    AIAnalysisResult,
    AgentExecutionLog,
    AgentMemory,
    ConfigHistory,
    DailyReview,
    DecisionSnapshot,
    ExperimentRun,
    LLMUsage,
    ModelVersion,
    News,
    NewsStockRelation,
    OrderPlan,
    OrderPlanEvaluation,
    OrderPriceCandidate,
    OrderReassessmentLog,
    Position,
    PredictionEvaluation,
    PredictionRecord,
    PreMarketAuction,
    PromptVersion,
    StockAIScore,
    StockFactorDetail,
    StockFactorScore,
    StockFinance,
    StockMarketData,
    StockMaster,
    TradeOrder,
    TradeRecord,
    TradingAccount,
)


NOW = datetime(2026, 7, 7, 9, 30, tzinfo=timezone.utc)
TODAY = date(2026, 7, 7)

MODEL_CLASSES = [
    StockMaster,
    StockMarketData,
    PreMarketAuction,
    StockFinance,
    StockFactorScore,
    StockFactorDetail,
    News,
    NewsStockRelation,
    AIAnalysisResult,
    StockAIScore,
    PredictionRecord,
    DecisionSnapshot,
    OrderPlan,
    OrderPriceCandidate,
    OrderPlanEvaluation,
    OrderReassessmentLog,
    ExperimentRun,
    AgentExecutionLog,
    LLMUsage,
    PromptVersion,
    ModelVersion,
    AgentMemory,
    TradingAccount,
    Position,
    TradeRecord,
    TradeOrder,
    DailyReview,
    PredictionEvaluation,
    ConfigHistory,
]

INSTANCE_FACTORIES = [
    lambda: StockMaster(id=1, code="000001", name="Test Stock"),
    lambda: StockMarketData(id=1, stock_code="000001", datetime=NOW),
    lambda: PreMarketAuction(id=1, stock_code="000001", trade_date=TODAY),
    lambda: StockFinance(id=1, stock_code="000001", date=TODAY),
    lambda: StockFactorScore(id=1, stock_code="000001", date=TODAY),
    lambda: StockFactorDetail(id=1, stock_code="000001", date=TODAY),
    lambda: News(id=1, title="market news", publish_time=NOW),
    lambda: NewsStockRelation(id=1, news_id=1, stock_code="000001"),
    lambda: AIAnalysisResult(id=1, stock_code="000001", agent_name="technical_agent"),
    lambda: StockAIScore(id=1, stock_code="000001", time=NOW),
    lambda: PredictionRecord(id=1, stock_code="000001", prediction_time=NOW),
    lambda: DecisionSnapshot(id=1, stock_code="000001", snapshot_time=NOW),
    lambda: OrderPlan(id=1, stock_code="000001", plan_date=TODAY),
    lambda: OrderPriceCandidate(id=1, order_plan_id=1, price_type="BALANCED"),
    lambda: OrderPlanEvaluation(id=1, order_plan_id=1, stock_code="000001"),
    lambda: OrderReassessmentLog(id=1, order_plan_id=1, stock_code="000001"),
    lambda: ExperimentRun(id=1, name="baseline"),
    lambda: AgentExecutionLog(id=1, agent_name="controller_agent"),
    lambda: LLMUsage(id=1, provider="mock", model_name="mock-model"),
    lambda: PromptVersion(id=1, agent_name="technical_agent", version="v1"),
    lambda: ModelVersion(id=1, provider="mock", model_name="mock-model"),
    lambda: AgentMemory(id=1, agent="risk_agent", memory_type="short"),
    lambda: TradingAccount(id=1, name="AI Simulation", type="ai_simulation"),
    lambda: Position(id=1, account_id=1, stock_code="000001"),
    lambda: TradeRecord(id=1, account_id=1, stock_code="000001"),
    lambda: TradeOrder(id=1, account_id=1, stock_code="000001"),
    lambda: DailyReview(id=1, date=TODAY, account_id=1),
    lambda: PredictionEvaluation(id=1, prediction_record_id=1, stock_code="000001"),
    lambda: ConfigHistory(id=1, config_key="system.log_level", time=NOW),
]


def create_sqlite_memory_engine():
    return create_engine("sqlite+pysqlite:///:memory:", future=True)


def test_all_models_can_be_imported() -> None:
    assert len(MODEL_CLASSES) == 29
    for model_class in MODEL_CLASSES:
        assert model_class.__tablename__ in Base.metadata.tables


def test_base_metadata_collects_expected_tables() -> None:
    expected_tables = {model_class.__tablename__ for model_class in MODEL_CLASSES}
    assert expected_tables.issubset(set(Base.metadata.tables))


def test_table_count_is_not_less_than_25() -> None:
    assert len(Base.metadata.tables) >= 25


def test_required_model_instances_can_be_created() -> None:
    for factory in INSTANCE_FACTORIES:
        instance = factory()
        assert instance is not None


def test_sqlite_memory_database_can_create_all_tables() -> None:
    engine = create_sqlite_memory_engine()
    Base.metadata.create_all(engine)
    assert len(Base.metadata.sorted_tables) >= 25


def test_insert_and_query_test_stock() -> None:
    engine = create_sqlite_memory_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(StockMaster(id=1001, code="000001", name="Test Stock"))
        session.commit()

        result = session.execute(
            select(StockMaster).where(StockMaster.code == "000001")
        ).scalar_one()

    assert result.name == "Test Stock"


def test_insert_and_query_order_plan() -> None:
    engine = create_sqlite_memory_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            OrderPlan(
                id=2001,
                stock_code="000001",
                plan_date=TODAY,
                plan_session="POST_MARKET",
                side="BUY",
                strategy_type="BALANCED",
                recommended_price=Decimal("10.50"),
                status="DRAFT",
            )
        )
        session.commit()

        result = session.execute(
            select(OrderPlan).where(OrderPlan.stock_code == "000001")
        ).scalar_one()

    assert result.status == "DRAFT"
