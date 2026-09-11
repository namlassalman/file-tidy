"""Generate an interactive HTML report for drive-wide duplicate candidates."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
from collections import Counter, defaultdict
from pathlib import Path

from .report import _size


COLORS = ["#367bf5", "#ef6c57", "#f5b82e", "#45a66f", "#9b59b6", "#16a085"]
SIZE_BANDS = [
    ("> 1 GB", 1024**3, None),
    ("500 MB–1 GB", 500 * 1024**2, 1024**3),
    ("100–500 MB", 100 * 1024**2, 500 * 1024**2),
    ("50–100 MB", 50 * 1024**2, 100 * 1024**2),
    ("10–50 MB", 10 * 1024**2, 50 * 1024**2),
    ("5–10 MB", 5 * 1024**2, 10 * 1024**2),
    ("< 5 MB", 0, 5 * 1024**2),
]


def _slug(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def _page(title: str, body: str) -> str:
    style = "body{font:15px system-ui,sans-serif;max-width:1400px;margin:1.5rem auto;padding:0 1rem;color:#17202a}nav{margin-bottom:1rem}.muted{color:#617384}.cards{display:flex;gap:1rem;flex-wrap:wrap}.card{background:#eef3f7;border-radius:.6rem;padding:1rem;min-width:11rem}.card b,.card small{display:block;margin-top:.3rem}.layout{display:grid;grid-template-columns:minmax(0,1fr) 17rem;gap:1.5rem}aside{background:#f7f9fb;padding:1rem;border-radius:.6rem;align-self:start;position:sticky;top:1rem}table{border-collapse:collapse;width:100%;font-size:.86rem}th,td{padding:.45rem;border-bottom:1px solid #d9e1e8;text-align:left;vertical-align:top}th{position:sticky;top:0;background:white}input{box-sizing:border-box;padding:.55rem;width:100%;margin:.5rem 0 1rem}.bar-row{display:grid;grid-template-columns:13rem 1fr 8rem;gap:.7rem;align-items:center;margin:.45rem 0}.bar{height:1.15rem;background:#367bf5;border-radius:.25rem;min-width:1px}.stack{display:flex;height:1.2rem;background:#eef3f7;border-radius:.25rem;overflow:hidden}.segment{height:100%}.legend span{display:inline-block;margin:.25rem .8rem .25rem 0}.swatch{display:inline-block;width:.8rem;height:.8rem;margin-right:.25rem;border-radius:.15rem}details{margin:1rem 0}summary{cursor:pointer;font-weight:650;margin:.75rem 0}button{display:block;width:100%;text-align:left;margin:.25rem 0;padding:.4rem}.path{word-break:break-word}a{color:#145dcc}.high{color:#18783b}.medium{color:#9a6500}.low{color:#a43b32}@media(max-width:850px){.layout{grid-template-columns:1fr}aside{position:static}.bar-row{grid-template-columns:8rem 1fr 6rem}}"
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>{style}</style></head><body>{body}</body></html>'


def _bars(values: list[tuple[str, int]], formatter=lambda value: f"{value:,}") -> str:
    maximum = max((value for _label, value in values), default=1) or 1
    return "".join(
        f'<div class="bar-row"><span>{html.escape(label)}</span><div class="bar" style="width:{value / maximum * 100:.2f}%"></div><b>{html.escape(formatter(value))}</b></div>'
        for label, value in values
    )


def _file_type_visual(rows: list[dict[str, str]]) -> str:
    values = Counter()
    for row in rows:
        suffix = Path(row["file_name"]).suffix.casefold()[1:] or "[no extension]"
        values[suffix] += int(row["size_bytes"])
    return _bars(values.most_common(15), _size) or '<p class="muted">No matching files.</p>'


def generate(
    aggregate_csv: Path,
    detail_csv: Path,
    output: Path,
    candidate_limit: int = 300,
    detail_limit: int = 1000,
) -> tuple[int, int]:
    with aggregate_csv.open(newline="", encoding="utf-8") as source:
        all_candidates = list(csv.DictReader(source))
    candidates = all_candidates[:candidate_limit]
    candidate_ids = {row["candidate_group"] for row in candidates}
    details: dict[str, list[dict[str, str]]] = defaultdict(list)
    detail_counts = Counter()
    with detail_csv.open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            group = row["candidate_group"]
            if group not in candidate_ids:
                continue
            detail_counts[group] += 1
            if len(details[group]) < detail_limit:
                details[group].append(row)

    output.parent.mkdir(parents=True, exist_ok=True)
    detail_dir = output.parent / f"{output.stem}-folders"
    detail_dir.mkdir(parents=True, exist_ok=True)
    candidate_by_id = {row["candidate_group"]: row for row in candidates}

    for group, candidate in candidate_by_id.items():
        group_rows = details[group]
        file_rows = "".join(
            f'<tr><td class="path">{html.escape(row["master_location"])}</td><td class="path">{html.escape(row["secondary_location"])}</td><td>{html.escape(row["file_name"])}</td><td>{_size(int(row["size_bytes"]))}</td><td>Hash required</td></tr>'
            for row in group_rows
        )
        truncated = "" if detail_counts[group] <= detail_limit else f'<p class="muted">Showing the largest {detail_limit:,} of {detail_counts[group]:,} matching file groups. The CSV retains the full evidence.</p>'
        body = f'''<nav><a href="../{html.escape(output.name)}">← Duplicate index</a></nav><h1>{html.escape(group)} duplicate candidate</h1><p><b>Master Folder:</b> <span class="path">{html.escape(candidate["master_folder"])}</span></p><p><b>Secondary Folder:</b> <span class="path">{html.escape(candidate["secondary_folder"])}</span></p><p class="muted">Exact normalized filenames and byte sizes are high-confidence candidates. Content hashes are required before an automated deletion recommendation.</p><details open><summary>File types</summary>{_file_type_visual(group_rows)}</details><section class="cards"><div class="card">Confidence<b class="{html.escape(candidate['confidence'])}">{html.escape(candidate['confidence'].title())}</b></div><div class="card">Matching files<b>{int(candidate['matching_files']):,}</b></div><div class="card">Candidate size<b>{_size(int(candidate['matching_bytes']))}</b></div><div class="card">Smaller-folder coverage<b>{html.escape(candidate['smaller_folder_coverage_pct'])}%</b></div></section><details open><summary>Matching-file evidence</summary>{truncated}<input id="file-filter" placeholder="Filter paths or filenames"><table id="files"><thead><tr><th>Master location</th><th>Secondary location</th><th>Filename</th><th>Size</th><th>Status</th></tr></thead><tbody>{file_rows}</tbody></table></details><script>const f=document.getElementById('file-filter');f.oninput=()=>{{const q=f.value.toLowerCase();document.querySelectorAll('#files tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q));}};</script>'''
        (detail_dir / f"{_slug(group)}.html").write_text(_page(f"File Tidy – {group}", body), encoding="utf-8")

    confidence = Counter(row["confidence"] for row in candidates)
    by_master = Counter()
    for row in candidates:
        by_master[row["master_folder"]] += int(row["potential_savings_bytes"])
    top_masters = [folder for folder, _bytes in by_master.most_common(10)]

    table_rows = []
    for row in candidates:
        link = f'{output.stem}-folders/{_slug(row["candidate_group"])}.html'
        table_rows.append(
            f'<tr data-master="{html.escape(row["master_folder"].casefold())}"><td class="path"><a href="{link}">{html.escape(row["master_folder"])}</a></td><td class="path"><a href="{link}">{html.escape(row["secondary_folder"])}</a></td><td>{html.escape(row["parent_folder"] or "(root)")}</td><td class="{html.escape(row["confidence"])}">{html.escape(row["confidence"].title())}</td><td>{int(row["matching_files"]):,}</td><td>{_size(int(row["matching_bytes"]))}</td><td>{html.escape(row["smaller_folder_coverage_pct"])}%</td></tr>'
        )

    selected_details = [row for rows in details.values() for row in rows]
    band_values = []
    for label, lower, upper in SIZE_BANDS:
        group_ids = {
            row["candidate_group"] for row in selected_details
            if int(row["size_bytes"]) >= lower and (upper is None or int(row["size_bytes"]) < upper)
        }
        band_values.append((label, len(group_ids)))
    depth_counts = Counter(row["secondary_folder"].count("/") for row in candidates)

    type_totals = Counter()
    type_by_master: dict[str, Counter[str]] = defaultdict(Counter)
    for row in selected_details:
        suffix = Path(row["file_name"]).suffix.casefold()[1:] or "[no extension]"
        size = int(row["size_bytes"])
        type_totals[suffix] += size
        master = candidate_by_id[row["candidate_group"]]["master_folder"]
        type_by_master[master][suffix] += size
    top_types = [kind for kind, _bytes in type_totals.most_common(8)]
    stack_legend = "".join(
        f'<span><i class="swatch" style="background:{COLORS[index % len(COLORS)]}"></i>{html.escape(kind)}</span>'
        for index, kind in enumerate(top_types)
    )
    stacks = []
    for master in top_masters:
        values = type_by_master[master]
        total = sum(values.values()) or 1
        segments = "".join(
            f'<span class="segment" style="width:{values[kind] / total * 100:.2f}%;background:{COLORS[index % len(COLORS)]}" title="{html.escape(kind)}: {_size(values[kind])}"></span>'
            for index, kind in enumerate(top_types)
        )
        stacks.append(f'<div class="bar-row"><span class="path">{html.escape(Path(master).name)}</span><div class="stack">{segments}</div><b>{_size(total)}</b></div>')

    buttons = '<button onclick="filterMaster(\'\')">Show all</button>' + "".join(
        f'<button onclick="filterMaster({html.escape(repr(master.casefold()))})">{html.escape(Path(master).name)}</button>'
        for master in top_masters
    )
    definitions = '''<details open><summary>How to read these candidates</summary><ul><li><b>Master Folder:</b> the most recently active non-trash folder in its related candidate group.</li><li><b>Secondary Folder:</b> another location containing normalized exact filenames with the same byte sizes.</li><li><b>High confidence:</b> matching candidates cover at least 80% of the smaller folder; medium covers at least 50%.</li><li><b>Candidate size:</b> possible savings for that row. Do not add rows together because folder candidates can overlap.</li><li><b>Verification:</b> matching names and sizes are evidence, not content proof. Hashes are required before automated deletion advice.</li></ul></details>'''
    body = f'''<nav><a href="{html.escape(output.name)}">Index</a></nav><h1>File Tidy duplicate candidates</h1><p class="muted">Read-only analysis from a completed local inventory. Showing the top {len(candidates):,} of {len(all_candidates):,} folder candidates.</p>{definitions}<section class="cards"><div class="card">Folder candidates<b>{len(all_candidates):,}</b></div><div class="card">High confidence shown<b>{confidence['high']:,}</b></div><div class="card">Medium confidence shown<b>{confidence['medium']:,}</b></div><div class="card">Largest candidate<b>{_size(max((int(row['matching_bytes']) for row in candidates), default=0))}</b></div></section><details open><summary>Folder details</summary><input id="folder-filter" placeholder="Filter folders, confidence, or counts"><table id="folders"><thead><tr><th>Master Folder</th><th>Secondary Folder</th><th>Parent Folder</th><th>Confidence</th><th>Matching files</th><th>Candidate size</th><th>Coverage</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table></details><div class="layout"><main><details open><summary>Files by size band and candidate-folder count</summary>{_bars(band_values)}</details><details open><summary>File types</summary>{_bars(type_totals.most_common(20), _size)}</details><details><summary>Path depth</summary>{_bars([(f'Depth {depth}', count) for depth, count in sorted(depth_counts.items())])}</details><details><summary>File size by folder and type</summary><div class="legend">{stack_legend}</div>{''.join(stacks)}</details></main><aside><h2>Top ten Master folders</h2><p class="muted">Filter Folder Details.</p>{buttons}</aside></div><script>const input=document.getElementById('folder-filter');function apply(){{const q=input.value.toLowerCase();document.querySelectorAll('#folders tbody tr').forEach(r=>r.hidden=(window.masterFilter&&r.dataset.master!==window.masterFilter)||!r.textContent.toLowerCase().includes(q));}}input.oninput=apply;function filterMaster(value){{window.masterFilter=value;apply();}}</script>'''
    output.write_text(_page("File Tidy duplicate candidates", body), encoding="utf-8")
    return len(all_candidates), len(selected_details)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("aggregate", type=Path)
    parser.add_argument("details", type=Path)
    parser.add_argument("--output", type=Path, default=Path("scan-results/duplicates.html"))
    parser.add_argument("--candidate-limit", type=int, default=300)
    parser.add_argument("--detail-limit", type=int, default=1000)
    args = parser.parse_args()
    candidates, details = generate(args.aggregate, args.details, args.output, args.candidate_limit, args.detail_limit)
    print(f"Wrote report for {candidates:,} candidates using {details:,} displayed file matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
