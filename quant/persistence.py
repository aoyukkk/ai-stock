from __future__ import annotations

from database.models.factor import StockFactorDetail, StockFactorScore
from quant.schemas import QuantRankingResult


def save_factor_scores(session, ranking_result: QuantRankingResult) -> None:
    for result in ranking_result.results:
        score_record = StockFactorScore(
            stock_code=result.stock_code,
            date=ranking_result.generated_at.date(),
            technical_score=result.technical_score,
            capital_score=result.capital_score,
            emotion_score=result.emotion_score,
            momentum_score=result.momentum_score,
            risk_score=result.risk_score,
            total_score=result.total_score,
            factor_version=result.factor_version,
        )
        session.add(score_record)
        for detail in result.factor_details:
            session.add(
                StockFactorDetail(
                    stock_code=detail.stock_code,
                    date=ranking_result.generated_at.date(),
                    factor_group=detail.factor_group,
                    factor_name=detail.factor_name,
                    raw_value=detail.raw_value,
                    normalized_value=detail.normalized_value,
                    score=detail.score,
                    weight=detail.weight,
                    factor_version=result.factor_version,
                    explain_text=detail.explain_text,
                )
            )
    session.commit()
