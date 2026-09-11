import io
import json
import tempfile
import unittest
from pathlib import Path

from main.scanner import estimate, scan


class ScannerTests(unittest.TestCase):
    def test_scans_files_and_skips_case_insensitive_excluded_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Keep").mkdir()
            (root / "System" / "wBeM" / "Nested").mkdir(parents=True)
            (root / "Keep" / "readme.TXT").write_text("hello", encoding="utf-8")
            (root / "System" / "wBeM" / "Nested" / "secret.txt").write_text(
                "secret", encoding="utf-8"
            )
            settings = {
                "source_type": "local",
                "source_path": str(root),
                "folders": [],
                "excluded_folders": ["WBEM"],
            }
            inventory, error_file = io.StringIO(), io.StringIO()
            result = scan(settings, inventory, error_file, progress_every=0)

            records = [json.loads(line) for line in inventory.getvalue().splitlines()]
            self.assertEqual(result.files, 1)
            self.assertEqual(records[0]["path"], "Keep/readme.TXT")
            self.assertEqual(records[0]["type"], "txt")
            self.assertEqual(result.by_type, {"txt": {"files": 1, "bytes": 5}})
            self.assertEqual(error_file.getvalue(), "")

    def test_scans_selected_folder_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "A").mkdir()
            (root / "B").mkdir()
            (root / "A" / "a").write_bytes(b"a")
            (root / "B" / "b").write_bytes(b"bb")
            settings = {
                "source_type": "local",
                "source_path": str(root),
                "folders": ["A"],
                "excluded_folders": [],
            }
            result = scan(settings, io.StringIO(), io.StringIO(), progress_every=0)
            self.assertEqual((result.files, result.bytes), (1, 1))

    def test_estimate_counts_local_files_without_writing_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "A").mkdir()
            (root / "A" / "a.txt").write_bytes(b"abc")
            settings = {
                "source_type": "local",
                "source_path": str(root),
                "folders": [],
                "excluded_folders": [],
            }
            self.assertEqual(estimate(settings), {"files": 1, "folders": 2, "bytes": 3})


if __name__ == "__main__":
    unittest.main()
