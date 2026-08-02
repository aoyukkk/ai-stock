from __future__ import annotations

import csv
import io
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from services.ranking_evaluation.constants import ROOT_DIR
from services.ranking_evaluation.utils import stable_hash, write_immutable_text


REQUIRED_WORKSHEETS = (
    "累计总览",
    "Rank_IC",
    "Top20_Bottom20",
    "五组收益",
    "单调性判断",
    "排名日明细",
    "每日五组平均收益",
    "数据质量",
    "版本与审计",
)


class WorkbookRuntimeUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ExportResult:
    paths: dict[str, str]
    hashes: dict[str, str]
    workbook_status: str
    workbook_error: str | None
    style_hash: str


def _csv_text(rows: Iterable[dict[str, Any]]) -> str:
    values = list(rows)
    buffer = io.StringIO(newline="")
    headers: list[str] = []
    for row in values:
        for key in row:
            if key not in headers:
                headers.append(key)
    writer = csv.DictWriter(buffer, fieldnames=headers, extrasaction="ignore")
    if headers:
        writer.writeheader()
        for row in values:
            writer.writerow(
                {
                    key: (
                        json.dumps(value, ensure_ascii=False, sort_keys=True)
                        if isinstance(value, (dict, list))
                        else value
                    )
                    for key, value in row.items()
                }
            )
    return buffer.getvalue()


class RankingEvaluationExportService:
    def __init__(self, output_root: Path | str) -> None:
        self.output_root = Path(output_root)

    def export(
        self,
        *,
        factor_version: str,
        week_ending: str,
        run_id: str,
        summary: dict[str, Any],
        daily_metrics: list[dict[str, Any]],
        ranking_details: list[dict[str, Any]],
        data_quality: list[dict[str, Any]],
        version_audit: list[dict[str, Any]],
    ) -> ExportResult:
        target = (
            ROOT_DIR
            / self.output_root
            / week_ending
            / _safe_token(factor_version)
            / run_id
        )
        target.mkdir(parents=True, exist_ok=True)
        paths: dict[str, str] = {}
        hashes: dict[str, str] = {}

        payloads = {
            "summary": json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            "daily_metrics": _csv_text(daily_metrics),
            "ranking_details": _csv_text(ranking_details),
            "data_quality": _csv_text(data_quality),
        }
        extensions = {
            "summary": "json",
            "daily_metrics": "csv",
            "ranking_details": "csv",
            "data_quality": "csv",
        }
        for name, content in payloads.items():
            path = target / f"{name}.{extensions[name]}"
            hashes[name] = write_immutable_text(path, content)
            paths[name] = str(path.resolve())

        style_contract = {
            "horizontal_alignment": "center",
            "vertical_alignment": "center",
            "wrap_text": True,
            "stock_code_format": "000000",
            "freeze_header": True,
            "auto_filter": True,
            "missing_values": "blank",
            "percentage_format": "0.00%",
            "worksheets": REQUIRED_WORKSHEETS,
        }
        style_hash = stable_hash(style_contract)
        workbook_payload = {
            "summary": summary,
            "daily_metrics": daily_metrics,
            "ranking_details": ranking_details,
            "data_quality": data_quality,
            "version_audit": version_audit,
            "style_contract": style_contract,
            "required_worksheets": list(REQUIRED_WORKSHEETS),
        }
        workbook_status = "DISABLED"
        workbook_error = None
        workbook_path = target / (
            f"量化排名前向效度评估_{_safe_token(factor_version)}_"
            f"{week_ending}_{run_id}.xlsx"
        )
        if summary.get("output_excel", True):
            try:
                self._export_workbook(workbook_payload, workbook_path)
            except WorkbookRuntimeUnavailable as exc:
                workbook_status = "RUNTIME_UNAVAILABLE"
                workbook_error = str(exc)
            else:
                workbook_status = "CREATED"
                paths["excel"] = str(workbook_path.resolve())
                hashes["excel"] = _file_hash(workbook_path)

        manifest = {
            "run_id": run_id,
            "factor_version": factor_version,
            "week_ending": week_ending,
            "report_hash": summary["report_hash"],
            "return_basis": summary["return_basis"],
            "evaluation_version": summary["evaluation_version"],
            "style_hash": style_hash,
            "workbook_status": workbook_status,
            "workbook_error": workbook_error,
            "artifact_paths": paths,
            "artifact_hashes": hashes,
            "required_worksheets": list(REQUIRED_WORKSHEETS),
            "external_api_calls": 0,
            "llm_calls": 0,
            "orders": 0,
            "scheduler": False,
        }
        manifest_path = target / "run_manifest.json"
        hashes["run_manifest"] = write_immutable_text(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        )
        paths["run_manifest"] = str(manifest_path.resolve())
        return ExportResult(
            paths=paths,
            hashes=hashes,
            workbook_status=workbook_status,
            workbook_error=workbook_error,
            style_hash=style_hash,
        )

    @staticmethod
    def _export_workbook(payload: dict[str, Any], output_path: Path) -> None:
        """Use only the approved artifact-tool runtime; never fall back to openpyxl."""
        node_module_dir = os.getenv("ARTIFACT_TOOL_NODE_MODULE_DIR", "").strip()
        if not node_module_dir:
            raise WorkbookRuntimeUnavailable(
                "ARTIFACT_TOOL_NODE_MODULE_DIR is not loaded in this runtime"
            )
        runner = ROOT_DIR / "scripts" / "build_ranking_evaluation_excel.mjs"
        if not runner.exists():
            raise WorkbookRuntimeUnavailable(
                "artifact-tool workbook runner is not installed"
            )
        payload_path = output_path.with_suffix(".workbook-input.json")
        write_immutable_text(
            payload_path,
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        )
        environment = dict(os.environ)
        environment["NODE_PATH"] = node_module_dir
        completed = subprocess.run(
            ["node", str(runner), str(payload_path), str(output_path)],
            cwd=str(ROOT_DIR),
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0 or not output_path.exists():
            raise WorkbookRuntimeUnavailable(
                "artifact-tool workbook export failed: "
                + (completed.stderr or completed.stdout or "unknown error").strip()[:500]
            )


def _safe_token(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in "._-" else "-" for char in str(value)
    )[:80]


def _file_hash(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
