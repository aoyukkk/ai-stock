from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = (ROOT / "outputs").resolve()
DATE_PATTERN = re.compile(r"(20\d{2})(\d{2})(\d{2})")


def main() -> int:
    parser = argparse.ArgumentParser(description="按交易日整理输出文件")
    parser.add_argument("--current-date", default="2026-07-10")
    args = parser.parse_args()
    current_date = args.current_date
    _assert_inside_outputs(OUTPUTS)

    moved: list[dict[str, str]] = []
    moved.extend(_move_legacy_tree("trader_demo", "交易演示", "2026-07-09"))
    moved.extend(_move_legacy_tree("validation", "验证", "2026-07-09"))
    moved.extend(_move_daily_full_test(current_date))
    moved.extend(_tidy_human_artifacts(current_date))
    copied = _copy_current_reports(current_date)
    checkpoint = _update_current_checkpoint(current_date)
    _remove_empty_legacy_directories()

    result = {
        "状态": "完成", "输出根目录": str(OUTPUTS), "当前日期目录": str(OUTPUTS / current_date),
        "移动项目数": len(moved), "复制审计文件数": len(copied),
        "当前检查点": str(checkpoint), "移动记录": moved, "复制记录": copied,
    }
    manifest = OUTPUTS / current_date / "审计" / "目录整理记录.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    if manifest.exists():
        previous = json.loads(manifest.read_text(encoding="utf-8"))
        result["累计移动项目数"] = int(previous.get("累计移动项目数", previous.get("移动项目数", 0))) + len(moved)
        result["最近检查移动项目数"] = len(moved)
    else:
        result["累计移动项目数"] = len(moved)
        result["最近检查移动项目数"] = len(moved)
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"移动记录", "复制记录"}}, ensure_ascii=False, indent=2))
    return 0


def _move_legacy_tree(name: str, category: str, fallback_date: str) -> list[dict[str, str]]:
    source_root = (OUTPUTS / name).resolve()
    if not source_root.exists():
        return []
    _assert_inside_outputs(source_root)
    moved = []
    for source in list(source_root.iterdir()):
        trade_date = _date_from_name(source.name) or fallback_date
        target = OUTPUTS / trade_date / "历史版本" / category / source.name
        moved.append(_safe_move(source, target))
    return moved


def _move_daily_full_test(current_date: str) -> list[dict[str, str]]:
    source_root = (OUTPUTS / "daily_full_test").resolve()
    if not source_root.exists():
        return []
    _assert_inside_outputs(source_root)
    moved = []
    for source in list(source_root.iterdir()):
        name = source.name
        trade_date = _date_from_name(name) or current_date
        if name == "flash_v4_checkpoint.json":
            target = OUTPUTS / trade_date / "审计" / name
        elif name == "daily_full_pipeline_checkpoint.json":
            target = OUTPUTS / trade_date / "审计" / "历史检查点_ProV2.json"
        elif name == "ai_trader_flash_v4_20260710_state_clean_audit.json":
            target = OUTPUTS / trade_date / "审计" / "机器版完整审计.json"
        else:
            target = OUTPUTS / trade_date / "历史版本" / "完整流水线" / name
        moved.append(_safe_move(source, target))
    return moved


def _tidy_human_artifacts(current_date: str) -> list[dict[str, str]]:
    daily_root = (OUTPUTS / current_date).resolve()
    _assert_inside_outputs(daily_root)
    moved = []
    for source in daily_root.glob("*.inspect.ndjson"):
        moved.append(_safe_move(source, daily_root / "预览" / "人工阅读版_结构检查.ndjson"))
    return moved


def _copy_current_reports(current_date: str) -> list[dict[str, str]]:
    audit_dir = (OUTPUTS / current_date / "审计").resolve()
    _assert_inside_outputs(audit_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)
    mappings = {
        ROOT / "data" / "reports" / "flash_v4_final_pipeline_20260710_report.json": audit_dir / "完整流水线报告.json",
        ROOT / "data" / "reports" / "flash_v4_final_pytest_20260710.txt": audit_dir / "测试结果.txt",
    }
    copied = []
    for source, target in mappings.items():
        if not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append({"源": str(source), "目标": str(target)})
    return copied


def _update_current_checkpoint(current_date: str) -> Path:
    daily_root = (OUTPUTS / current_date).resolve()
    checkpoint_path = daily_root / "审计" / "flash_v4_checkpoint.json"
    workbook = daily_root / f"智能交易助手_{current_date}_人工阅读版.xlsx"
    if not checkpoint_path.exists() or not workbook.exists():
        raise FileNotFoundError("CURRENT_CHECKPOINT_OR_HUMAN_WORKBOOK_MISSING")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    machine_workbook = daily_root / "历史版本" / "完整流水线" / "ai_trader_flash_v4_20260710_state_clean.xlsx"
    checkpoint.update({
        "stage": "COMPLETED", "final_status": "PARTIAL_SUCCESS",
        "excel_path": str(workbook), "workbook_sha256": _sha256(workbook),
        "machine_excel_path": str(machine_workbook) if machine_workbook.exists() else None,
        "output_layout": "outputs/YYYY-MM-DD",
    })
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    return checkpoint_path


def _safe_move(source: Path, target: Path) -> dict[str, str]:
    source = source.resolve()
    target = target.resolve()
    _assert_inside_outputs(source)
    _assert_inside_outputs(target)
    if not source.exists():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        suffix = _sha256(source)[:8] if source.is_file() else hashlib.sha256(source.name.encode()).hexdigest()[:8]
        target = target.with_name(f"{target.stem}_{suffix}{target.suffix}")
        _assert_inside_outputs(target)
    shutil.move(str(source), str(target))
    return {"源": str(source), "目标": str(target)}


def _date_from_name(name: str) -> str | None:
    match = DATE_PATTERN.search(name)
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else None


def _assert_inside_outputs(path: Path) -> None:
    resolved = path.resolve()
    if resolved != OUTPUTS and OUTPUTS not in resolved.parents:
        raise ValueError(f"PATH_OUTSIDE_OUTPUTS:{resolved}")


def _remove_empty_legacy_directories() -> None:
    for name in ("daily_full_test", "trader_demo", "validation"):
        path = OUTPUTS / name
        if path.exists() and not any(path.iterdir()):
            path.rmdir()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    else:
        digest.update(path.name.encode())
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
