from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path
from typing import Any


def safe_artifact_token(value: Any, *, maximum: int = 48) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    token = token.strip("._-")
    if not token:
        raise ValueError("WORKBOOK_RUN_ID_REQUIRED")
    return token[:maximum]


def versioned_workbook_path(
    output_dir: Path | str,
    *,
    stem: str,
    trade_date: str,
    run_id: str,
    factor_version: str | None = None,
) -> Path:
    directory = Path(output_dir).resolve()
    run_token = safe_artifact_token(run_id)
    version_token = (
        f"_{safe_artifact_token(factor_version)}" if factor_version else ""
    )
    return directory / f"{stem}_{trade_date}_{run_token}{version_token}.xlsx"


def assert_new_workbook_path(path: Path | str) -> Path:
    target = Path(path).resolve()
    if target.exists():
        raise FileExistsError(f"IMMUTABLE_WORKBOOK_ALREADY_EXISTS:{target.name}")
    return target


def workbook_content_style_hashes(path: Path | str) -> dict[str, str]:
    workbook = Path(path).resolve()
    with zipfile.ZipFile(workbook) as archive:
        content_material: list[str] = []
        style_refs: list[str] = []
        for name in sorted(archive.namelist()):
            if not name.startswith("xl/worksheets/") or not name.endswith(".xml"):
                continue
            text = archive.read(name).decode("utf-8", "replace")
            content_material.append(re.sub(r'\ss="\d+"', "", text))
            style_refs.extend(re.findall(r'\ss="(\d+)"', text))
        if "xl/sharedStrings.xml" in archive.namelist():
            content_material.append(
                archive.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            )
        styles = (
            archive.read("xl/styles.xml")
            if "xl/styles.xml" in archive.namelist()
            else b""
        )
        bad_member = archive.testzip()
    if bad_member:
        raise ValueError(f"WORKBOOK_ZIP_CORRUPT:{bad_member}")
    return {
        "content_hash": hashlib.sha256(
            "\n".join(content_material).encode("utf-8")
        ).hexdigest(),
        "style_hash": hashlib.sha256(
            styles + "\n".join(style_refs).encode("utf-8")
        ).hexdigest(),
        "file_sha256": hashlib.sha256(workbook.read_bytes()).hexdigest(),
    }
