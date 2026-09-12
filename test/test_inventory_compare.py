import csv
import json
import tempfile
import unittest
from pathlib import Path

from main.inventory_compare import compare_inventories
from main.inventory_compare_report import generate


def _write_inventory(path: Path, records: list[dict], *, complete: bool = True) -> Path:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    summary = path.with_suffix(".summary.json")
    summary.write_text(
        json.dumps({
            "complete": complete,
            "files": len(records),
            "bytes": sum(int(record["size"]) for record in records),
            "folders": 2,
            "errors": 0,
        }),
        encoding="utf-8",
    )
    return summary


def _record(root: str, relative: str, size: int, modified_ns: int = 1) -> dict:
    path = f"{root}/{relative}"
    absolute = f"/mnt/{root}/{relative}"
    return {
        "path": path,
        "absolute_path": absolute,
        "uri": Path(absolute).as_uri(),
        "size": size,
        "modified_ns": modified_ns,
    }


class InventoryCompareTests(unittest.TestCase):
    def test_classifies_paths_and_relocated_candidates_with_prefixes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "primary.jsonl"
            backup = root / "backup.jsonl"
            _write_inventory(primary, [
                _record("PrimaryRoot", "Library/same.txt", 10),
                _record("PrimaryRoot", "Library/conflict.bin", 12),
                _record("PrimaryRoot", "New/primary.txt", 5),
                _record("PrimaryRoot", "User/Project Archive/Photo.JPG", 20_000_000),
                _record("PrimaryRoot", "System/cache.bin", 99),
            ])
            _write_inventory(backup, [
                _record("BackupRoot", "Library/SAME.txt", 10),
                _record("BackupRoot", "Library/conflict.bin", 13),
                _record("BackupRoot", "Archive/backup.txt", 6),
                _record("BackupRoot", "User/Project Archive Old/photo.jpg", 20_000_000),
            ])
            files = root / "files.csv"
            relocated = root / "relocated.csv"
            aggregate = root / "aggregate.csv"
            folders = root / "folders.csv"
            folder_details = root / "folder-details.csv"
            summary = root / "summary.json"

            result = compare_inventories(
                primary,
                backup,
                files,
                relocated,
                aggregate,
                folders,
                folder_details,
                summary,
                primary_prefix="PrimaryRoot",
                backup_prefix="BackupRoot",
                primary_exclude_prefixes=("System",),
                primary_label="Media Server",
                backup_label="Cold Store",
            )

            self.assertTrue(result["complete"])
            self.assertEqual(result["primary"]["selected_files"], 4)
            self.assertEqual(result["primary"]["excluded_files"], 1)
            self.assertEqual(result["backup"]["selected_files"], 4)
            self.assertEqual(
                {status: values["paths"] for status, values in result["categories"].items()},
                {
                    "same_path_same_size": 1,
                    "same_path_different_size": 1,
                    "primary_only": 2,
                    "backup_only": 2,
                },
            )
            with relocated.open(newline="", encoding="utf-8") as source:
                candidates = list(csv.DictReader(source))
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["file_name"], "Photo.JPG")
            self.assertEqual(candidates[0]["candidate_bytes"], "20000000")
            self.assertIn("PrimaryRoot/User/Project Archive/Photo.JPG", candidates[0]["primary_relative_paths"])
            self.assertIn("BackupRoot/User/Project Archive Old/photo.jpg", candidates[0]["backup_relative_paths"])

            with folders.open(newline="", encoding="utf-8") as source:
                folder_candidates = list(csv.DictReader(source))
            self.assertEqual(len(folder_candidates), 1)
            self.assertEqual(folder_candidates[0]["confidence"], "high")
            self.assertEqual(folder_candidates[0]["secondary_coverage_pct"], "100.0")
            self.assertEqual(folder_candidates[0]["recommendation"], "verify all Secondary files with hashes")

            with files.open(newline="", encoding="utf-8") as source:
                statuses = {row["comparison_path"]: row["status"] for row in csv.DictReader(source)}
            self.assertEqual(statuses["Library/conflict.bin"], "same_path_different_size")
            self.assertEqual(statuses["New/primary.txt"], "primary_only")
            self.assertEqual(statuses["Archive/backup.txt"], "backup_only")

            report = root / "comparison.html"
            displayed_files, displayed_relocated, displayed_folders = generate(
                summary, files, relocated, folders, folder_details, report
            )
            self.assertEqual(
                (displayed_files, displayed_relocated, displayed_folders), (6, 1, 1)
            )
            page = report.read_text(encoding="utf-8")
            self.assertIn("Media Server compared with Cold Store", page)
            self.assertIn("Same path, different size", page)
            self.assertIn("Backup-only files", page)
            self.assertIn("Folder details", page)
            self.assertIn("PrimaryRoot/User/Project Archive", page)
            self.assertEqual(
                len(list((root / "comparison-folders").glob("*.html"))), 1
            )

    def test_rejects_an_incomplete_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "primary.jsonl"
            backup = root / "backup.jsonl"
            _write_inventory(primary, [_record("A", "one.txt", 1)], complete=False)
            _write_inventory(backup, [_record("B", "one.txt", 1)])

            with self.assertRaisesRegex(ValueError, "not marked complete"):
                compare_inventories(
                    primary,
                    backup,
                    root / "files.csv",
                    root / "relocated.csv",
                    root / "aggregate.csv",
                    root / "folders.csv",
                    root / "folder-details.csv",
                    root / "output-summary.json",
                )

    def test_rejects_inventory_that_does_not_match_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "primary.jsonl"
            backup = root / "backup.jsonl"
            summary = _write_inventory(primary, [_record("A", "one.txt", 1)])
            _write_inventory(backup, [_record("B", "one.txt", 1)])
            values = json.loads(summary.read_text(encoding="utf-8"))
            values["files"] = 2
            summary.write_text(json.dumps(values), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "does not match its summary"):
                compare_inventories(
                    primary,
                    backup,
                    root / "files.csv",
                    root / "relocated.csv",
                    root / "aggregate.csv",
                    root / "folders.csv",
                    root / "folder-details.csv",
                    root / "output-summary.json",
                )


if __name__ == "__main__":
    unittest.main()
