from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

import win32com.client


def main() -> int:
    parser = argparse.ArgumentParser(description="Render workbook sheet previews with installed Microsoft Excel.")
    parser.add_argument("workbook", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--max-rows", type=int, default=35)
    parser.add_argument("--max-cols", type=int, default=20)
    args = parser.parse_args()
    workbook_path = args.workbook.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    book = None
    try:
        book = excel.Workbooks.Open(str(workbook_path), ReadOnly=True)
        for sheet in book.Worksheets:
            sheet.Activate()
            used = sheet.UsedRange
            row_count = min(int(used.Rows.Count), args.max_rows)
            col_count = min(int(used.Columns.Count), args.max_cols)
            preview = sheet.Range(sheet.Cells(1, 1), sheet.Cells(max(1, row_count), max(1, col_count)))
            for attempt in range(3):
                try:
                    excel.CutCopyMode = False
                    excel.Goto(preview, True)
                    time.sleep(0.2)
                    preview.CopyPicture(Appearance=1, Format=2)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(0.5)
            chart_object = sheet.ChartObjects().Add(0, 0, max(900, float(preview.Width)), max(420, float(preview.Height)))
            try:
                chart_object.Chart.Paste()
                safe_name = re.sub(r'[\\/:*?"<>|]', "_", str(sheet.Name))
                chart_object.Chart.Export(str(output_dir / f"{safe_name}.png"), "PNG")
            finally:
                chart_object.Delete()
    finally:
        if book is not None:
            book.Close(False)
        excel.Quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
