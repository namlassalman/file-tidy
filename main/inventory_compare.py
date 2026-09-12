"""Compare two completed file inventories without modifying either source."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .cross_drive_folders import analyze_folder_candidates


STATUSES = (
    "same_path_same_size",
    "same_path_different_size",
    "primary_only",
    "backup_only",
)

INTERPRETATIONS = {
    "same_path_same_size": "Already-present candidate; hash if deletion proof is needed.",
    "same_path_different_size": "Conflict; review and verify before overwriting either file.",
    "primary_only": "Copy candidate from Primary to Backup.",
    "backup_only": "Recovery or relocation candidate; do not delete automatically.",
}


@dataclass(frozen=True, slots=True)
class InventoryRecord:
    path: str
    comparison_path: str
    normalized_path: str
    absolute_path: str
    uri: str
    name: str
    normalized_name: str
    size: int
    modified_ns: int

    @property
    def signature(self) -> tuple[str, int]:
        return self.normalized_name, self.size


def _parts(value: str) -> tuple[str, ...]:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Inventory path must be relative: {value!r}")
    return tuple(part for part in path.parts if part not in {"", "."})


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _select_path(path: str, prefix: str) -> str | None:
    path_parts = _parts(path)
    prefix_parts = _parts(prefix) if prefix else ()
    if tuple(_fold(part) for part in path_parts[: len(prefix_parts)]) != tuple(
        _fold(part) for part in prefix_parts
    ):
        return None
    selected = path_parts[len(prefix_parts) :]
    return "/".join(selected) if selected else None


def _normalized_path(path: str) -> str:
    return "/".join(_fold(part) for part in _parts(path))


def _matches_prefix(path: str, prefix: str) -> bool:
    path_parts = tuple(_fold(part) for part in _parts(path))
    prefix_parts = tuple(_fold(part) for part in _parts(prefix))
    return bool(prefix_parts) and path_parts[: len(prefix_parts)] == prefix_parts


def _default_summary_path(inventory: Path) -> Path:
    return inventory.with_suffix(".summary.json")


def _load_inventory(
    inventory: Path,
    summary_path: Path,
    prefix: str,
    exclude_prefixes: tuple[str, ...],
    label: str,
    allow_errors: bool,
) -> tuple[dict[str, InventoryRecord], dict[str, int]]:
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"{label} summary does not exist: {summary_path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} summary is not valid JSON: {summary_path}") from exc

    if summary.get("complete") is not True:
        raise ValueError(f"{label} inventory is not marked complete.")
    reported_errors = int(summary.get("errors", 0))
    if reported_errors and not allow_errors:
        raise ValueError(
            f"{label} inventory reports {reported_errors:,} errors; "
            "review them or use --allow-errors."
        )

    selected: dict[str, InventoryRecord] = {}
    total_files = total_bytes = selected_bytes = excluded_files = excluded_bytes = 0
    with inventory.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            try:
                raw = json.loads(line)
                path = str(raw["path"])
                size = int(raw["size"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"{label} inventory has an invalid record on line {line_number}."
                ) from exc
            total_files += 1
            total_bytes += size
            comparison_path = _select_path(path, prefix)
            if comparison_path is None:
                continue
            if any(_matches_prefix(comparison_path, item) for item in exclude_prefixes):
                excluded_files += 1
                excluded_bytes += size
                continue
            normalized_path = _normalized_path(comparison_path)
            if normalized_path in selected:
                raise ValueError(
                    f"{label} inventory contains duplicate normalized path: "
                    f"{comparison_path!r}"
                )
            name = PurePosixPath(comparison_path).name
            selected[normalized_path] = InventoryRecord(
                path=path,
                comparison_path=comparison_path,
                normalized_path=normalized_path,
                absolute_path=str(raw.get("absolute_path", path)),
                uri=str(raw.get("uri", "")),
                name=name,
                normalized_name=_fold(name),
                size=size,
                modified_ns=int(raw.get("modified_ns", 0)),
            )
            selected_bytes += size

    expected_files = int(summary.get("files", -1))
    expected_bytes = int(summary.get("bytes", -1))
    if total_files != expected_files or total_bytes != expected_bytes:
        raise ValueError(
            f"{label} inventory does not match its summary: "
            f"read {total_files:,} files/{total_bytes:,} bytes, expected "
            f"{expected_files:,} files/{expected_bytes:,} bytes."
        )
    return selected, {
        "full_files": total_files,
        "full_bytes": total_bytes,
        "selected_files": len(selected),
        "selected_bytes": selected_bytes,
        "excluded_files": excluded_files,
        "excluded_bytes": excluded_bytes,
        "errors": reported_errors,
    }


def _record_fields(record: InventoryRecord | None, side: str) -> dict[str, object]:
    if record is None:
        return {
            f"{side}_relative_path": "",
            f"{side}_absolute_path": "",
            f"{side}_uri": "",
            f"{side}_size_bytes": "",
            f"{side}_modified_ns": "",
        }
    return {
        f"{side}_relative_path": record.path,
        f"{side}_absolute_path": record.absolute_path,
        f"{side}_uri": record.uri,
        f"{side}_size_bytes": record.size,
        f"{side}_modified_ns": record.modified_ns,
    }


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def compare_inventories(
    primary_inventory: Path,
    backup_inventory: Path,
    files_output: Path,
    relocated_output: Path,
    aggregate_output: Path,
    folder_output: Path,
    folder_details_output: Path,
    summary_output: Path,
    *,
    primary_summary: Path | None = None,
    backup_summary: Path | None = None,
    primary_prefix: str = "",
    backup_prefix: str = "",
    primary_exclude_prefixes: tuple[str, ...] = (),
    backup_exclude_prefixes: tuple[str, ...] = (),
    primary_label: str = "Primary",
    backup_label: str = "Backup",
    allow_errors: bool = False,
) -> dict[str, object]:
    """Compare two complete inventories and write read-only evidence outputs."""
    primary_summary = primary_summary or _default_summary_path(primary_inventory)
    backup_summary = backup_summary or _default_summary_path(backup_inventory)
    primary, primary_stats = _load_inventory(
        primary_inventory,
        primary_summary,
        primary_prefix,
        primary_exclude_prefixes,
        primary_label,
        allow_errors,
    )
    backup, backup_stats = _load_inventory(
        backup_inventory,
        backup_summary,
        backup_prefix,
        backup_exclude_prefixes,
        backup_label,
        allow_errors,
    )

    file_rows: list[dict[str, object]] = []
    totals = {
        status: {
            "paths": 0,
            "primary_files": 0,
            "primary_bytes": 0,
            "backup_files": 0,
            "backup_bytes": 0,
        }
        for status in STATUSES
    }
    synchronized_paths: set[str] = set()

    for normalized_path in sorted(set(primary) | set(backup)):
        primary_record = primary.get(normalized_path)
        backup_record = backup.get(normalized_path)
        if primary_record and backup_record:
            if primary_record.size == backup_record.size:
                status = "same_path_same_size"
                synchronized_paths.add(normalized_path)
                verification = "candidate; hash required for deletion proof"
            else:
                status = "same_path_different_size"
                verification = "conflict; review and hash before overwrite"
        elif primary_record:
            status = "primary_only"
            verification = "copy candidate"
        else:
            status = "backup_only"
            verification = "recovery or relocation review"

        values = totals[status]
        values["paths"] += 1
        if primary_record:
            values["primary_files"] += 1
            values["primary_bytes"] += primary_record.size
        if backup_record:
            values["backup_files"] += 1
            values["backup_bytes"] += backup_record.size
        comparison_path = (
            primary_record.comparison_path if primary_record else backup_record.comparison_path
        )
        row: dict[str, object] = {
            "comparison_path": comparison_path,
            "status": status,
            "verification_status": verification,
        }
        row.update(_record_fields(primary_record, "primary"))
        row.update(_record_fields(backup_record, "backup"))
        file_rows.append(row)

    primary_by_signature: dict[tuple[str, int], list[InventoryRecord]] = defaultdict(list)
    backup_by_signature: dict[tuple[str, int], list[InventoryRecord]] = defaultdict(list)
    for record in primary.values():
        primary_by_signature[record.signature].append(record)
    for record in backup.values():
        backup_by_signature[record.signature].append(record)

    relocated_rows: list[dict[str, object]] = []
    for signature in sorted(set(primary_by_signature) & set(backup_by_signature)):
        primary_records = [
            record
            for record in primary_by_signature[signature]
            if record.normalized_path not in synchronized_paths
        ]
        backup_records = [
            record
            for record in backup_by_signature[signature]
            if record.normalized_path not in synchronized_paths
        ]
        if not primary_records or not backup_records:
            continue
        normalized_name, size = signature
        identifier = hashlib.sha256(
            f"{normalized_name}\0{size}".encode("utf-8")
        ).hexdigest()[:12]
        relocated_rows.append({
            "candidate_id": f"X{identifier}",
            "file_name": sorted(
                (record.name for record in primary_records + backup_records),
                key=str.casefold,
            )[0],
            "size_bytes": size,
            "primary_count": len(primary_records),
            "backup_count": len(backup_records),
            "candidate_copies": min(len(primary_records), len(backup_records)),
            "candidate_bytes": size * min(len(primary_records), len(backup_records)),
            "primary_relative_paths": json.dumps(
                sorted(record.path for record in primary_records), ensure_ascii=False
            ),
            "backup_relative_paths": json.dumps(
                sorted(record.path for record in backup_records), ensure_ascii=False
            ),
            "primary_absolute_paths": json.dumps(
                sorted(record.absolute_path for record in primary_records), ensure_ascii=False
            ),
            "backup_absolute_paths": json.dumps(
                sorted(record.absolute_path for record in backup_records), ensure_ascii=False
            ),
            "match_basis": "normalized exact filename and byte size at different paths",
            "verification_status": "candidate; content hash required",
        })
    relocated_rows.sort(
        key=lambda row: (int(row["candidate_bytes"]), int(row["candidate_copies"])),
        reverse=True,
    )

    file_fields = [
        "comparison_path", "status", "verification_status",
        "primary_relative_path", "primary_absolute_path", "primary_uri",
        "primary_size_bytes", "primary_modified_ns",
        "backup_relative_path", "backup_absolute_path", "backup_uri",
        "backup_size_bytes", "backup_modified_ns",
    ]
    relocated_fields = [
        "candidate_id", "file_name", "size_bytes", "primary_count", "backup_count",
        "candidate_copies", "candidate_bytes", "primary_relative_paths",
        "backup_relative_paths", "primary_absolute_paths", "backup_absolute_paths",
        "match_basis", "verification_status",
    ]
    aggregate_rows = [
        {
            "status": status,
            **totals[status],
            "interpretation": INTERPRETATIONS[status],
        }
        for status in STATUSES
    ]
    aggregate_fields = [
        "status", "paths", "primary_files", "primary_bytes", "backup_files",
        "backup_bytes", "interpretation",
    ]
    _write_csv(files_output, file_rows, file_fields)
    _write_csv(relocated_output, relocated_rows, relocated_fields)
    _write_csv(aggregate_output, aggregate_rows, aggregate_fields)
    folder_results = analyze_folder_candidates(
        primary.values(), backup.values(), folder_output, folder_details_output
    )

    result: dict[str, object] = {
        "complete": True,
        "read_only": True,
        "primary": {
            "label": primary_label,
            "inventory": str(primary_inventory),
            "summary": str(primary_summary),
            "prefix": primary_prefix,
            "excluded_prefixes": list(primary_exclude_prefixes),
            **primary_stats,
        },
        "backup": {
            "label": backup_label,
            "inventory": str(backup_inventory),
            "summary": str(backup_summary),
            "prefix": backup_prefix,
            "excluded_prefixes": list(backup_exclude_prefixes),
            **backup_stats,
        },
        "categories": totals,
        "relocated_candidate_groups": len(relocated_rows),
        "relocated_candidate_bytes": sum(
            int(row["candidate_bytes"]) for row in relocated_rows
        ),
        "folder_candidates": folder_results,
        "outputs": {
            "files": str(files_output),
            "relocated": str(relocated_output),
            "aggregate": str(aggregate_output),
            "folders": str(folder_output),
            "folder_details": str(folder_details_output),
        },
    }
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("primary_inventory", type=Path)
    parser.add_argument("backup_inventory", type=Path)
    parser.add_argument("--primary-summary", type=Path)
    parser.add_argument("--backup-summary", type=Path)
    parser.add_argument("--primary-prefix", default="")
    parser.add_argument("--backup-prefix", default="")
    parser.add_argument("--primary-exclude-prefix", action="append", default=[])
    parser.add_argument("--backup-exclude-prefix", action="append", default=[])
    parser.add_argument("--primary-label", default="Primary")
    parser.add_argument("--backup-label", default="Backup")
    parser.add_argument("--allow-errors", action="store_true")
    parser.add_argument(
        "--files-output", type=Path,
        default=Path("scan-results/cross-drive-files.csv"),
    )
    parser.add_argument(
        "--relocated-output", type=Path,
        default=Path("scan-results/cross-drive-relocated.csv"),
    )
    parser.add_argument(
        "--aggregate-output", type=Path,
        default=Path("scan-results/cross-drive-summary.csv"),
    )
    parser.add_argument(
        "--folder-output", type=Path,
        default=Path("scan-results/cross-drive-folders.csv"),
    )
    parser.add_argument(
        "--folder-details-output", type=Path,
        default=Path("scan-results/cross-drive-folder-files.csv"),
    )
    parser.add_argument(
        "--summary-output", type=Path,
        default=Path("scan-results/cross-drive-summary.json"),
    )
    args = parser.parse_args()
    result = compare_inventories(
        args.primary_inventory,
        args.backup_inventory,
        args.files_output,
        args.relocated_output,
        args.aggregate_output,
        args.folder_output,
        args.folder_details_output,
        args.summary_output,
        primary_summary=args.primary_summary,
        backup_summary=args.backup_summary,
        primary_prefix=args.primary_prefix,
        backup_prefix=args.backup_prefix,
        primary_exclude_prefixes=tuple(args.primary_exclude_prefix),
        backup_exclude_prefixes=tuple(args.backup_exclude_prefix),
        primary_label=args.primary_label,
        backup_label=args.backup_label,
        allow_errors=args.allow_errors,
    )
    print(json.dumps({
        "categories": result["categories"],
        "relocated_candidate_groups": result["relocated_candidate_groups"],
        "relocated_candidate_bytes": result["relocated_candidate_bytes"],
        "folder_candidates": result["folder_candidates"],
        "summary": str(args.summary_output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
