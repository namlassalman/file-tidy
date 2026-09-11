"""Read-only file inventory for local folders and mounted NAS shares."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, TextIO

from .config import is_excluded, load_config


@dataclass(frozen=True)
class ScanResult:
    files: int
    bytes: int
    folders: int
    errors: int
    by_type: dict[str, dict[str, int]]


def _file_type(name: str) -> str:
    suffix = Path(name).suffix.casefold()
    return suffix[1:] if suffix else "[no extension]"


def _source_root(settings: dict) -> Path:
    if settings["source_type"] == "local":
        return Path(settings["source_path"]).expanduser()
    mount_path = settings.get("nas_mount_path")
    if not isinstance(mount_path, str) or not mount_path.strip():
        raise ValueError(
            "NAS scanning requires nas_mount_path pointing to an accessible mounted share."
        )
    return Path(mount_path).expanduser()


def _selected_roots(source_root: Path, folders: list[str]) -> Iterable[tuple[str, Path]]:
    base = source_root.resolve()
    for folder in folders or ["."]:
        relative = PurePosixPath(folder.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Folder must stay within the source root: {folder!r}")
        candidate = (source_root / Path(*relative.parts)).resolve()
        if candidate != base and base not in candidate.parents:
            raise ValueError(f"Folder must stay within the source root: {folder!r}")
        yield ("." if str(relative) == "." else str(relative), candidate)


def scan(settings: dict, output: TextIO, errors: TextIO, progress_every: int = 1000) -> ScanResult:
    """Write one JSON object per file and return aggregate statistics.

    The source is never modified. Symlinks are recorded as errors and are not
    followed, preventing accidental traversal outside the selected source.
    """
    source_root = _source_root(settings)
    if not source_root.is_dir():
        raise NotADirectoryError(f"Source folder does not exist or is not a directory: {source_root}")
    excluded = settings["excluded_folders"]
    files = total_bytes = folders = error_count = 0
    by_type: Counter[str] = Counter()
    by_type_bytes: Counter[str] = Counter()
    stack: list[tuple[str, Path]] = list(_selected_roots(source_root, settings["folders"]))

    while stack:
        relative_folder, folder = stack.pop()
        folders += 1
        try:
            entries = list(os.scandir(folder))
        except OSError as exc:
            error_count += 1
            errors.write(json.dumps({"path": relative_folder, "error": str(exc)}) + "\n")
            continue
        for entry in entries:
            relative_path = (
                f"{relative_folder}/{entry.name}" if relative_folder != "." else entry.name
            )
            if entry.is_dir(follow_symlinks=False):
                if not is_excluded(relative_path, excluded):
                    stack.append((relative_path, Path(entry.path)))
                continue
            if entry.is_symlink():
                error_count += 1
                errors.write(json.dumps({"path": relative_path, "error": "symlink skipped"}) + "\n")
                continue
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                error_count += 1
                errors.write(json.dumps({"path": relative_path, "error": str(exc)}) + "\n")
                continue
            kind = _file_type(entry.name)
            record = {
                "path": relative_path,
                "size": stat.st_size,
                "type": kind,
                "depth": len(PurePosixPath(relative_path).parts) - 1,
            }
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            files += 1
            total_bytes += stat.st_size
            by_type[kind] += 1
            by_type_bytes[kind] += stat.st_size
            if progress_every and files % progress_every == 0:
                print(f"Scanned {files:,} files in {folders:,} folders", file=sys.stderr, flush=True)

    return ScanResult(
        files=files,
        bytes=total_bytes,
        folders=folders,
        errors=error_count,
        by_type={
            kind: {"files": by_type[kind], "bytes": by_type_bytes[kind]}
            for kind in sorted(by_type)
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to a JSON configuration file")
    parser.add_argument("--output", type=Path, default=Path("scan-results/inventory.jsonl"))
    parser.add_argument("--errors", type=Path, default=Path("scan-results/inventory-errors.jsonl"))
    parser.add_argument("--progress-every", type=int, default=1000)
    args = parser.parse_args()
    try:
        settings = load_config(args.config) if args.config else load_config()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.errors.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as inventory, args.errors.open(
            "w", encoding="utf-8"
        ) as error_file:
            result = scan(settings, inventory, error_file, args.progress_every)
        summary = {
            "source_type": settings["source_type"],
            "files": result.files,
            "bytes": result.bytes,
            "folders": result.folders,
            "errors": result.errors,
            "by_type": result.by_type,
        }
        summary_path = args.output.with_suffix(".summary.json")
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
