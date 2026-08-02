from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from database.models.postclose_official import PostCloseOfficialRun
from database.session import get_session, init_db
from reporting.immutable_workbook import workbook_content_style_hashes
from reporting.workbook_standard import validate_trading_assistant_workbook
from reporting.workbook_style import WorkbookStyleService


SHANGHAI = ZoneInfo("Asia/Shanghai")
OFFICIAL_SUCCESS = {
    "POSTCLOSE_FULL_A_SUCCESS",
    "POSTCLOSE_FULL_A_PARTIAL_SUCCESS",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Atomically replace an official daily workbook with an audited validated repair."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--official-run-id", required=True)
    parser.add_argument("--verification", type=Path, required=True)
    args = parser.parse_args()

    source = args.source.resolve()
    target = args.target.resolve()
    verification = args.verification.resolve()
    _validate_scope(source, target, verification)
    init_db()

    session = get_session()
    try:
        run = session.scalar(
            select(PostCloseOfficialRun).where(
                PostCloseOfficialRun.run_id == args.official_run_id
            )
        )
        if run is None:
            raise ValueError("OFFICIAL_RUN_NOT_FOUND")
        if run.status not in OFFICIAL_SUCCESS or run.stage != "COMPLETED":
            raise ValueError("OFFICIAL_RUN_NOT_REPAIRABLE")
        configured_target = Path(
            str((run.output_paths_json or {}).get("main_workbook") or "")
        ).resolve()
        if configured_target != target:
            raise ValueError("OFFICIAL_WORKBOOK_TARGET_MISMATCH")
        if run.real_orders or run.virtual_orders or run.scheduler_enabled:
            raise ValueError("ADVISORY_ONLY_INVARIANT_FAILED")

        old_hashes = workbook_content_style_hashes(target)
        history = target.parent / "历史版本"
        history.mkdir(parents=True, exist_ok=True)
        backup = history / f"{target.stem}_人工复核前_{old_hashes['file_sha256'][:8]}.xlsx"
        if not backup.exists():
            shutil.copy2(target, backup)

        candidate = target.with_name(
            f".{target.stem}_{uuid.uuid4().hex[:8]}_repair.xlsx"
        )
        try:
            shutil.copy2(source, candidate)
            style = WorkbookStyleService(target)
            style.annotate_official_status(candidate, run.status)
            formatting = validate_trading_assistant_workbook(candidate)
            compatibility = style.validate_excel_compatibility(candidate)
            os.replace(candidate, target)
        finally:
            candidate.unlink(missing_ok=True)

        new_hashes = workbook_content_style_hashes(target)
        repaired_at = datetime.now(SHANGHAI).isoformat()
        repair_record = {
            "status": "HUMAN_VERIFICATION_REPAIR_APPLIED",
            "official_run_id": run.run_id,
            "official_status_preserved": run.status,
            "repaired_at": repaired_at,
            "source_workbook": str(source),
            "official_workbook": str(target),
            "backup_workbook": str(backup),
            "verification": str(verification),
            "verification_sha256": _sha256(verification),
            "old_file_sha256": old_hashes["file_sha256"],
            "new_file_sha256": new_hashes["file_sha256"],
            "content_hash": new_hashes["content_hash"],
            "style_hash": new_hashes["style_hash"],
            "formatting_validation": formatting,
            "excel_compatibility": compatibility,
            "quant_ranking_changed": False,
            "candidate_set_changed": False,
            "prices_or_positions_changed": False,
            "historical_llm_failures_preserved": True,
            "real_orders": 0,
            "virtual_orders": 0,
            "scheduler": False,
        }
        output_paths = dict(run.output_paths_json or {})
        output_paths.update({
            "main_workbook": str(target),
            "main_workbook_sha256": new_hashes["file_sha256"],
            "main_workbook_content_hash": new_hashes["content_hash"],
            "main_workbook_style_hash": new_hashes["style_hash"],
        })
        report = dict(run.report_json or {})
        report["output_paths"] = output_paths
        report["human_verification_repair"] = repair_record
        run.output_paths_json = output_paths
        run.report_json = report
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _update_matching_audit_files(target.parent, args.official_run_id, repair_record)
    audit_path = target.parent / (
        f"official_workbook_repair_{args.official_run_id.rsplit('-', 1)[-1]}.json"
    )
    _atomic_write_json(audit_path, repair_record)
    print(json.dumps({**repair_record, "audit": str(audit_path)}, ensure_ascii=False, indent=2))
    return 0


def _validate_scope(source: Path, target: Path, verification: Path) -> None:
    for path in (source, target, verification):
        if not path.is_file():
            raise FileNotFoundError(path)
    if source.suffix.lower() != ".xlsx" or target.suffix.lower() != ".xlsx":
        raise ValueError("WORKBOOK_EXTENSION_REQUIRED")
    if source.name != target.name:
        raise ValueError("SOURCE_TARGET_NAME_MISMATCH")
    if "正式日线" not in target.parts:
        raise ValueError("TARGET_NOT_OFFICIAL_DAILY_WORKBOOK")
    payload = json.loads(verification.read_text(encoding="utf-8"))
    if payload.get("artifact_type") != "HUMAN_VERIFICATION_OVERLAY_V1":
        raise ValueError("INVALID_VERIFICATION_ARTIFACT")


def _update_matching_audit_files(
    directory: Path, official_run_id: str, repair_record: dict
) -> None:
    for path in directory.glob("postclose_official*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if payload.get("run_id") != official_run_id:
            continue
        output_paths = dict(payload.get("output_paths") or {})
        output_paths.update({
            "main_workbook": repair_record["official_workbook"],
            "main_workbook_sha256": repair_record["new_file_sha256"],
            "main_workbook_content_hash": repair_record["content_hash"],
            "main_workbook_style_hash": repair_record["style_hash"],
        })
        payload["output_paths"] = output_paths
        payload["human_verification_repair"] = repair_record
        _atomic_write_json(path, payload)


def _atomic_write_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
