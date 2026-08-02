from __future__ import annotations

from scripts.build_v2_weekly_threshold_review import _threshold_scan


def test_threshold_scan_keeps_minimum_sample_and_excludes_weak_tail() -> None:
    rows = []
    for index, score in enumerate(range(90, 80, -1)):
        net_return = 0.02 if index < 8 else -0.20
        rows.append(
            {
                "pro_score": float(score),
                "eligible": True,
                "current_net_return": net_return,
                "mfe": 0.04 if net_return > 0 else 0.01,
                "mae": -0.01 if net_return > 0 else -0.22,
                "giveback": 0.02,
                "stop_hit": net_return < 0,
                "result_class": "STABLE_SUCCESS" if net_return > 0 else "FAIL",
            }
        )

    threshold, scans = _threshold_scan(rows)

    assert threshold == 83.0
    chosen = next(row for row in scans if row["threshold"] == threshold)
    assert chosen["eligible_count"] == 8
    assert chosen["average_net_return"] == 0.02
