import csv
import json
import tempfile
import unittest
from pathlib import Path

from main.duplicate_report import generate
from main.duplicates import analyze


class DuplicateTests(unittest.TestCase):
    def test_builds_master_secondary_tables_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "inventory.jsonl"
            rows = []
            for folder, modified in (
                ("Current/15 Project Mail Backup", 200),
                ("Old/Project Mail Backup", 100),
            ):
                for name, size in (("one.msg", 10), ("two.msg", 20)):
                    relative = f"{folder}/{name}"
                    rows.append({
                        "path": relative,
                        "source": relative.split("/", 1)[0],
                        "absolute_path": str(Path("/mnt/test") / relative),
                        "uri": (Path("/mnt/test") / relative).as_uri(),
                        "size": size,
                        "type": "msg",
                        "depth": relative.count("/"),
                        "modified_ns": modified,
                        "parent_modified_ns": modified,
                    })
            inventory.write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            aggregate, details = root / "folders.csv", root / "files.csv"
            self.assertEqual(analyze(inventory, aggregate, details), (1, 2))
            with aggregate.open(newline="", encoding="utf-8") as source:
                candidate = next(csv.DictReader(source))
            self.assertEqual(candidate["master_folder"], "/mnt/test/Current/15 Project Mail Backup")
            self.assertEqual(candidate["secondary_folder"], "/mnt/test/Old/Project Mail Backup")
            self.assertEqual(candidate["confidence"], "high")
            self.assertEqual(candidate["matching_files"], "2")

            report = root / "duplicates.html"
            self.assertEqual(generate(aggregate, details, report), (1, 2))
            page = report.read_text(encoding="utf-8")
            self.assertIn("Master Folder", page)
            self.assertIn("Secondary Folder", page)
            self.assertIn("Files by size band", page)
            self.assertEqual(len(list((root / "duplicates-folders").glob("*.html"))), 1)


if __name__ == "__main__":
    unittest.main()
