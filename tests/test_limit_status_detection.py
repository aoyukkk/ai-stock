from __future__ import annotations

from scripts.run_real_quant_top500 import _detect_limit_status


def test_limit_status_detection_values() -> None:
    assert _detect_limit_status(11.0, 11.0, 9.0) == "LIMIT_UP_CLOSE"
    assert _detect_limit_status(10.82, 11.0, 9.0) == "NEAR_LIMIT_UP"
    assert _detect_limit_status(9.0, 11.0, 9.0) == "LIMIT_DOWN_CLOSE"
    assert _detect_limit_status(9.15, 11.0, 9.0) == "NEAR_LIMIT_DOWN"
    assert _detect_limit_status(10.0, 11.0, 9.0) == "NORMAL"
    assert _detect_limit_status(10.0, 0.0, 0.0) == "NORMAL"
