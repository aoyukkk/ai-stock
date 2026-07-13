from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]


class PerformanceExcelExporter:
    def export(self, output_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="selection-performance-") as temporary:
            build_dir = Path(temporary)
            builder = build_dir / "build_selection_performance_excel.mjs"
            shutil.copy2(ROOT_DIR / "scripts" / "build_selection_performance_excel.mjs", builder)
            shutil.copy2(ROOT_DIR / "scripts" / "excel_alignment.mjs", build_dir / "excel_alignment.mjs")
            payload_path = build_dir / "payload.json"
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
            dependency_root = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
            link = build_dir / "node_modules"
            if not (dependency_root / "@oai" / "artifact-tool").exists():
                raise RuntimeError("ARTIFACT_TOOL_DEPENDENCY_NOT_FOUND")
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(dependency_root)], capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise RuntimeError("ARTIFACT_TOOL_JUNCTION_FAILED")
            completed = subprocess.run(["node", str(builder), str(payload_path), str(output_path)], cwd=build_dir, check=False)
            if completed.returncode != 0 or not output_path.exists():
                raise RuntimeError(f"PERFORMANCE_EXCEL_EXPORT_FAILED:{completed.returncode}")
        content = output_path.read_bytes()
        return {"output_path": str(output_path), "size": len(content), "sha256": hashlib.sha256(content).hexdigest(), "sheet_count": 4, "status": "SUCCESS"}
