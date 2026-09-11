"""Create a quoted comparison CSV from two scanner JSONL inventories."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def compare(inventory: Path, output: Path, first: str, second: str) -> None:
    values: dict[str, dict[str, int]] = {first: {}, second: {}}
    with inventory.open(encoding="utf-8") as source:
        for line in source:
            record = json.loads(line)
            source_name = record.get("source")
            if source_name in values:
                path = record["path"]
                prefix = f"{source_name}/"
                if path.startswith(prefix):
                    path = path[len(prefix) :]
                values[source_name][path] = int(record["size"])
    paths = sorted(set(values[first]) | set(values[second]))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.writer(destination)
        writer.writerow(["relative_path", f"{first} bytes", f"{second} bytes", "comparison"])
        for path in paths:
            left, right = values[first].get(path), values[second].get(path)
            if left is None:
                category = f"only in {second} at this path"
            elif right is None:
                category = f"only in {first} at this path"
            elif left == right:
                category = "same path and size"
            else:
                category = "different size"
            writer.writerow([path, "" if left is None else left, "" if right is None else right, category])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--output", type=Path, default=Path("scan-results/comparison.csv"))
    parser.add_argument("--first", default="PC Data")
    parser.add_argument("--second", default="85 PC Data")
    args = parser.parse_args()
    compare(args.inventory, args.output, args.first, args.second)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
