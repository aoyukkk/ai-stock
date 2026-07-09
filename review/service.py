from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from backend.core.exceptions import AppException
from database.session import get_session, init_db
from llm_gateway.service import LLMGatewayService
from review.comparison import AccountComparison, find_human_account_id
from review.config import ReviewConfig, load_review_config
from review.order_plan_evaluator import OrderPlanEvaluator
from review.persistence import (
    daily_review_to_result,
    get_latest_daily_review,
    persist_daily_review,
)
from review.portfolio_evaluator import PortfolioEvaluator
from review.prediction_evaluator import PredictionEvaluator
from review.schemas import (
    DailyReviewResult,
    OrderPlanEvaluationResult,
    PredictionEvaluationResult,
)
from review.summary_builder import build_mock_llm_summary, build_rule_based_summary


class DailyReviewService:
    def __init__(
        self,
        session=None,
        config: ReviewConfig | None = None,
        llm_gateway: LLMGatewayService | None = None,
    ) -> None:
        self.session = session
        self.config = config or load_review_config()
        self.llm_gateway = llm_gateway
        self.config.validate()

    def run_daily_review(
        self,
        review_date: date | None = None,
        account_id: int | None = None,
        use_mock_llm: bool | None = None,
    ) -> DailyReviewResult:
        target_date = review_date or date.today()
        session, own_session = self._session()
        try:
            prediction_results = PredictionEvaluator(session=session, config=self.config).evaluate_predictions_for_date(target_date)
            order_results = OrderPlanEvaluator(session=session, config=self.config).evaluate_order_plans_for_date(target_date)
            portfolio_result = (
                PortfolioEvaluator(session=session).evaluate_account(account_id, target_date)
                if account_id is not None
                else PortfolioEvaluator(session=session).evaluate_ai_simulation(target_date)
            )
            human_account_id = find_human_account_id(session)
            comparison_result = AccountComparison(session=session).compare_ai_vs_human(
                ai_account_id=portfolio_result.account_id,
                human_account_id=human_account_id,
                evaluation_date=target_date,
            )
            module_scores = self._module_scores(prediction_results, order_results, portfolio_result)
            final_review_score = self._final_review_score(module_scores)
            rule_summary = build_rule_based_summary(
                prediction_results=prediction_results,
                order_results=order_results,
                portfolio_result=portfolio_result,
                comparison_result=comparison_result,
                module_scores=module_scores,
            )
            should_use_mock_llm = self.config.use_mock_llm_summary if use_mock_llm is None else bool(use_mock_llm)
            ai_summary = rule_summary["ai_summary"]
            if should_use_mock_llm:
                mock_llm_text = build_mock_llm_summary(
                    rule_summary=rule_summary,
                    module_scores=module_scores,
                    llm_gateway=self.llm_gateway,
                )
                ai_summary = f"{ai_summary}\nMock LLM summary: {mock_llm_text}"

            result = DailyReviewResult(
                date=target_date,
                account_id=portfolio_result.account_id,
                market_summary=rule_summary["market_summary"],
                ai_summary=ai_summary,
                human_summary=rule_summary["human_summary"],
                prediction_accuracy=module_scores["prediction_accuracy"],
                order_price_quality=module_scores["order_price_quality"],
                profit_loss=portfolio_result.profit_loss,
                max_drawdown=portfolio_result.max_drawdown,
                win_rate=portfolio_result.win_rate,
                mistake_analysis=rule_summary["mistake_analysis"],
                suggestion=rule_summary["suggestion"],
                final_review_score=final_review_score,
                module_scores=module_scores,
                created_at=datetime.now(timezone.utc),
            )
            persisted = persist_daily_review(session, result)
            return daily_review_to_result(persisted)
        finally:
            if own_session:
                session.close()

    def get_daily_review(self, review_date: date) -> DailyReviewResult:
        session, own_session = self._session()
        try:
            review = get_latest_daily_review(session, review_date)
            if review is None:
                raise AppException("DAILY_REVIEW_NOT_FOUND", "Daily review not found.", status_code=404)
            return daily_review_to_result(review)
        finally:
            if own_session:
                session.close()

    def evaluate_predictions(self, review_date: date | None = None) -> list[PredictionEvaluationResult]:
        target_date = review_date or date.today()
        session, own_session = self._session()
        try:
            return PredictionEvaluator(session=session, config=self.config).evaluate_predictions_for_date(target_date)
        finally:
            if own_session:
                session.close()

    def evaluate_order_plans(self, review_date: date | None = None) -> list[OrderPlanEvaluationResult]:
        target_date = review_date or date.today()
        session, own_session = self._session()
        try:
            return OrderPlanEvaluator(session=session, config=self.config).evaluate_order_plans_for_date(target_date)
        finally:
            if own_session:
                session.close()

    def config_summary(self) -> dict:
        return self.config.summary()

    def _module_scores(
        self,
        prediction_results: list[PredictionEvaluationResult],
        order_results: list[OrderPlanEvaluationResult],
        portfolio_result,
    ) -> dict[str, Decimal]:
        prediction_score = self._prediction_accuracy(prediction_results)
        order_score = self._order_price_quality(order_results)
        portfolio_score = self._portfolio_score(portfolio_result.profit_loss_percent)
        risk_score = self._risk_control_score(portfolio_result.max_drawdown)
        return {
            "prediction_accuracy": prediction_score,
            "order_price_quality": order_score,
            "portfolio_performance": portfolio_score,
            "risk_control": risk_score,
        }

    def _prediction_accuracy(self, results: list[PredictionEvaluationResult]) -> Decimal:
        evaluated = [result for result in results if result.is_correct is not None]
        if not evaluated:
            return Decimal("0.0000")
        correct = len([result for result in evaluated if result.is_correct])
        return _score(Decimal(correct) / Decimal(len(evaluated)) * Decimal("100"))

    def _order_price_quality(self, results: list[OrderPlanEvaluationResult]) -> Decimal:
        if not results:
            return Decimal("0.0000")
        total = sum(result.price_quality_score for result in results)
        return _score(total / Decimal(len(results)))

    def _portfolio_score(self, profit_loss_percent: Decimal) -> Decimal:
        return _score(Decimal("50") + profit_loss_percent)

    def _risk_control_score(self, max_drawdown: Decimal) -> Decimal:
        threshold = self.config.thresholds["max_acceptable_drawdown_percent"]
        if max_drawdown <= 0:
            return Decimal("100.0000")
        penalty = (max_drawdown / threshold) * Decimal("100")
        return _score(Decimal("100") - penalty)

    def _final_review_score(self, module_scores: dict[str, Decimal]) -> Decimal:
        weights = self.config.scoring
        final_score = (
            module_scores["prediction_accuracy"] * weights["prediction_accuracy_weight"]
            + module_scores["order_price_quality"] * weights["order_price_quality_weight"]
            + module_scores["portfolio_performance"] * weights["portfolio_performance_weight"]
            + module_scores["risk_control"] * weights["risk_control_weight"]
        )
        return _score(final_score)

    def _session(self):
        if self.session is not None:
            return self.session, False
        init_db()
        return get_session(), True


def _score(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("100"), value)).quantize(Decimal("0.0001"))
