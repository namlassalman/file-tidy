"""Read-only file inventory for local folders and mounted NAS shares."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
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
    elapsed_seconds: float


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


def _record_stats(record: dict, by_type: Counter[str], by_type_bytes: Counter[str]) -> tuple[int, int]:
    kind = record["type"]
    size = int(record["size"])
    by_type[kind] += 1
    by_type_bytes[kind] += size
    return 1, size


def _write_checkpoint(path: Path | None, stack: list[tuple[str, Path]], completed: set[str]) -> None:
    if path is None:
        return
    payload = {"stack": [[relative, str(folder)] for relative, folder in stack], "completed": sorted(completed)}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


def _load_checkpoint(path: Path) -> tuple[list[tuple[str, Path]], set[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [(relative, Path(folder)) for relative, folder in data.get("stack", [])], set(data.get("completed", []))


def _scan_gio_worker(settings: dict, output: TextIO, errors: TextIO, progress_every: int) -> ScanResult:
    payload = {
        "root": str(_source_root(settings)),
        "folders": settings["folders"],
        "excluded_folders": settings["excluded_folders"],
    }
    worker = Path(__file__).with_name("gio_worker.py")
    process = subprocess.Popen(
        ["/usr/bin/python3", str(worker), json.dumps(payload)],
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        bufsize=1,
    )
    files = total_bytes = error_count = 0
    by_type: Counter[str] = Counter()
    by_type_bytes: Counter[str] = Counter()
    started = time.monotonic()
    assert process.stdout is not None
    folder_count = 0
    for line in process.stdout:
        record = json.loads(line)
        if "_summary" in record:
            folder_count = int(record["_summary"].get("folders", 0))
            continue
        if "_error" in record:
            error_count += 1
            errors.write(json.dumps(record["_error"]) + "\n")
            continue
        output.write(json.dumps(record, ensure_ascii=False) + "\n")
        added_files, added_bytes = _record_stats(record, by_type, by_type_bytes)
        files += added_files
        total_bytes += added_bytes
        if progress_every and files % progress_every == 0:
            print(f"Scanned {files:,} files", file=sys.stderr, flush=True)
    process.wait()
    if process.returncode:
        raise OSError(f"GIO worker exited with status {process.returncode}")
    return ScanResult(
        files=files, bytes=total_bytes, folders=folder_count, errors=error_count,
        by_type={kind: {"files": by_type[kind], "bytes": by_type_bytes[kind]} for kind in sorted(by_type)},
        elapsed_seconds=round(time.monotonic() - started, 3),
    )


def _scan_entries(folder: Path) -> list[tuple[str, int, bool, bool, int]]:
    """Return name, size, kind flags, and modification time using GIO."""
    uri = folder.as_uri()
    command = [
        "/usr/bin/gio", "list", "-n", "-a",
        "standard::name,standard::type,standard::size,time::modified,time::modified-usec", uri,
    ]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise OSError("gio metadata request timed out") from exc
    if completed.returncode:
        raise OSError(completed.stderr.strip() or "gio list failed")
    entries = []
    for line in completed.stdout.splitlines():
        fields = line.split("\t", 3)
        if len(fields) < 3:
            continue
        name, size_text, type_text = fields[:3]
        attributes = fields[3] if len(fields) == 4 else ""
        try:
            size = int(size_text)
        except ValueError:
            size = 0
        seconds_match = re.search(r"time::modified=(\d+)", attributes)
        usec_match = re.search(r"time::modified-usec=(\d+)", attributes)
        modified_ns = (
            int(seconds_match.group(1)) * 1_000_000_000
            + (int(usec_match.group(1)) * 1_000 if usec_match else 0)
            if seconds_match
            else 0
        )
        entries.append((name, size, "directory" in type_text, "symbolic" in type_text, modified_ns))
    return entries


def scan(
    settings: dict,
    output: TextIO,
    errors: TextIO,
    progress_every: int = 1000,
    checkpoint: Path | None = None,
    resume: bool = False,
) -> ScanResult:
    """Write one JSON object per file and return aggregate statistics.

    The source is never modified. Symlinks are recorded as errors and are not
    followed, preventing accidental traversal outside the selected source.
    """
    source_root = _source_root(settings)
    if not source_root.is_dir():
        raise NotADirectoryError(f"Source folder does not exist or is not a directory: {source_root}")
    if settings["source_type"] == "nas" and settings.get("nas_backend", "auto") in {"auto", "gio-worker"}:
        return _scan_gio_worker(settings, output, errors, progress_every)
    excluded = settings["excluded_folders"]
    files = total_bytes = folders = error_count = 0
    by_type: Counter[str] = Counter()
    by_type_bytes: Counter[str] = Counter()
    completed: set[str] = set()
    stack: list[tuple[str, Path]] = list(_selected_roots(source_root, settings["folders"]))
    if resume and checkpoint and checkpoint.exists():
        stack, completed = _load_checkpoint(checkpoint)
        output_name = getattr(output, "name", None)
        if output_name and Path(output_name).exists():
            with Path(output_name).open(encoding="utf-8") as existing:
                for line in existing:
                    try:
                        record = json.loads(line)
                        added_files, added_bytes = _record_stats(record, by_type, by_type_bytes)
                        files += added_files
                        total_bytes += added_bytes
                    except (ValueError, KeyError, TypeError):
                        continue
    use_gio = settings["source_type"] == "nas" and settings.get("nas_backend", "auto") in {"auto", "gio"}
    started = time.monotonic()
    while stack:
        relative_folder, folder = stack.pop()
        if relative_folder in completed:
            continue
        folders += 1
        try:
            parent_modified_ns = folder.stat().st_mtime_ns
            if use_gio:
                entries = _scan_entries(folder)
            else:
                entries = []
                for entry in os.scandir(folder):
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        entries.append((entry.name, stat.st_size, entry.is_dir(follow_symlinks=False), entry.is_symlink(), stat.st_mtime_ns))
                    except OSError as exc:
                        raise OSError(f"{entry.name}: {exc}") from exc
        except OSError as exc:
            error_count += 1
            errors.write(json.dumps({"path": relative_folder, "error": str(exc)}) + "\n")
            continue
        for name, entry_size, is_directory, is_symlink, modified_ns in entries:
            relative_path = (
                f"{relative_folder}/{name}" if relative_folder != "." else name
            )
            if is_directory:
                if not is_excluded(relative_path, excluded):
                    stack.append((relative_path, folder / name))
                continue
            if is_symlink:
                error_count += 1
                errors.write(json.dumps({"path": relative_path, "error": "symlink skipped"}) + "\n")
                continue
            kind = _file_type(name)
            absolute_path = (folder / name).absolute()
            record = {
                "path": relative_path,
                "source": relative_folder.split("/", 1)[0],
                "absolute_path": str(absolute_path),
                "uri": absolute_path.as_uri(),
                "size": entry_size,
                "type": kind,
                "depth": len(PurePosixPath(relative_path).parts) - 1,
                "modified_ns": modified_ns,
                "parent_modified_ns": parent_modified_ns,
            }
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            added_files, added_bytes = _record_stats(record, by_type, by_type_bytes)
            files += added_files
            total_bytes += added_bytes
            if progress_every and files % progress_every == 0:
                print(f"Scanned {files:,} files in {folders:,} folders", file=sys.stderr, flush=True)
        completed.add(relative_folder)
        _write_checkpoint(checkpoint, stack, completed)

    if checkpoint and checkpoint.exists():
        checkpoint.unlink()

    return ScanResult(
        files=files,
        bytes=total_bytes,
        folders=folders,
        errors=error_count,
        by_type={
            kind: {"files": by_type[kind], "bytes": by_type_bytes[kind]}
            for kind in sorted(by_type)
        },
        elapsed_seconds=round(time.monotonic() - started, 3),
    )


def estimate(settings: dict) -> dict[str, int]:
    """Count files and folders before a scan, without writing inventory data."""
    source_root = _source_root(settings)
    if settings["source_type"] == "nas" and settings.get("nas_backend", "auto") in {"auto", "gio-worker"}:
        result = _scan_gio_worker(settings, io.StringIO(), io.StringIO(), 0)
        return {"files": result.files, "folders": result.folders, "bytes": result.bytes, "errors": result.errors}
    stack = list(_selected_roots(source_root, settings["folders"]))
    files = folders = bytes_total = 0
    use_gio = settings["source_type"] == "nas" and settings.get("nas_backend", "auto") in {"auto", "gio"}
    while stack:
        relative_folder, folder = stack.pop()
        folders += 1
        if folders % 100 == 0:
            print(f"Estimate visited {folders:,} folders; found {files:,} files", file=sys.stderr, flush=True)
        entries = _scan_entries(folder) if use_gio else [
            (entry.name, stat.st_size, entry.is_dir(follow_symlinks=False), entry.is_symlink(), stat.st_mtime_ns)
            for entry in os.scandir(folder)
            for stat in [entry.stat(follow_symlinks=False)]
        ]
        for name, size, is_directory, is_symlink, _modified_ns in entries:
            relative_path = f"{relative_folder}/{name}" if relative_folder != "." else name
            if is_directory:
                if not is_excluded(relative_path, settings["excluded_folders"]):
                    stack.append((relative_path, folder / name))
            elif not is_symlink:
                files += 1
                bytes_total += size
    return {"files": files, "folders": folders, "bytes": bytes_total}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to a JSON configuration file")
    parser.add_argument("--output", type=Path, default=Path("scan-results/inventory.jsonl"))
    parser.add_argument("--errors", type=Path, default=Path("scan-results/inventory-errors.jsonl"))
    parser.add_argument("--progress-every", type=int, default=1000)
    parser.add_argument("--checkpoint", type=Path, help="Checkpoint path for resumable scans")
    parser.add_argument("--resume", action="store_true", help="Resume from --checkpoint")
    parser.add_argument("--estimate-only", action="store_true", help="Count files and folders, then exit")
    args = parser.parse_args()
    try:
        settings = load_config(args.config) if args.config else load_config()
        if args.estimate_only:
            print(json.dumps(estimate(settings), indent=2))
            return 0
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.errors.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if args.resume else "w"
        with args.output.open(mode, encoding="utf-8") as inventory, args.errors.open(
            mode, encoding="utf-8"
        ) as error_file:
            result = scan(settings, inventory, error_file, args.progress_every, args.checkpoint, args.resume)
        summary = {
            "complete": True,
            "source_type": settings["source_type"],
            "files": result.files,
            "bytes": result.bytes,
            "folders": result.folders,
            "errors": result.errors,
            "elapsed_seconds": result.elapsed_seconds,
            "files_per_second": round(result.files / result.elapsed_seconds, 2) if result.elapsed_seconds else 0,
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
