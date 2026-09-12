"""Aggregate cross-drive file matches into Primary/Secondary folder candidates."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
from typing import Iterable, Protocol


NOISE_NAMES = {
    ".ds_store", ".nomedia", ".parent", "desktop.ini", "folder.jpg", "thumbs.db",
}
GENERIC_TOKENS = {
    "archive", "attachments", "backup", "camera", "copy", "data", "documents",
    "files", "folder", "inbox", "items", "mail", "miscellaneous", "music",
    "new", "old", "photos", "pictures", "sent", "the", "videos", "work",
}
GENERIC_LEAF_NAMES = {
    "archive", "attachments", "camera", "camera roll", "deleted items", "documents",
    "drafts", "inbox", "mail", "miscellaneous", "music", "outbox", "photos",
    "pictures", "sent items", "videos", "work",
}


class Record(Protocol):
    comparison_path: str
    absolute_path: str
    name: str
    normalized_name: str
    size: int
    modified_ns: int

    @property
    def signature(self) -> tuple[str, int]: ...


@dataclass
class FolderStats:
    keys: set[int] = field(default_factory=set)
    key_counts: Counter[int] = field(default_factory=Counter)
    files: int = 0
    bytes: int = 0
    latest_ns: int = 0


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _normal_folder(value: str) -> str:
    value = re.sub(r"^\d+[\s._-]+", "", _fold(value))
    return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()


def _similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= 5 and len(shorter.split()) >= 2 and shorter in longer:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _is_noise(record: Record) -> bool:
    name = _fold(record.name)
    return record.size == 0 or name in NOISE_NAMES or name.startswith("._")


def _ancestors(path: str) -> list[str]:
    parts = PurePosixPath(path).parts[:-1]
    return ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]


def _under(record: Record, folder: str) -> bool:
    return record.comparison_path.casefold().startswith(folder.casefold() + "/")


def _root(records: Iterable[Record]) -> Path:
    for record in records:
        root = Path(record.absolute_path)
        for _ in PurePosixPath(record.comparison_path).parts:
            root = root.parent
        return root
    raise ValueError("Selected inventory is empty.")


def _context_tokens(folder: str) -> set[str]:
    parts = list(PurePosixPath(folder).parts[:-1])
    if len(parts) > 1:
        parts = parts[1:]
    tokens: set[str] = set()
    for part in parts[-4:]:
        tokens.update(
            token
            for token in _normal_folder(part).split()
            if len(token) >= 3 and token not in GENERIC_TOKENS and not token.isdigit()
        )
    return tokens


def _contexts_compatible(primary_folder: str, backup_folder: str) -> bool:
    leaf = _normal_folder(PurePosixPath(backup_folder).name)
    if leaf not in GENERIC_LEAF_NAMES:
        return True
    primary_parent = _normal_folder(PurePosixPath(primary_folder).parent.name)
    backup_parent = _normal_folder(PurePosixPath(backup_folder).parent.name)
    if primary_parent and backup_parent and _similarity(primary_parent, backup_parent) >= 0.8:
        return True
    return bool(_context_tokens(primary_folder) & _context_tokens(backup_folder))


def _absolute_folder(root: Path, folder: str) -> str:
    return str(root.joinpath(*PurePosixPath(folder).parts))


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def analyze_folder_candidates(
    primary_records: Iterable[Record],
    backup_records: Iterable[Record],
    folder_output: Path,
    detail_output: Path,
    minimum_similarity: float = 0.8,
) -> dict[str, int]:
    """Write candidates with Primary fixed as Master and Backup as Secondary."""
    primary_records = list(primary_records)
    backup_records = list(backup_records)
    primary_root = _root(primary_records)
    backup_root = _root(backup_records)
    key_ids: dict[tuple[str, int], int] = {}
    key_names: list[str] = []
    key_sizes: list[int] = []
    primary_by_key: list[list[Record]] = []
    backup_by_key: list[list[Record]] = []
    primary_folders: dict[str, FolderStats] = defaultdict(FolderStats)
    backup_folders: dict[str, FolderStats] = defaultdict(FolderStats)

    def add(records: list[Record], folders: dict[str, FolderStats], side: str) -> None:
        for record in records:
            if _is_noise(record):
                continue
            key_id = key_ids.get(record.signature)
            if key_id is None:
                key_id = len(key_names)
                key_ids[record.signature] = key_id
                key_names.append(record.name)
                key_sizes.append(record.size)
                primary_by_key.append([])
                backup_by_key.append([])
            (primary_by_key if side == "primary" else backup_by_key)[key_id].append(record)
            for folder in _ancestors(record.comparison_path):
                stats = folders[folder]
                stats.keys.add(key_id)
                stats.key_counts[key_id] += 1
                stats.files += 1
                stats.bytes += record.size
                stats.latest_ns = max(stats.latest_ns, record.modified_ns)

    add(primary_records, primary_folders, "primary")
    add(backup_records, backup_folders, "backup")

    primary_names: dict[str, list[str]] = defaultdict(list)
    backup_names: dict[str, list[str]] = defaultdict(list)
    for folder in primary_folders:
        if len(PurePosixPath(folder).parts) < 2:
            continue
        name = _normal_folder(PurePosixPath(folder).name)
        if name:
            primary_names[name].append(folder)
    for folder in backup_folders:
        if len(PurePosixPath(folder).parts) < 2:
            continue
        name = _normal_folder(PurePosixPath(folder).name)
        if name:
            backup_names[name].append(folder)

    name_pairs = {(name, name) for name in set(primary_names) & set(backup_names)}
    primary_by_token: dict[str, set[str]] = defaultdict(set)
    backup_by_token: dict[str, set[str]] = defaultdict(set)
    for name in primary_names:
        for token in set(name.split()) - GENERIC_TOKENS:
            if len(token) >= 3:
                primary_by_token[token].add(name)
    for name in backup_names:
        for token in set(name.split()) - GENERIC_TOKENS:
            if len(token) >= 3:
                backup_by_token[token].add(name)
    for token in set(primary_by_token) & set(backup_by_token):
        left, right = primary_by_token[token], backup_by_token[token]
        if len(left) <= 200 and len(right) <= 200:
            name_pairs.update((a, b) for a in left for b in right)

    best_by_secondary: dict[str, dict[str, object]] = {}
    for primary_name, backup_name in name_pairs:
        similarity = _similarity(primary_name, backup_name)
        if similarity < minimum_similarity:
            continue
        for primary_folder in primary_names[primary_name]:
            for backup_folder in backup_names[backup_name]:
                if not _contexts_compatible(primary_folder, backup_folder):
                    continue
                matched = primary_folders[primary_folder].keys & backup_folders[backup_folder].keys
                if not matched:
                    continue
                matching_files = sum(
                    backup_folders[backup_folder].key_counts[key_id] for key_id in matched
                )
                matching_bytes = sum(
                    backup_folders[backup_folder].key_counts[key_id] * key_sizes[key_id]
                    for key_id in matched
                )
                if matching_files < 2 and matching_bytes < 10 * 1024**2:
                    continue
                backup_stats = backup_folders[backup_folder]
                coverage = matching_files / backup_stats.files if backup_stats.files else 0
                candidate: dict[str, object] = {
                    "primary_folder": primary_folder,
                    "backup_folder": backup_folder,
                    "similarity": similarity,
                    "matched": matched,
                    "matching_files": matching_files,
                    "matching_bytes": matching_bytes,
                    "coverage": coverage,
                }
                existing = best_by_secondary.get(backup_folder)
                score = (
                    coverage, similarity, matching_bytes, matching_files,
                    primary_folders[primary_folder].latest_ns,
                )
                if existing is None:
                    best_by_secondary[backup_folder] = candidate
                else:
                    existing_score = (
                        float(existing["coverage"]),
                        float(existing["similarity"]),
                        int(existing["matching_bytes"]),
                        int(existing["matching_files"]),
                        primary_folders[str(existing["primary_folder"])].latest_ns,
                    )
                    if score > existing_score:
                        best_by_secondary[backup_folder] = candidate

    candidates = sorted(
        best_by_secondary.values(),
        key=lambda item: (
            len(PurePosixPath(str(item["backup_folder"])).parts),
            -float(item["coverage"]),
            -int(item["matching_bytes"]),
        ),
    )
    actionable: list[dict[str, object]] = []
    for candidate in candidates:
        backup_folder = str(candidate["backup_folder"])
        primary_folder = str(candidate["primary_folder"])
        redundant = any(
            backup_folder.startswith(str(parent["backup_folder"]) + "/")
            and float(parent["coverage"]) >= 0.8
            and (
                primary_folder == str(parent["primary_folder"])
                or
                primary_folder.startswith(str(parent["primary_folder"]) + "/")
                or str(parent["primary_folder"]).startswith(primary_folder + "/")
            )
            for parent in actionable
        )
        if not redundant:
            actionable.append(candidate)

    folder_rows: list[dict[str, object]] = []
    detail_rows: list[dict[str, object]] = []
    for candidate in actionable:
        primary_folder = str(candidate["primary_folder"])
        backup_folder = str(candidate["backup_folder"])
        matched = set(candidate["matched"])
        backup_stats = backup_folders[backup_folder]
        coverage = float(candidate["coverage"])
        confidence = "high" if coverage >= 0.8 else "medium" if coverage >= 0.5 else "low"
        if coverage == 1.0:
            recommendation = "verify all Secondary files with hashes"
        elif coverage >= 0.8:
            recommendation = "merge or review unmatched Secondary files"
        elif coverage >= 0.5:
            recommendation = "review partial overlap"
        else:
            recommendation = "weak overlap; keep separate unless reviewed"
        identifier = hashlib.sha256(
            f"{primary_folder}\0{backup_folder}".encode("utf-8")
        ).hexdigest()[:12]
        candidate_id = f"F{identifier}"
        folder_rows.append({
            "candidate_id": candidate_id,
            "master_folder": _absolute_folder(primary_root, primary_folder),
            "secondary_folder": _absolute_folder(backup_root, backup_folder),
            "parent_folder": PurePosixPath(backup_folder).parent.name,
            "confidence": confidence,
            "folder_name_similarity_pct": round(float(candidate["similarity"]) * 100, 1),
            "matching_files": int(candidate["matching_files"]),
            "matching_bytes": int(candidate["matching_bytes"]),
            "secondary_files": backup_stats.files,
            "secondary_bytes": backup_stats.bytes,
            "secondary_coverage_pct": round(coverage * 100, 2),
            "unmatched_secondary_files": backup_stats.files - int(candidate["matching_files"]),
            "unmatched_secondary_bytes": backup_stats.bytes - int(candidate["matching_bytes"]),
            "master_latest_modified_ns": primary_folders[primary_folder].latest_ns,
            "secondary_latest_modified_ns": backup_stats.latest_ns,
            "recommendation": recommendation,
            "verification_status": "candidate; content hashes not yet checked",
        })
        for key_id in sorted(matched, key=lambda item: (-key_sizes[item], key_names[item].casefold())):
            primary_matches = [record for record in primary_by_key[key_id] if _under(record, primary_folder)]
            backup_matches = [record for record in backup_by_key[key_id] if _under(record, backup_folder)]
            if not primary_matches or not backup_matches:
                continue
            detail_rows.append({
                "candidate_id": candidate_id,
                "file_name": key_names[key_id],
                "size_bytes": key_sizes[key_id],
                "master_locations": json.dumps(
                    sorted({record.absolute_path for record in primary_matches}),
                    ensure_ascii=False,
                ),
                "secondary_locations": json.dumps(
                    sorted({record.absolute_path for record in backup_matches}),
                    ensure_ascii=False,
                ),
                "secondary_file_count": len(backup_matches),
                "match_basis": "normalized exact filename and byte size",
                "verification_status": "candidate; content hash required",
            })

    confidence_rank = {"high": 2, "medium": 1, "low": 0}
    folder_rows.sort(
        key=lambda row: (
            confidence_rank[str(row["confidence"])],
            float(row["secondary_coverage_pct"]),
            int(row["matching_bytes"]),
        ),
        reverse=True,
    )
    folder_fields = [
        "candidate_id", "master_folder", "secondary_folder", "parent_folder",
        "confidence", "folder_name_similarity_pct", "matching_files", "matching_bytes",
        "secondary_files", "secondary_bytes", "secondary_coverage_pct",
        "unmatched_secondary_files", "unmatched_secondary_bytes",
        "master_latest_modified_ns", "secondary_latest_modified_ns", "recommendation",
        "verification_status",
    ]
    detail_fields = [
        "candidate_id", "file_name", "size_bytes", "master_locations",
        "secondary_locations", "secondary_file_count", "match_basis",
        "verification_status",
    ]
    _write_csv(folder_output, folder_rows, folder_fields)
    _write_csv(detail_output, detail_rows, detail_fields)
    return {
        "candidates": len(folder_rows),
        "high_confidence": sum(row["confidence"] == "high" for row in folder_rows),
        "complete_coverage": sum(float(row["secondary_coverage_pct"]) == 100 for row in folder_rows),
        "detail_rows": len(detail_rows),
    }
