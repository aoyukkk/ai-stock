from __future__ import annotations

from openpyxl import Workbook

from scripts.run_close_once import _workbook_issue_count


def test_completion_check_counts_only_populated_issue_rows(tmp_path):
    path = tmp_path / "daily.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["title"])
    worksheet.append([])
    worksheet.append(["summary"])
    worksheet.append(["code", "name", "type", "status", "description"])
    worksheet.append(["无待处理事项"])
    workbook.save(path)

    assert _workbook_issue_count(path) == 0

    workbook = Workbook()
    worksheet = workbook.active
    for _ in range(4):
        worksheet.append([])
    worksheet.append(["603726", "朗迪集团", "基本面", "待处理", "说明"])
    workbook.save(path)

    assert _workbook_issue_count(path) == 1
