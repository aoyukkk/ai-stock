from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_performance_route_controls_tables_and_centered_component() -> None:
    router = (ROOT / "frontend/src/router/index.ts").read_text(encoding="utf-8")
    layout = (ROOT / "frontend/src/layouts/MainLayout.vue").read_text(encoding="utf-8")
    view = (ROOT / "frontend/src/views/SelectionPerformanceView.vue").read_text(encoding="utf-8")
    table = (ROOT / "frontend/src/components/common/CenteredDataTable.vue").read_text(encoding="utf-8")
    assert "selection-performance" in router
    assert "选股收益统计" in layout
    assert "lookback_value: 5" in view
    assert view.count("<CenteredDataTable") >= 4
    assert "PerformanceCharts" in view
    assert "NEXT_OPEN" in view and "SIGNAL_CLOSE" in view
    assert "SUGGESTED_POSITION_WEIGHT" in view
    assert 'align="center"' in table and 'header-align="center"' in table
    assert "row-dblclick" in table
