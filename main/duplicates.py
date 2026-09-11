"""Build drive-wide duplicate candidate tables from a completed inventory."""

from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path, PurePosixPath


NOISE_NAMES = {
    ".ds_store",
    ".nomedia",
    ".parent",
    "desktop.ini",
    "folder.jpg",
    "thumbs.db",
}
GENERIC_TOKENS = {
    "archive", "backup", "copy", "data", "documents", "files", "folder",
    "items", "miscellaneous", "music", "new", "old", "photos", "the", "work",
}


@dataclass(slots=True)
class FileRecord:
    path: str
    absolute_path: str
    name: str
    size: int
    modified_ns: int


@dataclass
class FolderStats:
    keys: set[int] = field(default_factory=set)
    files: int = 0
    bytes: int = 0
    latest_ns: int = 0


def _normal(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"^\d+[\s._-]+", "", value)
    return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()


def _similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= 5 and len(shorter.split()) >= 2 and shorter in longer:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _is_noise(name: str, size: int) -> bool:
    folded = unicodedata.normalize("NFKC", name).casefold()
    return size == 0 or folded in NOISE_NAMES or folded.startswith("._")


def _ancestors(path: str) -> list[str]:
    parts = PurePosixPath(path).parts[:-1]
    return ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]


def _root_path(record: FileRecord) -> Path:
    root = Path(record.absolute_path)
    for _ in PurePosixPath(record.path).parts:
        root = root.parent
    return root


def _under(record: FileRecord, folder: str) -> bool:
    return record.path.startswith(folder + "/")


