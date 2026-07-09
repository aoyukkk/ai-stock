from __future__ import annotations

from datetime import timedelta

from datasource.service import DataSourceService
from database.session import get_session
from quant.config import QuantConfig, load_quant_config
from quant.persistence import save_factor_scores
from quant.ranking import QuantRankingEngine
from quant.schemas import QuantFactorInput, QuantRankingResult


class QuantService:
    def __init__(
        self,
        data_source_service: DataSourceService | None = None,
        engine: QuantRankingEngine | None = None,
        config: QuantConfig | None = None,
    ) -> None:
        self.config = config or load_quant_config()
        self.data_source_service = data_source_service or DataSourceService()
        self.engine = engine or QuantRankingEngine(config=self.config)
        self._latest_ranking: QuantRankingResult | None = None

    def run_quant_scan(
        self,
        top_q: int | None = None,
        persist: bool = False,
        session=None,
    ) -> QuantRankingResult:
        requested_top_q = int(top_q or self.config.top_q_default)
        inputs = self._build_inputs()
        ranking = self.engine.rank_stocks(inputs, requested_top_q)

        if persist:
            own_session = session is None
            db_session = session or get_session()
            try:
                save_factor_scores(db_session, ranking)
            finally:
                if own_session:
                    db_session.close()

        self._latest_ranking = ranking
        return ranking

    def get_latest_ranking(self, limit: int = 50) -> QuantRankingResult | None:
        if self._latest_ranking is None:
            return None
        copied = self._latest_ranking.model_copy(deep=True)
        copied.results = copied.results[:limit]
        copied.returned_count = len(copied.results)
        return copied

    def _build_inputs(self) -> list[QuantFactorInput]:
        stocks = self.data_source_service.get_stock_universe()
        quotes_by_code = {
            quote.stock_code: quote
            for quote in self.data_source_service.get_realtime_quotes(
                [stock.stock_code for stock in stocks]
            )
        }
        market_emotion = self.data_source_service.registry.get_default_market_provider().get_market_emotion()
        inputs: list[QuantFactorInput] = []
        end_date = market_emotion.trade_date
        start_date = end_date - timedelta(days=29)
        market_provider = self.data_source_service.registry.get_default_market_provider()

        for stock in stocks:
            quote = quotes_by_code.get(stock.stock_code)
            if quote is None:
                continue
            kline_bars = market_provider.get_kline(
                stock.stock_code,
                start_date=start_date,
                end_date=end_date,
                frequency="1d",
            )
            inputs.append(
                QuantFactorInput(
                    stock_code=stock.stock_code,
                    stock_name=stock.name,
                    industry=stock.industry,
                    realtime_quote=quote,
                    kline_bars=kline_bars,
                    finance_snapshot=market_provider.get_finance(stock.stock_code),
                    capital_flow=market_provider.get_capital_flow(stock.stock_code),
                    market_emotion=market_emotion,
                )
            )
        return inputs
