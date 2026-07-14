from __future__ import annotations

import argparse
import json
import os
import re
import zipfile
from pathlib import Path
from typing import BinaryIO

from dotenv import dotenv_values


TEXT_SUFFIXES = {".json", ".txt", ".md", ".yaml", ".yml", ".js", ".html", ".css", ".ps1"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEVELOPER_PATHS = tuple({
    str(PROJECT_ROOT).encode("utf-8"),
    PROJECT_ROOT.as_posix().encode("utf-8"),
    str(Path.home()).encode("utf-8"),
    Path.home().as_posix().encode("utf-8"),
})
GENERIC_SECRET = re.compile(rb"(?i)(?:gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})")


def scan(root: Path) -> dict[str, object]:
    secrets = _known_secrets()
    secret_hits: list[str] = []
    path_hits: list[str] = []
    env_hits: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        patterns = [secret.encode("utf-8") for secret in secrets]
        if path.suffix.lower() != ".zip" and _stream_has_secret(path.open("rb"), patterns, generic=path.suffix.lower() in TEXT_SUFFIXES):
            secret_hits.append(relative)
        if _forbidden_credential_name(path.name):
            env_hits.append(relative)
        if path.suffix.lower() in TEXT_SUFFIXES and _contains_developer_path(path.read_bytes()):
            path_hits.append(relative)
        if path.suffix.lower() == ".zip":
            _scan_zip(path, relative, patterns, secret_hits, path_hits, env_hits)
    return {
        "passed": not secret_hits and not env_hits and not path_hits,
        "known_secret_file_hits": secret_hits,
        "forbidden_credential_files": env_hits,
        "absolute_developer_path_files": path_hits,
        "files_scanned": sum(1 for path in root.rglob("*") if path.is_file()),
    }


def _scan_zip(
    path: Path,
    relative: str,
    patterns: list[bytes],
    secret_hits: list[str],
    path_hits: list[str],
    env_hits: list[str],
) -> None:
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            logical = f"{relative}!/{info.filename}"
            name = Path(info.filename).name.lower()
            suffix = Path(info.filename).suffix.lower()
            if _forbidden_credential_name(name):
                env_hits.append(logical)
            with archive.open(info) as stream:
                if _stream_has_secret(stream, patterns, generic=suffix in TEXT_SUFFIXES):
                    secret_hits.append(logical)
            if suffix in TEXT_SUFFIXES:
                with archive.open(info) as stream:
                    if _contains_developer_path(stream.read()):
                        path_hits.append(logical)


def _stream_has_secret(stream: BinaryIO, patterns: list[bytes], *, generic: bool) -> bool:
    longest = max([len(pattern) for pattern in patterns] + [64])
    tail = b""
    try:
        while chunk := stream.read(1024 * 1024):
            data = tail + chunk
            if any(pattern and pattern in data for pattern in patterns) or (generic and GENERIC_SECRET.search(data)):
                return True
            tail = data[-longest:]
        return False
    finally:
        stream.close()


def _forbidden_credential_name(name: str) -> bool:
    lowered = name.lower()
    suffix = Path(lowered).suffix
    return lowered == ".env" or suffix in {".pfx", ".key"} or (suffix == ".pem" and any(word in lowered for word in ("private", "secret", "credential", "key")))


def _contains_developer_path(data: bytes) -> bool:
    lowered = data.lower()
    return any(value and value.lower() in lowered for value in DEVELOPER_PATHS)


def _known_secrets() -> list[str]:
    project = Path(__file__).resolve().parents[1]
    values = dotenv_values(project / ".env") if (project / ".env").is_file() else {}
    names = ("TUSHARE_TOKEN", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN")
    return [str(os.getenv(name) or values.get(name) or "").strip() for name in names if os.getenv(name) or values.get(name)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = scan(args.root.resolve())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "files_scanned": report["files_scanned"], "secret_hits": len(report["known_secret_file_hits"]), "path_hits": len(report["absolute_developer_path_files"])}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
