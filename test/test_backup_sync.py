import csv
import json
import tempfile
import unittest
from pathlib import Path

from main.backup_sync import build_backup_plan
from main.backup_sync_report import generate
from main.inventory_compare import compare_inventories


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


def _write_inventory(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    path.with_suffix(".summary.json").write_text(
        json.dumps({
            "complete": True,
            "files": len(records),
            "bytes": sum(int(record["size"]) for record in records),
            "folders": 4,
            "errors": 0,
        }),
        encoding="utf-8",
    )


class BackupSyncTests(unittest.TestCase):
    def _comparison(self, root: Path) -> tuple[Path, Path, Path]:
        primary = root / "primary.jsonl"
        backup = root / "backup.jsonl"
        _write_inventory(primary, [
            _record("PrimaryRoot", "Library/same.txt", 10),
            _record("PrimaryRoot", "Library/conflict.bin", 12, 3),
            _record("PrimaryRoot", "New/primary.txt", 5, 4),
            _record("PrimaryRoot", "Photos/Trip/photo.jpg", 20, 5),
        ])
        _write_inventory(backup, [
            _record("BackupRoot", "Library/same.txt", 10),
            _record("BackupRoot", "Library/conflict.bin", 13, 2),
            _record("BackupRoot", "Archive/backup.txt", 6),
            _record("BackupRoot", "Old Photos/photo.jpg", 20),
        ])
        files = root / "cross-files.csv"
        relocated = root / "relocated.csv"
        comparison_summary = root / "cross-summary.json"
        compare_inventories(
            primary,
            backup,
            files,
            relocated,
            root / "aggregate.csv",
            root / "folders.csv",
            root / "folder-details.csv",
            comparison_summary,
            primary_prefix="PrimaryRoot",
            backup_prefix="BackupRoot",
            primary_label="Media Server",
            backup_label="Cold Store",
        )
        return comparison_summary, files, relocated

    def test_builds_backup_gaps_and_copy_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            comparison_summary, files, relocated = self._comparison(root)
            gaps = root / "gaps.csv"
            copies = root / "copies.csv"
            review = root / "review.csv"
            backup_only_folders = root / "backup-only-folders.csv"
            plan_summary = root / "plan.json"

            result = build_backup_plan(
                comparison_summary,
                files,
                relocated,
                gaps,
                copies,
                review,
                plan_summary,
                backup_only_output=backup_only_folders,
            )

            self.assertTrue(result["complete"])
            self.assertTrue(result["read_only"])
            self.assertEqual(result["mode"], "backup_sync")
            self.assertEqual(result["primary"]["selected_root"], "/mnt/PrimaryRoot")
            self.assertEqual(result["backup"]["selected_root"], "/mnt/BackupRoot")
            self.assertEqual(result["coverage"]["backed_up_files"], 1)
            self.assertEqual(result["coverage"]["missing_files"], 2)
            self.assertEqual(result["coverage"]["missing_bytes"], 25)
            self.assertEqual(result["coverage"]["copy_candidate_files"], 1)
            self.assertEqual(result["coverage"]["copy_candidate_bytes"], 5)
            self.assertEqual(result["coverage"]["relocated_candidate_files"], 1)
            self.assertEqual(result["coverage"]["relocated_candidate_bytes"], 20)
            self.assertEqual(result["coverage"]["conflict_files"], 1)
            self.assertEqual(result["coverage"]["backup_only_files"], 2)
            self.assertEqual(result["backup_only_folder_rows"], 3)

            with copies.open(newline="", encoding="utf-8") as source:
                copy_rows = list(csv.DictReader(source))
            self.assertEqual(len(copy_rows), 1)
            by_path = {row["comparison_path"]: row for row in copy_rows}
            self.assertEqual(
                by_path["New/primary.txt"]["backup_target_path"],
                "/mnt/BackupRoot/New/primary.txt",
            )

            with gaps.open(newline="", encoding="utf-8") as source:
                gap_rows = {row["comparison_folder"]: row for row in csv.DictReader(source)}
            self.assertEqual(gap_rows["."]["status"], "needs_sync_and_review")
            self.assertEqual(gap_rows["."]["missing_bytes"], "25")
            self.assertEqual(gap_rows["New"]["byte_coverage_pct"], "0.0")
            self.assertEqual(gap_rows["Library"]["status"], "review_required")
            self.assertEqual(gap_rows["Photos"]["missing_files"], "1")
            self.assertEqual(gap_rows["Photos/Trip"]["missing_files"], "1")
            self.assertEqual(gap_rows["Photos"]["relocated_candidate_files"], "1")
            self.assertEqual(gap_rows["Photos"]["copy_candidate_files"], "0")
            self.assertEqual(gap_rows["Photos"]["status"], "review_required")

            with review.open(newline="", encoding="utf-8") as source:
                review_rows = list(csv.DictReader(source))
            self.assertEqual(len(review_rows), 4)
            self.assertEqual(
                {row["action"] for row in review_rows},
                {
                    "review_conflict",
                    "review_backup_only",
                    "review_relocated_candidate",
                },
            )
            relocated_review = next(
                row for row in review_rows
                if row["action"] == "review_relocated_candidate"
            )
            self.assertIn("Old Photos/photo.jpg", relocated_review["candidate_backup_paths"])
            relocated_backup = next(
                row for row in review_rows
                if row["action"] == "review_backup_only" and row["candidate_id"]
            )
            self.assertIn(
                "Photos/Trip/photo.jpg",
                relocated_backup["candidate_primary_paths"],
            )

            with backup_only_folders.open(newline="", encoding="utf-8") as source:
                backup_folder_rows = {
                    row["comparison_folder"]: row for row in csv.DictReader(source)
                }
            self.assertEqual(backup_folder_rows["."]["backup_only_files"], "2")
            self.assertEqual(backup_folder_rows["."]["backup_only_bytes"], "26")
            self.assertEqual(
                backup_folder_rows["Old Photos"]["status"], "relocation_review"
            )
            self.assertEqual(
                backup_folder_rows["Archive"]["status"], "recovery_review"
            )

            report = root / "backup-sync.html"
            displayed, pages = generate(
                plan_summary,
                report,
                notice="Demonstration data; rescan before copying.",
                folders_per_status=10,
                files_per_section=10,
            )
            self.assertEqual(displayed, pages)
            self.assertGreater(displayed, 0)
            page = report.read_text(encoding="utf-8")
            self.assertIn("File Tidy Backup Sync", page)
            self.assertIn("Folder Details — Backup Gaps", page)
            self.assertIn("Cold Store-only — Recovery Review", page)
            self.assertIn("Demonstration data; rescan before copying.", page)
            self.assertIn("Needs sync", page)
            self.assertIn("function sortTable", page)
            self.assertGreaterEqual(page.count('class="sort-button"'), 9)
            self.assertIn('data-direction="desc" aria-sort="descending"', page)
            detail_pages = list((root / "backup-sync-folders").glob("*.html"))
            self.assertEqual(len(detail_pages), pages)
            details = "\n".join(
                detail.read_text(encoding="utf-8") for detail in detail_pages
            )
            self.assertIn("Ready to copy", details)
            self.assertIn("Possible relocated matches", details)
            self.assertIn("Cold Store-only file evidence", details)
            self.assertIn("No filename-and-size candidate", details)
            self.assertIn("function sortTable", details)
            self.assertIn('data-sort="5"', details)

    def test_rejects_file_evidence_that_does_not_match_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            comparison_summary, files, relocated = self._comparison(root)
            rows = files.read_text(encoding="utf-8").splitlines()
            files.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "does not match its summary"):
                build_backup_plan(
                    comparison_summary,
                    files,
                    relocated,
                    root / "gaps.csv",
                    root / "copies.csv",
                    root / "review.csv",
                    root / "plan.json",
                )


if __name__ == "__main__":
    unittest.main()
