from decimal import Decimal

from datasource.service import DataSourceService
from quant.ranking import QuantRankingEngine
from quant.schemas import QuantFactorInput


def build_inputs(count: int = 5) -> list[QuantFactorInput]:
    service = DataSourceService()
    market_provider = service.registry.get_default_market_provider()
    market_emotion = market_provider.get_market_emotion()
    inputs: list[QuantFactorInput] = []
    for stock in service.get_stock_universe()[:count]:
        quote = service.get_realtime_quotes([stock.stock_code])[0]
        inputs.append(
            QuantFactorInput(
                stock_code=stock.stock_code,
                stock_name=stock.name,
                industry=stock.industry,
                realtime_quote=quote,
                kline_bars=market_provider.get_kline(stock.stock_code, market_emotion.trade_date.replace(day=1), market_emotion.trade_date),
                finance_snapshot=market_provider.get_finance(stock.stock_code),
                capital_flow=market_provider.get_capital_flow(stock.stock_code),
                market_emotion=market_emotion,
            )
        )
    return inputs


def test_rank_stocks_orders_by_total_score_and_top_q() -> None:
    ranking = QuantRankingEngine().rank_stocks(build_inputs(8), top_q=3)

    assert ranking.universe_size == 8
    assert ranking.requested_top_q == 3
    assert ranking.returned_count == 3
    scores = [result.total_score for result in ranking.results]
    assert scores == sorted(scores, reverse=True)
    assert [result.rank for result in ranking.results] == [1, 2, 3]
    assert all(Decimal("0") <= result.total_score <= Decimal("100") for result in ranking.results)


def test_top_q_larger_than_universe_returns_all() -> None:
    ranking = QuantRankingEngine().rank_stocks(build_inputs(4), top_q=500)

    assert ranking.universe_size == 4
    assert ranking.returned_count == 4


def test_ranking_does_not_use_llm() -> None:
    ranking = QuantRankingEngine().rank_stocks(build_inputs(3), top_q=2)

    assert "LLM" in ranking.results[0].reason
    assert "without LLM calls" in ranking.results[0].reason
