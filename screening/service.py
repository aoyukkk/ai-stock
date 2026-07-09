from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from database.session import get_session
from llm_gateway.schemas import LLMMessage, LLMRequest
from llm_gateway.service import LLMGatewayService, get_llm_gateway_service
from quant.service import QuantService
from screening.config import LightScreeningConfig, load_light_screening_config
from screening.parser import parse_light_screening_output
from screening.persistence import persist_light_screening_results
from screening.prompt_builder import SYSTEM_PROMPT, build_light_screening_prompt
from screening.schemas import (
    LightScreeningInput,
    LightScreeningRanking,
    LightScreeningResult,
)
from screening.scoring import calculate_final_light_score, calculate_llm_score


class LightScreeningService:
    def __init__(
        self,
        quant_service: QuantService | None = None,
        llm_service: LLMGatewayService | None = None,
        config: LightScreeningConfig | None = None,
    ) -> None:
        self.config = config or load_light_screening_config()
        self.quant_service = quant_service or QuantService()
        self.llm_service = llm_service or get_llm_gateway_service()

    def run_light_screening(
        self,
        quant_top_q: int | None = None,
        top_n: int | None = None,
        persist: bool | None = None,
        session=None,
    ) -> LightScreeningRanking:
        self.config.validate_weights()
        requested_top_n = int(top_n or self.config.output_top_n)
        should_persist = self.config.persist_default if persist is None else persist
        quant_ranking = self.quant_service.run_quant_scan(top_q=quant_top_q, persist=False)
        screening_inputs = self._from_quant_results(quant_ranking.results)
        all_results = self._screen_batches(screening_inputs)
        kept_results = [result for result in all_results if result.should_keep]
        kept_results.sort(key=lambda item: item.final_light_score, reverse=True)
        selected = kept_results[: min(requested_top_n, len(kept_results))]
        for index, result in enumerate(selected, start=1):
            result.rank = index

        ranking = LightScreeningRanking(
            generated_at=datetime.now(timezone.utc),
            quant_universe_size=quant_ranking.universe_size,
            requested_quant_top_q=quant_ranking.requested_top_q,
            requested_top_n=requested_top_n,
            returned_count=len(selected),
            results=selected,
        )

        if should_persist:
            own_session = session is None
            db_session = session or get_session()
            try:
                persist_light_screening_results(db_session, ranking)
            finally:
                if own_session:
                    db_session.close()
        return ranking

    def config_summary(self) -> dict:
        return self.config.summary()

    def _from_quant_results(self, quant_results) -> list[LightScreeningInput]:
        inputs: list[LightScreeningInput] = []
        for result in quant_results:
            inputs.append(
                LightScreeningInput(
                    stock_code=result.stock_code,
                    stock_name=result.stock_name,
                    industry=result.industry,
                    quant_rank=int(result.rank or 0),
                    quant_total_score=result.total_score,
                    technical_score=result.technical_score,
                    capital_score=result.capital_score,
                    emotion_score=result.emotion_score,
                    momentum_score=result.momentum_score,
                    risk_score=result.risk_score,
                    quant_reason=result.reason,
                    latest_news_summary="Mock news summary only; no real news source used.",
                    overseas_summary="Mock overseas snapshot only.",
                    liquidity_summary=f"Capital score {result.capital_score}.",
                )
            )
        return inputs

    def _screen_batches(self, inputs: list[LightScreeningInput]) -> list[LightScreeningResult]:
        results: list[LightScreeningResult] = []
        batch_size = max(1, self.config.batch_size)
        for start in range(0, len(inputs), batch_size):
            batch = inputs[start : start + batch_size]
            prompt = build_light_screening_prompt(batch)
            response = self.llm_service.chat(
                LLMRequest(
                    agent_name="light_screening_agent",
                    task="light_screening",
                    messages=[
                        LLMMessage(role="system", content=SYSTEM_PROMPT),
                        LLMMessage(role="user", content=prompt),
                    ],
                    provider=self.config.provider,
                    model=self.config.model,
                    prompt_version="v0.3-phase6",
                    metadata={
                        "structured": True,
                        "screening_inputs": [
                            item.model_dump(mode="json")
                            for item in batch
                        ],
                    },
                )
            )
            outputs = {
                item.stock_code: item
                for item in parse_light_screening_output(response.content)
            }
            for item in batch:
                output = outputs[item.stock_code]
                llm_score = calculate_llm_score(output)
                final_score = calculate_final_light_score(
                    quant_score=item.quant_total_score,
                    llm_output=output,
                    weights=self.config.score_weights,
                )
                should_keep = bool(output.should_keep and output.confidence >= self.config.min_confidence)
                results.append(
                    LightScreeningResult(
                        stock_code=item.stock_code,
                        stock_name=item.stock_name,
                        industry=item.industry,
                        quant_rank=item.quant_rank,
                        quant_total_score=item.quant_total_score,
                        llm_score=llm_score,
                        final_light_score=final_score,
                        direction=output.direction,
                        confidence=output.confidence,
                        should_keep=should_keep,
                        reason=output.reason,
                        risk_note=output.risk_note,
                        prompt_version=response.prompt_version,
                        model_name=response.model,
                        request_hash=response.request_hash,
                    )
                )
        return results
