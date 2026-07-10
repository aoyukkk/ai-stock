from scripts.build_human_daily_output import _human_text, _human_warning, _yes_no
from scripts.organize_outputs_by_date import _date_from_name


def test_human_output_translates_machine_statuses():
    assert _human_text("WATCH_ONLY") == "普通观察"
    assert _human_text("STABLE") == "稳定"
    assert _human_text("MULTI_SEGMENT") == "多环节"


def test_human_output_removes_repeated_disclaimer_and_translates_warnings():
    value = _human_warning(
        "LLM_UNVERIFIED_POSITION_DISCOUNT_APPLIED；仅供模型验证，不可作为正式交易仓位建议",
        "BELOW_ONE_TRADING_LOT；仅供模型验证，不可作为正式交易仓位建议",
    )
    assert value == "未核验信息已按规则降权；建议金额不足一手"
    assert "LLM" not in value
    assert "仅供模型验证" not in value


def test_human_output_yes_no_handles_chinese_false_value():
    assert _yes_no("是") == "是"
    assert _yes_no("否") == "否"
    assert _yes_no("") == "否"


def test_human_output_translates_technical_terms_and_removes_provenance():
    value = _human_text("603726：智能SoC、GPU、PVC：P：stock_company：main_business")
    assert value == "智能系统级芯片、图形处理芯片、聚氯乙烯"


def test_output_organizer_extracts_date_from_artifact_name():
    assert _date_from_name("ai_trader_demo_20260709.xlsx") == "2026-07-09"
    assert _date_from_name("flash_v4_checkpoint.json") is None
