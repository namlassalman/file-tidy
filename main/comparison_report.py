"""Generate an interactive comparison index and folder drill-down pages."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .report import _size

CATEGORIES = {
    "only in PC Data at this path",
    "only in 85 PC Data at this path",
    "same path and size",
    "different size",
}
SOURCES = (("left", "Comparison 1 – PC Data"), ("right", "Comparison 2 – 85 PC Data"))
COLORS = ["#367bf5", "#ef6c57", "#f5b82e", "#45a66f", "#9b59b6", "#16a085"]
SIZE_BANDS = [
    (" > 1 GB", 1024**3, None),
    ("500 MB–1 GB", 500 * 1024**2, 1024**3),
    ("400–500 MB", 400 * 1024**2, 500 * 1024**2),
    ("300–400 MB", 300 * 1024**2, 400 * 1024**2),
    ("200–300 MB", 200 * 1024**2, 300 * 1024**2),
    ("100–200 MB", 100 * 1024**2, 200 * 1024**2),
    ("50–100 MB", 50 * 1024**2, 100 * 1024**2),
    ("40–50 MB", 40 * 1024**2, 50 * 1024**2),
    ("30–40 MB", 30 * 1024**2, 40 * 1024**2),
    ("20–30 MB", 20 * 1024**2, 30 * 1024**2),
    ("10–20 MB", 10 * 1024**2, 20 * 1024**2),
    ("5–10 MB", 5 * 1024**2, 10 * 1024**2),
    ("< 5 MB", 0, 5 * 1024**2),
]


def _read_rows(path: Path) -> list[dict[str, object]]:
    rows = []
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.reader(source)
        next(reader, None)
        for fields in reader:
            index = next((i for i, value in enumerate(fields) if value in CATEGORIES), None)
            if index is None or index < 2:
                continue
            rows.append({"path": ",".join(fields[: index - 2]), "left": int(fields[index - 2] or 0), "right": int(fields[index - 1] or 0), "category": fields[index]})
    return rows


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "root"


def _size_band(value: int) -> int:
    for index, (_, lower, upper) in enumerate(SIZE_BANDS):
        if upper is None and value > lower:
            return index
        if upper is not None and lower <= value < upper:
            return index
    return len(SIZE_BANDS) - 1


def _page(title: str, body: str) -> str:
    style = "body{font:16px system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#17202a}.cards{display:flex;gap:1rem;flex-wrap:wrap}.card{background:#eef3f7;border-radius:.6rem;padding:1rem;min-width:10rem}.card b,.card small{display:block;margin-top:.3rem}.bar-row{display:grid;grid-template-columns:14rem 1fr 8rem;gap:.7rem;align-items:center;margin:.4rem 0}.bar{height:1.1rem;background:#367bf5;border-radius:.25rem}.stack-row{display:grid;grid-template-columns:14rem 1fr 7rem;gap:.7rem;align-items:center;margin:.5rem 0}.stack{display:flex;height:1.25rem;background:#eef3f7;border-radius:.25rem;overflow:hidden}.segment{height:100%}.legend span{display:inline-block;margin:.25rem .75rem .25rem 0}.swatch{display:inline-block;width:.8rem;height:.8rem;margin-right:.25rem;border-radius:.15rem}table{border-collapse:collapse;width:100%;font-size:.9rem}th,td{padding:.45rem;border-bottom:1px solid #d9e1e8;text-align:left}th{cursor:pointer}input{padding:.5rem;width:100%;margin:.5rem 0 1rem}.muted{color:#617384}a{color:#145dcc}summary{cursor:pointer;font-weight:600;margin:.75rem 0}.layout{display:grid;grid-template-columns:minmax(0,1fr) 16rem;gap:1.5rem}aside{background:#f7f9fb;padding:1rem;border-radius:.6rem}button{margin:.2rem 0;padding:.3rem .5rem}"
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>{style}</style></head><body>{body}</body></html>'


def _type_bars(rows: list[dict[str, object]]) -> str:
    totals = {key: Counter() for key, _ in SOURCES}
    for row in rows:
        suffix = Path(str(row["path"])).suffix.casefold()[1:] or "[no extension]"
        for key, _ in SOURCES:
            totals[key][suffix] += int(row[key])
    types = sorted(set(totals["left"]) | set(totals["right"]), key=lambda t: totals["left"][t] + totals["right"][t], reverse=True)[:20]
    maximum = max((max(totals["left"][t], totals["right"][t]) for t in types), default=1)
    out = []
    for t in types:
        out.append(f'<div class="bar-row"><span>{html.escape(t)}</span><div><div class="bar" style="width:{totals["left"][t] / maximum * 100:.2f}%;background:{COLORS[0]}" title="{SOURCES[0][1]}"></div><div class="bar" style="width:{totals["right"][t] / maximum * 100:.2f}%;background:{COLORS[1]}" title="{SOURCES[1][1]}"></div></div><span>{_size(totals["left"][t] + totals["right"][t])}</span></div>')
    legend = f'<div class="legend"><span><i class="swatch" style="background:{COLORS[0]}"></i>{SOURCES[0][1]}</span><span><i class="swatch" style="background:{COLORS[1]}"></i>{SOURCES[1][1]}</span></div>'
    return legend + (''.join(out) or '<p class="muted">No data.</p>')


def _folder_size_stacks(groups: dict[str, list[dict[str, object]]]) -> str:
    totals = Counter()
    by_folder = {}
    for folder, rows in groups.items():
        values = Counter()
        for row in rows:
            kind = Path(str(row["path"])).suffix.casefold()[1:] or "[no extension]"
            values[kind] += max(int(row["left"]), int(row["right"]))
            totals[kind] += max(int(row["left"]), int(row["right"]))
        by_folder[folder] = values
    types = [kind for kind, _ in totals.most_common(8)]
    maximum = max((sum(values.values()) for values in by_folder.values()), default=1)
    legend = ''.join(f'<span><i class="swatch" style="background:{COLORS[i % len(COLORS)]}"></i>{html.escape(kind)}</span>' for i, kind in enumerate(types))
    rows_html = []
    for folder, values in sorted(by_folder.items(), key=lambda item: sum(item[1].values()), reverse=True)[:20]:
        total = sum(values.values()) or 1
        segments = ''.join(f'<span class="segment" style="width:{values.get(kind, 0) / total * 100:.2f}%;background:{COLORS[i % len(COLORS)]}" title="{html.escape(kind)}: {_size(values.get(kind, 0))}"></span>' for i, kind in enumerate(types))
        rows_html.append(f'<div class="stack-row"><span>{html.escape(folder)}</span><div class="stack">{segments}</div><b>{_size(total)}</b></div>')
    return f'<div class="legend">{legend}</div>{"".join(rows_html)}'


def generate(csv_path: Path, output: Path) -> None:
    rows = _read_rows(csv_path)
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        path = str(row["path"])
        groups[path.split("/", 1)[0] if "/" in path else "(root files)"].append(row)
    output.parent.mkdir(parents=True, exist_ok=True)
    detail_dir = output.parent / f"{output.stem}-folders"
    detail_dir.mkdir(parents=True, exist_ok=True)
    folder_links, folder_size = [], []
    folder_bands: dict[str, dict[str, list[set[str]]]] = {}
    for folder, folder_rows in sorted(groups.items(), key=lambda item: sum(max(int(r["left"]), int(r["right"])) for r in item[1]), reverse=True):
        slug = _slug(folder)
        stats = Counter(str(r["category"]) for r in folder_rows)
        type_html = _type_bars(folder_rows)
        rows_html = ''.join(f'<tr><td>{html.escape(str(r["path"]))}</td><td>{html.escape(str(r["category"]))}</td><td>{int(r["left"]):,}</td><td>{int(r["right"]):,}</td></tr>' for r in sorted(folder_rows, key=lambda r: max(int(r["left"]), int(r["right"])), reverse=True)[:500])
        summary_cards = ''.join(f'<div class="card"><span>{html.escape(k)}</span><b>{v:,}</b></div>' for k, v in stats.items())
        detail_body = f'<nav><a href="../{html.escape(output.name)}">← Comparison index</a></nav><h1>{html.escape(folder)}</h1><p class="muted">Folder-specific file-type visual. Candidate overlap requires content-hash verification.</p><details open><summary>File types in this folder</summary><div class="legend"><span><i class="swatch" style="background:{COLORS[0]}"></i>{SOURCES[0][1]}</span><span><i class="swatch" style="background:{COLORS[1]}"></i>{SOURCES[1][1]}</span></div>{type_html}</details><details><summary>Comparison summary</summary><section class="cards">{summary_cards}</section></details><details><summary>Files</summary><input id="filter" placeholder="Filter paths or categories"><table id="files"><thead><tr><th>Path</th><th>Category</th><th>Comparison 1 bytes</th><th>Comparison 2 bytes</th></tr></thead><tbody>{rows_html}</tbody></table></details><script>const i=document.getElementById("filter");i.oninput=()=>{{const q=i.value.toLowerCase();document.querySelectorAll("#files tbody tr").forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q));}};</script>'
        (detail_dir / f"{slug}.html").write_text(_page(f"File Tidy – {folder}", detail_body), encoding="utf-8")
        folder_links.append(f'<tr><td><a href="{output.stem}-folders/{slug}.html">{html.escape(folder)}</a></td><td>{len(folder_rows):,}</td><td>{_size(sum(max(int(r["left"]), int(r["right"])) for r in folder_rows))}</td></tr>')
        folder_size.append((folder, sum(max(int(r["left"]), int(r["right"])) for r in folder_rows)))
        folder_bands[folder] = {key: [set() for _ in SIZE_BANDS] for key, _ in SOURCES}
        for row in folder_rows:
            for key, _ in SOURCES:
                if int(row[key]) > 0:
                    folder_bands[folder][key][_size_band(int(row[key]))].add(str(row["path"]))
    top = [folder for folder, _ in sorted(folder_size, key=lambda item: item[1], reverse=True)[:10]]
    stacked = []
    folder_colors = {folder: COLORS[i % len(COLORS)] for i, folder in enumerate(groups)}
    for band_index, (label, _, _) in enumerate(SIZE_BANDS):
        present = [folder for folder in groups if folder_bands[folder]["left"][band_index] or folder_bands[folder]["right"][band_index]]
        total = max(len(present), 1)
        segments = ''.join(f'<span class="segment folder-segment" data-folder="{html.escape(folder.casefold())}" style="width:{100 / total:.2f}%;background:{folder_colors[folder]}" title="{html.escape(folder)}"></span>' for folder in present)
        stacked.append(f'<div class="stack-row" data-band="{band_index}"><span>{html.escape(label)}</span><div class="stack">{segments}</div><b class="band-total">{len(present):,} folders</b></div>')
    legend = ''.join(f'<span><i class="swatch" style="background:{folder_colors[folder]}"></i>{html.escape(folder)}</span>' for folder in groups)
    folder_buttons = ''.join(f'<button onclick="filterFolder({html.escape(json.dumps(folder.casefold()))})">{html.escape(folder)}</button><br>' for folder in top)
    size_bars = _folder_size_stacks(groups)
    categories = Counter(str(row["category"]) for row in rows)
    folder_table = "".join(folder_links)
    stacked_html = "".join(stacked)
    type_bars = _type_bars(rows)
    depth_bars = _bars_depth(rows)
    folder_links = [link for (_, size), link in sorted(zip(folder_size, folder_links), key=lambda item: item[0][1], reverse=True)]
    definitions = '<details open><summary>Comparison category definitions</summary><ul><li><b>Only in:</b> the relative path occurs in one source only; renamed or moved files can still be duplicates.</li><li><b>Same relative path and size:</b> a candidate; hashes are needed to confirm identical contents.</li><li><b>Different size:</b> the path occurs in both sources but byte counts differ.</li></ul><p class="muted">“Only in” describes paths, not guaranteed unique content.</p></details>'
    body = f'''<nav><a href="{html.escape(output.name)}">Index</a></nav><h1>File Tidy comparison index</h1><p class="muted">Read-only EDA from <code>{html.escape(csv_path.name)}</code>. Candidate overlap requires content-hash verification.</p>{definitions}<details open><summary>Folder details</summary><table><thead><tr><th>Folder</th><th>Compared files</th><th>Combined candidate size</th></tr></thead><tbody>{"".join(folder_links)}</tbody></table></details><div class="layout"><main><details open><summary>Files by size band and folder count</summary><div class="legend">{legend}</div>{stacked_html}</details><details open><summary>File types</summary>{type_bars}</details><details open><summary>Path depth</summary>{depth_bars}</details><details open><summary>File size by folder and type</summary>{size_bars}</details></main><aside><h2>Top ten folders</h2><p class="muted">Filter the size-band chart.</p><button onclick="filterFolder('')">Show all</button>{folder_buttons}</aside></div><script>function filterFolder(q){{document.querySelectorAll('.stack-row').forEach(row=>{{const segments=[...row.querySelectorAll('.folder-segment')];const shown=segments.filter(s=>!q||s.dataset.folder===q);segments.forEach(s=>s.style.display=shown.includes(s)?'block':'none');const total=Math.max(shown.length,1);shown.forEach(s=>s.style.width=(100/total)+'%');row.querySelector('.band-total').textContent=(q?shown.length:segments.length).toLocaleString()+' folders';}});}}</script>'''
    output.write_text(_page("File Tidy comparison index", body), encoding="utf-8")


def _bars_depth(rows: list[dict[str, object]]) -> str:
    counts = {key: Counter() for key, _ in SOURCES}
    for row in rows:
        depth = str(row["path"]).count("/") + 1
        for key, _ in SOURCES:
            if int(row[key]) > 0:
                counts[key][depth] += 1
    maximum = max((max(counter.values(), default=0) for counter in counts.values()), default=1)
    bars = []
    for depth in sorted(set(counts["left"]) | set(counts["right"])):
        left = counts["left"][depth]
        right = counts["right"][depth]
        bars.append(f'<div class="bar-row"><span>Depth {depth}</span><div class="bar" style="width:{(left + right) / (maximum * 2) * 100:.2f}%"></div><span>{left + right:,}</span></div>')
    return "".join(bars)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output", type=Path, default=Path("scan-results/comparison.html"))
    args = parser.parse_args()
    generate(args.csv, args.output)
    print(f"Wrote {args.output} and folder pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
