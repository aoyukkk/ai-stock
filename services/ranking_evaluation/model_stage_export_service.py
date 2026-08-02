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
    "Quant排序效度",
    "Flash评分效度",
    "Flash选中与未选中",
    "Flash相对Quant增量",
    "Promote与Demote",
    "V2与V3对照",
    "D1明细",
    "D3明细",
    "D5明细",
    "D10明细",
    "每日指标",
    "工程可靠性",
    "数据质量",
    "版本与审计",
)


@dataclass(frozen=True)
class ModelStageExportResult:
    paths: dict[str, str]
    hashes: dict[str, str]
    workbook_status: str
    workbook_error: str | None
    style_hash: str


class ModelStageExportService:
    def __init__(self, output_root: str | Path = "outputs/model_effectiveness") -> None:
        self.output_root = Path(output_root)

    def export(
        self,
        *,
        week_ending: str,
        run_id: str,
        quant_factor_version: str,
        screening_version: str,
        summary: dict[str, Any],
        daily_metrics: list[dict[str, Any]],
        stage_details: list[dict[str, Any]],
        promote_demote: list[dict[str, Any]],
        reliability: list[dict[str, Any]],
        data_quality: list[dict[str, Any]],
        version_audit: list[dict[str, Any]],
        output_excel: bool = True,
    ) -> ModelStageExportResult:
        target = (
            ROOT_DIR
            / self.output_root
            / week_ending
            / _safe(quant_factor_version)
            / _safe(screening_version)
            / run_id
        )
        target.mkdir(parents=True, exist_ok=True)
        paths: dict[str, str] = {}
        hashes: dict[str, str] = {}
        payloads = {
            "cumulative_summary.json": json.dumps(
                summary, ensure_ascii=False, indent=2, default=str
            ),
            "daily_metrics.csv": _csv(daily_metrics),
            "stage_details.csv": _csv(stage_details),
            "promote_demote_details.csv": _csv(promote_demote),
            "reliability_metrics.csv": _csv(reliability),
            "data_quality.csv": _csv(data_quality),
        }
        for filename, content in payloads.items():
            key = filename.rsplit(".", 1)[0]
            path = target / filename
            hashes[key] = write_immutable_text(path, content)
            paths[key] = str(path.resolve())

        validation = _validation_report(summary)
        validation_path = target / "validation_report.md"
        hashes["validation_report"] = write_immutable_text(validation_path, validation)
        paths["validation_report"] = str(validation_path.resolve())

        style_contract = {
            "horizontal_alignment": "center",
            "vertical_alignment": "center",
            "wrap_text": True,
            "stock_code_format": "TEXT_6_DIGIT",
            "freeze_header": True,
            "auto_filter": True,
            "missing_values": "BLANK",
            "percentage_format": "0.00%",
            "worksheets": REQUIRED_WORKSHEETS,
        }
        style_hash = stable_hash(style_contract)
        workbook_status = "DISABLED"
        workbook_error = None
        workbook_path = target / (
            f"模型分阶段前向效度评估_{week_ending}_{run_id}_{summary['report_hash'][:12]}.xlsx"
        )
        workbook_payload = {
            "summary": summary,
            "daily_metrics": daily_metrics,
            "stage_details": stage_details,
            "promote_demote": promote_demote,
            "reliability": reliability,
            "data_quality": data_quality,
            "version_audit": version_audit,
            "required_worksheets": list(REQUIRED_WORKSHEETS),
            "style_contract": style_contract,
        }
        if output_excel:
            try:
                self._workbook(workbook_payload, workbook_path)
            except RuntimeError as exc:
                workbook_status = "RUNTIME_UNAVAILABLE"
                workbook_error = str(exc)
            else:
                workbook_status = "CREATED"
                paths["excel"] = str(workbook_path.resolve())
                hashes["excel"] = _file_hash(workbook_path)

        manifest = {
            "run_id": run_id,
            "evaluation_version": summary["evaluation_version"],
            "quant_factor_version": quant_factor_version,
            "screening_version": screening_version,
            "week_ending": week_ending,
            "return_basis": summary["return_basis"],
            "execution_contract_version": summary["execution_contract_version"],
            "report_hash": summary["report_hash"],
            "style_hash": style_hash,
            "workbook_status": workbook_status,
            "workbook_error": workbook_error,
            "required_worksheets": list(REQUIRED_WORKSHEETS),
            "artifact_paths": paths,
            "artifact_hashes": hashes,
            "versions": summary.get("versions", {}),
            "real_llm_calls": 0,
            "external_search_calls": 0,
            "real_orders": 0,
            "virtual_orders": 0,
            "scheduler": False,
        }
        manifest_path = target / "run_manifest.json"
        hashes["run_manifest"] = write_immutable_text(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        )
        paths["run_manifest"] = str(manifest_path.resolve())
        return ModelStageExportResult(
            paths=paths,
            hashes=hashes,
            workbook_status=workbook_status,
            workbook_error=workbook_error,
            style_hash=style_hash,
        )

    @staticmethod
    def _workbook(payload: dict[str, Any], output_path: Path) -> None:
        module_dir = os.getenv("ARTIFACT_TOOL_NODE_MODULE_DIR", "").strip()
        if not module_dir:
            raise RuntimeError("ARTIFACT_TOOL_NODE_MODULE_DIR is not loaded")
        runner = ROOT_DIR / "scripts" / "build_model_stage_effectiveness_excel.mjs"
        payload_path = output_path.with_suffix(".workbook-input.json")
        write_immutable_text(
            payload_path,
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        )
        environment = dict(os.environ)
        environment["NODE_PATH"] = module_dir
        completed = subprocess.run(
            ["node", str(runner), str(payload_path), str(output_path)],
            cwd=ROOT_DIR,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0 or not output_path.exists():
            raise RuntimeError(
                (completed.stderr or completed.stdout or "artifact-tool export failed")[:500]
            )


def _csv(rows: Iterable[dict[str, Any]]) -> str:
    values = list(rows)
    headers: list[str] = []
    for row in values:
        for key in row:
            if key not in headers:
                headers.append(key)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=headers)
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


def _validation_report(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Quant + Flash Second-Stage Forward Effectiveness Evaluation V2",
            "",
            f"- Evaluation version: `{summary['evaluation_version']}`",
            f"- Return basis: `{summary['return_basis']}`",
            f"- Data status: `{summary.get('data_status', 'UNKNOWN')}`",
            f"- Evidence status: `{summary.get('evidence_status', 'NOT_MATURED')}`",
            "- Historical LLM calls: `0`",
            "- Historical event searches: `0`",
            "- Orders: `0`",
            "- Scheduler: `false`",
            "",
            "This report is Shadow evidence only and cannot promote a model or change production.",
        ]
    )


def _safe(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value)[:80]


def _file_hash(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()
