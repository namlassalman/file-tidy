import unittest

from main.report import render


class ReportTests(unittest.TestCase):
    def test_render_contains_summary_and_escaped_types(self):
        page = render(
            {
                "source_type": "local",
                "files": 2,
                "bytes": 5,
                "folders": 1,
                "errors": 0,
                "by_type": {"txt": {"files": 2, "bytes": 5}, "<bad>": {"files": 0, "bytes": 0}},
            }
        )
        self.assertIn("2", page)
        self.assertIn("&lt;bad&gt;", page)
        self.assertIn("sortTable", page)


if __name__ == "__main__":
    unittest.main()
