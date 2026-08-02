from __future__ import annotations

from scripts.build_today_sector_success_rate import _sample_status


def test_today_sector_success_rate_sample_status_is_explicit() -> None:
    assert _sample_status(0) == "暂无成熟样本"
    assert _sample_status(1) == "样本极少"
    assert _sample_status(4) == "样本极少"
    assert _sample_status(5) == "样本较少"
    assert _sample_status(9) == "样本较少"
    assert _sample_status(10) == "可初步观察"
