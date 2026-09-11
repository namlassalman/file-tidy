"""Generate a small interactive HTML report from a scanner summary."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>File Tidy report</title>
<style>
body {{ font: 16px system-ui, sans-serif; max-width: 1000px; margin: 2rem auto; padding: 0 1rem; color: #17202a; }}
.cards {{ display: flex; gap: 1rem; flex-wrap: wrap; }}
.card {{ background: #eef3f7; border-radius: .6rem; padding: 1rem 1.25rem; min-width: 8rem; }}
.value {{ display: block; font-size: 1.5rem; font-weight: 700; }}
.chart-row {{ display: grid; grid-template-columns: 10rem 1fr 8rem; gap: .75rem; align-items: center; margin: .45rem 0; }}
.bar {{ background: #367bf5; height: 1.1rem; border-radius: .25rem; min-width: 2px; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
th, td {{ border-bottom: 1px solid #d9e1e8; padding: .5rem; text-align: left; }}
th {{ cursor: pointer; }}
.muted {{ color: #617384; }}
</style>
</head>
<body>
<h1>File Tidy report</h1>
<p class="muted">Read-only inventory for {source_type}.</p>
<section class="cards">
  <div class="card"><span>Files</span><span class="value">{files}</span></div>
  <div class="card"><span>Total size</span><span class="value">{total_size}</span></div>
  <div class="card"><span>Folders</span><span class="value">{folders}</span></div>
  <div class="card"><span>Access errors</span><span class="value">{errors}</span></div>
</section>
<h2>Storage by file type</h2>
<div id="chart">{bars}</div>
<h2>Details</h2>
<table id="details"><thead><tr><th onclick="sortTable(0)">Type</th><th onclick="sortTable(1)">Files</th><th onclick="sortTable(2)">Bytes</th></tr></thead>
<tbody>{rows}</tbody></table>
<script>
function sortTable(column) {{ const table=document.getElementById('details'), rows=[...table.tBodies[0].rows];
  const numeric=column>0; rows.sort((a,b)=>{{const x=a.cells[column].textContent,y=b.cells[column].textContent; return (numeric?Number(x.replaceAll(',','')):x).toString().localeCompare((numeric?Number(y.replaceAll(',','')):y).toString(), undefined, numeric?{{numeric:true}}:{{}});}});
  rows.forEach(row=>table.tBodies[0].appendChild(row)); }}
</script>
</body></html>
"""


def _size(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{value:,} B"
        amount /= 1024
    return f"{value:,} B"


def render(summary: dict) -> str:
    by_type = summary.get("by_type", {})
    largest = max((item.get("bytes", 0) for item in by_type.values()), default=0)
    bars, rows = [], []
    for kind, item in sorted(by_type.items(), key=lambda pair: pair[1].get("bytes", 0), reverse=True):
        label = html.escape(kind)
        size = int(item.get("bytes", 0))
        count = int(item.get("files", 0))
        width = (size / largest * 100) if largest else 0
        bars.append(f'<div class="chart-row"><span>{label}</span><div class="bar" style="width:{width:.2f}%" title="{size:,} bytes"></div><span>{_size(size)}</span></div>')
        rows.append(f"<tr><td>{label}</td><td>{count:,}</td><td>{size:,}</td></tr>")
    return HTML_TEMPLATE.format(
        source_type=html.escape(str(summary.get("source_type", "unknown"))),
        files=f"{int(summary.get('files', 0)):,}",
        total_size=_size(int(summary.get("bytes", 0))),
        folders=f"{int(summary.get('folders', 0)):,}",
        errors=f"{int(summary.get('errors', 0)):,}",
        bars="\n".join(bars) or '<p class="muted">No files found.</p>',
        rows="\n".join(rows),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path, help="Scanner summary JSON file")
    parser.add_argument("--output", type=Path, default=Path("scan-results/report.html"))
    args = parser.parse_args()
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(summary), encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
