from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from post_close.actions import (
    ActionHealthInput, CandidateAction, HardGateInput, HeldAction, PositionFacts,
    PostCloseActionHealthEngine, enforce_conservative_pro_review, target_day_sellable,
)
from post_close.service import _market_session, _next_trade_date_from_cache


CONFIG = {
    "thresholds": {"continue_hold": 75, "hold_with_tight_stop": 60, "reduce_position": 45, "exit_below": 45},
    "candidate_thresholds": {"prepare_entry": 70, "keep_watch": 50, "remove_below": 40},
    "reduce": {"mild_percent": .25, "medium_percent": .5, "severe_percent": .75, "lot_size": 100},
}


def health(score: float) -> ActionHealthInput:
    return ActionHealthInput(score, score, score, score, score, score, score, score)


def test_held_action_thresholds_and_lot_size():
    engine = PostCloseActionHealthEngine(CONFIG)
    position = PositionFacts(True, 1000, 1000, 1000)
    assert engine.decide(health(80), position, HardGateInput()).action == HeldAction.CONTINUE_HOLD
    assert engine.decide(health(65), position, HardGateInput()).action == HeldAction.HOLD_WITH_TIGHT_STOP
    reduced = engine.decide(health(50), position, HardGateInput())
    assert reduced.action == HeldAction.REDUCE_POSITION
    assert reduced.suggested_reduce_quantity == 200
    assert engine.decide(health(30), position, HardGateInput()).action == HeldAction.EXIT_NEXT_SESSION


def test_t_plus_one_and_untradable_hard_gates_override_score():
    engine = PostCloseActionHealthEngine(CONFIG)
    locked = PositionFacts(True, 1000, 0, 1000, bought_today_quantity=1000)
    assert engine.decide(health(90), locked, HardGateInput(stop_breached=True)).action == HeldAction.T_PLUS_ONE_LOCKED_EXIT_PLAN
    suspended = PositionFacts(True, 1000, 0, 0, suspended=True)
    assert engine.decide(health(90), suspended, HardGateInput(hard_risk=True)).action == HeldAction.EXIT_WHEN_TRADABLE
    assert target_day_sellable(1000, 0, 1000, target_is_next_session=True) == 1000


def test_non_held_candidates_never_receive_sell_or_exit_actions():
    engine = PostCloseActionHealthEngine(CONFIG)
    actions = {engine.decide(health(score), PositionFacts(False), HardGateInput()).action for score in (90, 60, 45, 20)}
    assert actions <= {item.value for item in CandidateAction}
    assert not any("EXIT" in action or "REDUCE" in action for action in actions)
    missing = engine.decide(health(90), PositionFacts(False, position_data_complete=False), HardGateInput(critical_data_missing=True))
    assert missing.action == CandidateAction.MANUAL_REVIEW


def test_pro_review_cannot_make_held_action_less_conservative_or_change_candidate_to_sell():
    assert enforce_conservative_pro_review(HeldAction.REDUCE_POSITION, HeldAction.CONTINUE_HOLD, held=True) == HeldAction.REDUCE_POSITION
    assert enforce_conservative_pro_review(HeldAction.CONTINUE_HOLD, HeldAction.EXIT_NEXT_SESSION, held=True) == HeldAction.EXIT_NEXT_SESSION
    assert enforce_conservative_pro_review(CandidateAction.KEEP_WATCH, HeldAction.EXIT_NEXT_SESSION, held=False) == CandidateAction.MANUAL_REVIEW


def test_missing_optional_scores_are_reweighted_not_zeroed():
    engine = PostCloseActionHealthEngine(CONFIG)
    value = ActionHealthInput(base_quant_score=80, risk_health_score=80)
    assert engine.health_score(value) == 80


def test_target_trade_date_uses_cached_trade_calendar(tmp_path):
    cache = tmp_path / "data" / "cache" / "tushare"
    cache.mkdir(parents=True)
    (cache / "trade_cal_test.json").write_text(json.dumps([
        {"cal_date": "20261001", "is_open": 0},
        {"cal_date": "20261009", "is_open": 1},
    ]), encoding="utf-8")
    assert _next_trade_date_from_cache(tmp_path, date(2026, 9, 30)) == date(2026, 10, 9)


def test_post_close_session_starts_at_1501_shanghai_time():
    timezone = ZoneInfo("Asia/Shanghai")
    trade_date = date(2026, 7, 15)
    assert _market_session(datetime(2026, 7, 15, 15, 0, tzinfo=timezone), trade_date) == "AFTERNOON_SESSION"
    assert _market_session(datetime(2026, 7, 15, 15, 1, tzinfo=timezone), trade_date) == "POST_MARKET"