def analyze(
    inventory: Path,
    aggregate_output: Path,
    detail_output: Path,
    minimum_similarity: float = 0.8,
) -> tuple[int, int]:
    """Write aggregate folder candidates and their matching-file evidence."""
    key_ids: dict[tuple[str, int], int] = {}
    key_names: list[str] = []
    key_sizes: list[int] = []
    records_by_key: list[list[FileRecord]] = []
    folders: dict[str, FolderStats] = defaultdict(FolderStats)
    root: Path | None = None

    with inventory.open(encoding="utf-8") as source:
        for line in source:
            raw = json.loads(line)
            path = str(raw["path"])
            name = PurePosixPath(path).name
            size = int(raw["size"])
            key = (unicodedata.normalize("NFKC", name).casefold(), size)
            key_id = key_ids.get(key)
            if key_id is None:
                key_id = len(key_names)
                key_ids[key] = key_id
                key_names.append(name)
                key_sizes.append(size)
                records_by_key.append([])
            record = FileRecord(
                path=path,
                absolute_path=str(raw.get("absolute_path", path)),
                name=name,
                size=size,
                modified_ns=int(raw.get("modified_ns", 0)),
            )
            records_by_key[key_id].append(record)
            if root is None:
                root = _root_path(record)
            latest = max(record.modified_ns, int(raw.get("parent_modified_ns", 0)))
            for folder in _ancestors(path):
                stats = folders[folder]
                stats.keys.add(key_id)
                stats.files += 1
                stats.bytes += size
                stats.latest_ns = max(stats.latest_ns, latest)

    if root is None:
        raise ValueError("Inventory is empty.")

    names: dict[str, list[str]] = defaultdict(list)
    for folder in folders:
        name = _normal(PurePosixPath(folder).name)
        if name:
            names[name].append(folder)

    possible_name_pairs: set[tuple[str, str]] = {(name, name) for name in names}
    by_token: dict[str, set[str]] = defaultdict(set)
    for name in names:
        for token in set(name.split()) - GENERIC_TOKENS:
            if len(token) >= 3:
                by_token[token].add(name)
    for values in by_token.values():
        if len(values) <= 200:
            possible_name_pairs.update(combinations(sorted(values), 2))

    candidate_pairs: list[tuple[str, str, float, set[int], int]] = []
    meaningful_cache: dict[str, set[int]] = {}

    def meaningful(folder: str) -> set[int]:
        cached = meaningful_cache.get(folder)
        if cached is None:
            cached = {
                key_id for key_id in folders[folder].keys
                if not _is_noise(key_names[key_id], key_sizes[key_id])
            }
            meaningful_cache[folder] = cached
        return cached

    for left_name, right_name in possible_name_pairs:
        similarity = _similarity(left_name, right_name)
        if similarity < minimum_similarity:
            continue
        left_folders = names[left_name]
        right_folders = names[right_name]
        folder_pairs = combinations(left_folders, 2) if left_name == right_name else (
            (left, right) for left in left_folders for right in right_folders
        )
        for left, right in folder_pairs:
            if left == right:
                continue
            if left.startswith(right + "/") or right.startswith(left + "/"):
                continue
            matched = meaningful(left) & meaningful(right)
            if not matched:
                continue
            matched_bytes = sum(key_sizes[key_id] for key_id in matched)
            if len(matched) < 2 and matched_bytes < 10 * 1024**2:
                continue
            candidate_pairs.append((left, right, similarity, matched, matched_bytes))

    parent: dict[str, str] = {}

    def find(item: str) -> str:
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left, right, _similarity_value, _matched, _bytes in candidate_pairs:
        union(left, right)

    components: dict[str, set[str]] = defaultdict(set)
    for folder in parent:
        components[find(folder)].add(folder)

    pair_lookup: dict[frozenset[str], tuple[float, set[int], int]] = {}
    for left, right, similarity, matched, matched_bytes in candidate_pairs:
        pair_lookup[frozenset((left, right))] = (similarity, matched, matched_bytes)

    aggregate_rows: list[dict[str, object]] = []
    detail_rows: list[dict[str, object]] = []
    group_number = 0
    for component in components.values():
        master = max(
            component,
            key=lambda folder: (
                not folder.casefold().startswith(".trash"),
                folders[folder].latest_ns,
                folders[folder].bytes,
            ),
        )
        secondary_candidates: list[tuple[str, float, set[int], int]] = []
        for secondary in sorted(component - {master}):
            if master.startswith(secondary + "/") or secondary.startswith(master + "/"):
                continue
            values = pair_lookup.get(frozenset((master, secondary)))
            if values is None:
                matched = meaningful(master) & meaningful(secondary)
                if not matched:
                    continue
                matched_bytes = sum(key_sizes[key_id] for key_id in matched)
                similarity = _similarity(
                    _normal(PurePosixPath(master).name),
                    _normal(PurePosixPath(secondary).name),
                )
            else:
                similarity, matched, matched_bytes = values
            if len(matched) < 2 and matched_bytes < 10 * 1024**2:
                continue
            secondary_candidates.append((secondary, similarity, matched, matched_bytes))

        actionable_candidates = []
        for candidate in secondary_candidates:
            secondary, _similarity_value, matched, _matched_bytes = candidate
            is_redundant_parent = any(
                other_secondary.startswith(secondary + "/")
                and len(other_matched) >= len(matched) * 0.8
                for other_secondary, _other_similarity, other_matched, _other_bytes
                in secondary_candidates
                if other_secondary != secondary
            )
            if not is_redundant_parent:
                actionable_candidates.append(candidate)

        for secondary, similarity, matched, matched_bytes in actionable_candidates:
            group_number += 1
            group = f"D{group_number:05d}"
            smaller_count = min(folders[master].files, folders[secondary].files) or 1
            coverage = len(matched) / smaller_count
            confidence = "high" if coverage >= 0.8 else "medium" if coverage >= 0.5 else "low"
            master_absolute = str(root.joinpath(*PurePosixPath(master).parts))
            secondary_absolute = str(root.joinpath(*PurePosixPath(secondary).parts))
            aggregate_rows.append({
                "candidate_group": group,
                "master_folder": master_absolute,
                "secondary_folder": secondary_absolute,
                "parent_folder": PurePosixPath(secondary).parent.name,
                "confidence": confidence,
                "folder_name_similarity_pct": round(similarity * 100, 1),
                "matching_files": len(matched),
                "matching_bytes": matched_bytes,
                "potential_savings_bytes": matched_bytes,
                "smaller_folder_coverage_pct": round(coverage * 100, 1),
                "master_files": folders[master].files,
                "master_bytes": folders[master].bytes,
                "secondary_files": folders[secondary].files,
                "secondary_bytes": folders[secondary].bytes,
                "master_latest_modified_ns": folders[master].latest_ns,
                "secondary_latest_modified_ns": folders[secondary].latest_ns,
                "verification_status": "candidate; content hash required",
            })
            for key_id in sorted(matched, key=lambda item: (-key_sizes[item], key_names[item].casefold())):
                master_records = [record for record in records_by_key[key_id] if _under(record, master)]
                secondary_records = [record for record in records_by_key[key_id] if _under(record, secondary)]
                if not master_records or not secondary_records:
                    continue
                detail_rows.append({
                    "candidate_group": group,
                    "master_location": " | ".join(sorted({record.absolute_path for record in master_records})),
                    "secondary_location": " | ".join(sorted({record.absolute_path for record in secondary_records})),
                    "parent_folder": PurePosixPath(secondary).parent.name,
                    "file_name": key_names[key_id],
                    "size_bytes": key_sizes[key_id],
                    "master_modified_ns": max(record.modified_ns for record in master_records),
                    "secondary_modified_ns": max(record.modified_ns for record in secondary_records),
                    "match_basis": "normalized exact filename and byte size",
                    "verification_status": "candidate; content hash required",
                })

    aggregate_rows.sort(
        key=lambda row: (
            {"high": 2, "medium": 1, "low": 0}[str(row["confidence"])],
            int(row["potential_savings_bytes"]),
            int(row["matching_files"]),
        ),
        reverse=True,
    )
    aggregate_output.parent.mkdir(parents=True, exist_ok=True)
    aggregate_fields = list(aggregate_rows[0]) if aggregate_rows else [
        "candidate_group", "master_folder", "secondary_folder", "parent_folder",
        "confidence", "matching_files", "matching_bytes", "verification_status",
    ]
    with aggregate_output.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=aggregate_fields)
        writer.writeheader()
        writer.writerows(aggregate_rows)
    detail_fields = list(detail_rows[0]) if detail_rows else [
        "candidate_group", "master_location", "secondary_location", "parent_folder",
        "file_name", "size_bytes", "match_basis", "verification_status",
    ]
    with detail_output.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=detail_fields)
        writer.writeheader()
        writer.writerows(detail_rows)
    return len(aggregate_rows), len(detail_rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--aggregate", type=Path, default=Path("scan-results/duplicate-folders.csv"))
    parser.add_argument("--details", type=Path, default=Path("scan-results/duplicate-files.csv"))
    parser.add_argument("--minimum-folder-similarity", type=float, default=0.8)
    args = parser.parse_args()
    folders, files = analyze(args.inventory, args.aggregate, args.details, args.minimum_folder_similarity)
    print(f"Wrote {folders:,} folder candidates and {files:,} file matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
