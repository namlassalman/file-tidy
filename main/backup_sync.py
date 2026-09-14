"""Build a read-only backup-sync plan from a completed cross-drive comparison."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


COMPARISON_STATUSES = {
    "same_path_same_size",
    "same_path_different_size",
    "primary_only",
    "backup_only",
}


@dataclass(slots=True)
class FolderTotals:
    total_files: int = 0
    total_bytes: int = 0
    backed_up_files: int = 0
    backed_up_bytes: int = 0
    missing_files: int = 0
    missing_bytes: int = 0
    copy_candidate_files: int = 0
    copy_candidate_bytes: int = 0
    relocated_candidate_files: int = 0
    relocated_candidate_bytes: int = 0
    conflict_files: int = 0
    conflict_bytes: int = 0


def _read_summary(path: Path) -> dict[str, object]:
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"Comparison summary does not exist: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Comparison summary is not valid JSON: {path}") from exc
    if summary.get("complete") is not True:
        raise ValueError("Cross-drive comparison is not marked complete.")
    categories = summary.get("categories")
    if not isinstance(categories, dict) or set(categories) != COMPARISON_STATUSES:
        raise ValueError("Cross-drive summary has incomplete comparison categories.")
    return summary


def _resolve_input(summary_path: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute() or path.exists():
        return path
    beside_summary = summary_path.parent / path.name
    if beside_summary.exists():
        return beside_summary
    return path


def _slash(value: str) -> str:
    return value.replace("\\", "/").rstrip("/")


def _infer_selected_root(absolute_path: str, comparison_path: str) -> str | None:
    absolute = _slash(absolute_path)
    relative = _slash(comparison_path).lstrip("/")
    if not absolute or not relative:
        return None
    suffix = "/" + relative
    if not absolute.casefold().endswith(suffix.casefold()):
        return None
    root = absolute[: -len(suffix)]
    return root or "/"


def _join(root: str, relative: str) -> str:
    root = _slash(root)
    relative = _slash(relative).lstrip("/")
    if root == "/":
        return f"/{relative}" if relative else "/"
    return f"{root}/{relative}" if relative else root


def _folder_parts(comparison_path: str) -> tuple[str, ...]:
    parts = tuple(
        part for part in PurePosixPath(comparison_path).parts if part not in {"", "."}
    )
    return parts[:-1]


def _ancestors(parts: tuple[str, ...]) -> list[str]:
    return ["."] + ["/".join(parts[:depth]) for depth in range(1, len(parts) + 1)]


def _percentage(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 100.0
    return round(numerator * 100 / denominator, 1)


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _validate_counts(
    observed: dict[str, dict[str, int]], summary: dict[str, object]
) -> None:
    categories = summary["categories"]
    assert isinstance(categories, dict)
    for status in COMPARISON_STATUSES:
        expected = categories[status]
        if not isinstance(expected, dict):
            raise ValueError(f"Invalid summary category: {status}")
        for field in (
            "paths",
            "primary_files",
            "primary_bytes",
            "backup_files",
            "backup_bytes",
        ):
            if observed[status][field] != int(expected.get(field, -1)):
                raise ValueError(
                    "Comparison evidence does not match its summary: "
                    f"{status}.{field} is {observed[status][field]:,}, expected "
                    f"{int(expected.get(field, -1)):,}."
                )


def build_backup_plan(
    comparison_summary: Path,
    files_csv: Path | None,
    relocated_csv: Path | None,
    gaps_output: Path,
    copy_output: Path,
    review_output: Path,
    summary_output: Path,
    *,
    primary_root: str | None = None,
    backup_root: str | None = None,
) -> dict[str, object]:
    """Create read-only backup gaps and file plans from comparison evidence."""
    summary = _read_summary(comparison_summary)
    outputs = summary.get("outputs")
    if files_csv is None:
        if not isinstance(outputs, dict) or not outputs.get("files"):
            raise ValueError("Comparison summary does not identify its file evidence CSV.")
        files_csv = _resolve_input(comparison_summary, str(outputs["files"]))
    relocated_groups = int(summary.get("relocated_candidate_groups", 0))
    if relocated_csv is None and isinstance(outputs, dict) and outputs.get("relocated"):
        relocated_csv = _resolve_input(comparison_summary, str(outputs["relocated"]))

    relocated_primary: dict[str, dict[str, str]] = {}
    if relocated_groups:
        if relocated_csv is None:
            raise ValueError(
                "Comparison contains relocated candidates but does not identify their evidence CSV."
            )
        try:
            relocated_source = relocated_csv.open(newline="", encoding="utf-8")
        except FileNotFoundError:
            raise ValueError(
                f"Relocated-candidate evidence does not exist: {relocated_csv}"
            ) from None
        with relocated_source:
            relocated_reader = csv.DictReader(relocated_source)
            relocated_fields = {
                "candidate_id",
                "primary_relative_paths",
                "backup_absolute_paths",
            }
            if (
                not relocated_reader.fieldnames
                or not relocated_fields.issubset(relocated_reader.fieldnames)
            ):
                raise ValueError("Relocated-candidate evidence has an unsupported schema.")
            observed_groups = 0
            for line_number, row in enumerate(relocated_reader, 2):
                observed_groups += 1
                try:
                    primary_paths = json.loads(row["primary_relative_paths"])
                    backup_paths = json.loads(row["backup_absolute_paths"])
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid relocated-candidate paths on CSV line {line_number}."
                    ) from exc
                if not isinstance(primary_paths, list) or not isinstance(backup_paths, list):
                    raise ValueError(
                        f"Invalid relocated-candidate paths on CSV line {line_number}."
                    )
                backup_locations = json.dumps(backup_paths, ensure_ascii=False)
                for primary_path in primary_paths:
                    relocated_primary[str(primary_path)] = {
                        "candidate_id": row["candidate_id"],
                        "backup_locations": backup_locations,
                    }
            if observed_groups != relocated_groups:
                raise ValueError(
                    "Relocated-candidate evidence does not match its summary: "
                    f"read {observed_groups:,} groups, expected {relocated_groups:,}."
                )

    expected_fields = {
        "comparison_path",
        "status",
        "primary_relative_path",
        "primary_absolute_path",
        "primary_uri",
        "primary_size_bytes",
        "primary_modified_ns",
        "backup_absolute_path",
        "backup_uri",
        "backup_size_bytes",
        "backup_modified_ns",
    }
    observed = {
        status: {
            "paths": 0,
            "primary_files": 0,
            "primary_bytes": 0,
            "backup_files": 0,
            "backup_bytes": 0,
        }
        for status in COMPARISON_STATUSES
    }
    folder_totals: dict[str, FolderTotals] = defaultdict(FolderTotals)
    copy_rows: list[dict[str, object]] = []
    review_rows: list[dict[str, object]] = []
    primary_roots: set[str] = set()
    backup_roots: set[str] = set()

    try:
        source = files_csv.open(newline="", encoding="utf-8")
    except FileNotFoundError:
        raise ValueError(f"Comparison file evidence does not exist: {files_csv}") from None
    with source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or not expected_fields.issubset(reader.fieldnames):
            raise ValueError("Comparison file evidence has an unsupported schema.")
        for line_number, row in enumerate(reader, 2):
            status = row["status"]
            if status not in COMPARISON_STATUSES:
                raise ValueError(
                    f"Unknown comparison status on CSV line {line_number}: {status!r}"
                )
            comparison_path = row["comparison_path"]
            try:
                primary_size = int(row["primary_size_bytes"] or 0)
                backup_size = int(row["backup_size_bytes"] or 0)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid byte size on comparison CSV line {line_number}."
                ) from exc

            counts = observed[status]
            counts["paths"] += 1
            if row["primary_absolute_path"]:
                counts["primary_files"] += 1
                counts["primary_bytes"] += primary_size
                inferred = _infer_selected_root(
                    row["primary_absolute_path"], comparison_path
                )
                if inferred:
                    primary_roots.add(inferred)
            if row["backup_absolute_path"]:
                counts["backup_files"] += 1
                counts["backup_bytes"] += backup_size
                inferred = _infer_selected_root(
                    row["backup_absolute_path"], comparison_path
                )
                if inferred:
                    backup_roots.add(inferred)

            if status != "backup_only":
                relocated = (
                    relocated_primary.get(row["primary_relative_path"])
                    if status == "primary_only"
                    else None
                )
                for folder in _ancestors(_folder_parts(comparison_path)):
                    totals = folder_totals[folder]
                    totals.total_files += 1
                    totals.total_bytes += primary_size
                    if status == "same_path_same_size":
                        totals.backed_up_files += 1
                        totals.backed_up_bytes += primary_size
                    elif status == "primary_only":
                        totals.missing_files += 1
                        totals.missing_bytes += primary_size
                        if relocated:
                            totals.relocated_candidate_files += 1
                            totals.relocated_candidate_bytes += primary_size
                        else:
                            totals.copy_candidate_files += 1
                            totals.copy_candidate_bytes += primary_size
                    else:
                        totals.conflict_files += 1
                        totals.conflict_bytes += primary_size

            if status == "primary_only":
                relocated = relocated_primary.get(row["primary_relative_path"])
                if relocated:
                    review_rows.append({
                        "action": "review_relocated_candidate",
                        "candidate_id": relocated["candidate_id"],
                        "comparison_path": comparison_path,
                        "primary_path": row["primary_absolute_path"],
                        "primary_size_bytes": row["primary_size_bytes"],
                        "primary_modified_ns": row["primary_modified_ns"],
                        "backup_path": "",
                        "backup_size_bytes": "",
                        "backup_modified_ns": "",
                        "candidate_backup_paths": relocated["backup_locations"],
                        "review_status": "not_reviewed",
                    })
                else:
                    copy_rows.append({
                        "action": "copy_primary_to_backup",
                        "comparison_path": comparison_path,
                        "size_bytes": primary_size,
                        "primary_modified_ns": row["primary_modified_ns"],
                        "primary_path": row["primary_absolute_path"],
                        "primary_uri": row["primary_uri"],
                        "backup_target_path": "",
                        "approval_status": "not_reviewed",
                    })
            elif status in {"same_path_different_size", "backup_only"}:
                review_rows.append({
                    "action": (
                        "review_conflict"
                        if status == "same_path_different_size"
                        else "review_backup_only"
                    ),
                    "candidate_id": "",
                    "comparison_path": comparison_path,
                    "primary_path": row["primary_absolute_path"],
                    "primary_size_bytes": row["primary_size_bytes"],
                    "primary_modified_ns": row["primary_modified_ns"],
                    "backup_path": row["backup_absolute_path"],
                    "backup_size_bytes": row["backup_size_bytes"],
                    "backup_modified_ns": row["backup_modified_ns"],
                    "candidate_backup_paths": "",
                    "review_status": "not_reviewed",
                })

    _validate_counts(observed, summary)

    if primary_root is None:
        if len(primary_roots) != 1:
            raise ValueError(
                "Could not infer one Primary selected root; pass --primary-root."
            )
        primary_root = next(iter(primary_roots))
    if backup_root is None:
        if len(backup_roots) != 1:
            raise ValueError(
                "Could not infer one Backup selected root; pass --backup-root."
            )
        backup_root = next(iter(backup_roots))

    for row in copy_rows:
        row["backup_target_path"] = _join(backup_root, str(row["comparison_path"]))

    gaps_rows: list[dict[str, object]] = []
    for folder, totals in folder_totals.items():
        relative = "" if folder == "." else folder
        requires_review = bool(
            totals.conflict_files or totals.relocated_candidate_files
        )
        if totals.copy_candidate_files and requires_review:
            status = "needs_sync_and_review"
        elif totals.copy_candidate_files:
            status = "needs_sync"
        elif requires_review:
            status = "review_required"
        else:
            status = "backed_up"
        identifier = hashlib.sha256(folder.casefold().encode("utf-8")).hexdigest()[:12]
        gaps_rows.append({
            "folder_id": f"B{identifier}",
            "comparison_folder": folder,
            "primary_folder": _join(primary_root, relative),
            "backup_folder": _join(backup_root, relative),
            "depth": 0 if folder == "." else len(PurePosixPath(folder).parts),
            "primary_files": totals.total_files,
            "primary_bytes": totals.total_bytes,
            "backed_up_files": totals.backed_up_files,
            "backed_up_bytes": totals.backed_up_bytes,
            "file_coverage_pct": _percentage(
                totals.backed_up_files, totals.total_files
            ),
            "byte_coverage_pct": _percentage(
                totals.backed_up_bytes, totals.total_bytes
            ),
            "missing_files": totals.missing_files,
            "missing_bytes": totals.missing_bytes,
            "copy_candidate_files": totals.copy_candidate_files,
            "copy_candidate_bytes": totals.copy_candidate_bytes,
            "relocated_candidate_files": totals.relocated_candidate_files,
            "relocated_candidate_bytes": totals.relocated_candidate_bytes,
            "conflict_files": totals.conflict_files,
            "conflict_bytes": totals.conflict_bytes,
            "status": status,
            "approval_status": "not_reviewed",
        })
    gaps_rows.sort(
        key=lambda row: (
            int(row["copy_candidate_bytes"]),
            int(row["copy_candidate_files"]),
            int(row["relocated_candidate_bytes"]),
            int(row["conflict_files"]),
            -int(row["depth"]),
        ),
        reverse=True,
    )
    copy_rows.sort(key=lambda row: int(row["size_bytes"]), reverse=True)
    review_rows.sort(
        key=lambda row: max(
            int(row["primary_size_bytes"] or 0),
            int(row["backup_size_bytes"] or 0),
        ),
        reverse=True,
    )

    gap_fields = [
        "folder_id", "comparison_folder", "primary_folder", "backup_folder",
        "depth", "primary_files", "primary_bytes", "backed_up_files",
        "backed_up_bytes", "file_coverage_pct", "byte_coverage_pct",
        "missing_files", "missing_bytes", "conflict_files", "conflict_bytes",
        "copy_candidate_files", "copy_candidate_bytes",
        "relocated_candidate_files", "relocated_candidate_bytes",
        "status", "approval_status",
    ]
    copy_fields = [
        "action", "comparison_path", "size_bytes", "primary_modified_ns",
        "primary_path", "primary_uri", "backup_target_path", "approval_status",
    ]
    review_fields = [
        "action", "candidate_id", "comparison_path", "primary_path", "primary_size_bytes",
        "primary_modified_ns", "backup_path", "backup_size_bytes",
        "backup_modified_ns", "candidate_backup_paths", "review_status",
    ]
    _write_csv(gaps_output, gaps_rows, gap_fields)
    _write_csv(copy_output, copy_rows, copy_fields)
    _write_csv(review_output, review_rows, review_fields)

    root_totals = folder_totals.get(".", FolderTotals())
    backup_only = observed["backup_only"]
    result: dict[str, object] = {
        "complete": True,
        "read_only": True,
        "mode": "backup_sync",
        "comparison_summary": str(comparison_summary),
        "comparison_files": str(files_csv),
        "primary": {
            "label": summary["primary"]["label"],
            "selected_root": primary_root,
            "files": root_totals.total_files,
            "bytes": root_totals.total_bytes,
        },
        "backup": {
            "label": summary["backup"]["label"],
            "selected_root": backup_root,
        },
        "coverage": {
            "backed_up_files": root_totals.backed_up_files,
            "backed_up_bytes": root_totals.backed_up_bytes,
            "file_coverage_pct": _percentage(
                root_totals.backed_up_files, root_totals.total_files
            ),
            "byte_coverage_pct": _percentage(
                root_totals.backed_up_bytes, root_totals.total_bytes
            ),
            "missing_files": root_totals.missing_files,
            "missing_bytes": root_totals.missing_bytes,
            "copy_candidate_files": root_totals.copy_candidate_files,
            "copy_candidate_bytes": root_totals.copy_candidate_bytes,
            "relocated_candidate_files": root_totals.relocated_candidate_files,
            "relocated_candidate_bytes": root_totals.relocated_candidate_bytes,
            "conflict_files": root_totals.conflict_files,
            "conflict_bytes": root_totals.conflict_bytes,
            "backup_only_files": backup_only["backup_files"],
            "backup_only_bytes": backup_only["backup_bytes"],
        },
        "folder_rows": len(gaps_rows),
        "note": (
            "Folder rows are recursive and overlap. File-level copy candidates are "
            "unique and require folder approval before an rsync manifest is created."
        ),
        "outputs": {
            "gaps": str(gaps_output),
            "copy_candidates": str(copy_output),
            "review": str(review_output),
        },
    }
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comparison_summary", type=Path)
    parser.add_argument("--files", type=Path)
    parser.add_argument("--relocated", type=Path)
    parser.add_argument("--primary-root")
    parser.add_argument("--backup-root")
    parser.add_argument(
        "--gaps-output",
        type=Path,
        default=Path("scan-results/backup-sync-gaps.csv"),
    )
    parser.add_argument(
        "--copy-output",
        type=Path,
        default=Path("scan-results/backup-sync-copy-candidates.csv"),
    )
    parser.add_argument(
        "--review-output",
        type=Path,
        default=Path("scan-results/backup-sync-review.csv"),
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("scan-results/backup-sync-summary.json"),
    )
    args = parser.parse_args()
    result = build_backup_plan(
        args.comparison_summary,
        args.files,
        args.relocated,
        args.gaps_output,
        args.copy_output,
        args.review_output,
        args.summary_output,
        primary_root=args.primary_root,
        backup_root=args.backup_root,
    )
    print(json.dumps({
        "coverage": result["coverage"],
        "folder_rows": result["folder_rows"],
        "outputs": result["outputs"],
        "summary": str(args.summary_output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
