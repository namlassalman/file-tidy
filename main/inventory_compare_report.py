"""Generate an interactive HTML report for a two-inventory comparison."""

from __future__ import annotations

import argparse
import csv
import heapq
import html
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import count, islice
from pathlib import Path


COLORS = {
    "same_path_same_size": "#2f855a",
    "same_path_different_size": "#c05621",
    "primary_only": "#2b6cb0",
    "backup_only": "#805ad5",
}

LABELS = {
    "same_path_same_size": "Same path and size",
    "same_path_different_size": "Same path, different size",
    "primary_only": "Primary only",
    "backup_only": "Backup only",
}

STYLE = '''body{font:15px system-ui,sans-serif;max-width:1500px;margin:1.5rem auto;padding:0 1rem;color:#17202a}h1{margin-bottom:.2rem}.muted{color:#617384}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.8rem;margin:1rem 0}.card{background:#eef3f7;border-radius:.6rem;padding:1rem}.card b{display:block;font-size:1.35rem;margin-top:.3rem}.bar-row{display:grid;grid-template-columns:13rem minmax(10rem,1fr) 8rem 14rem;gap:.7rem;align-items:center;margin:.55rem 0}.bar-track{height:1.2rem;background:#eef3f7;border-radius:.25rem;overflow:hidden}.bar{height:100%;min-width:1px}details{margin:1rem 0}summary{cursor:pointer;font-weight:650}input{box-sizing:border-box;width:100%;padding:.55rem;margin:.5rem 0}.table-wrap{max-height:36rem;overflow:auto;border:1px solid #d9e1e8;border-radius:.4rem}table{border-collapse:collapse;width:100%;font-size:.86rem}th,td{padding:.45rem;border-bottom:1px solid #d9e1e8;text-align:left;vertical-align:top}thead th{position:sticky;top:0;background:white}.path{word-break:break-word;max-width:40rem}a{color:#145dcc}.high{color:#18783b}.medium{color:#9a6500}.low{color:#a43b32}nav{margin-bottom:1rem}@media(max-width:850px){.bar-row{grid-template-columns:9rem 1fr 6rem}.bar-row span:last-child{display:none}}'''


def _size(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if abs(amount) < 1000 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount):,} B"
        amount /= 1000
    return f"{value:,} B"


def _time(value: str) -> str:
    try:
        nanoseconds = int(value)
    except (TypeError, ValueError):
        return ""
    if not nanoseconds:
        return "Unknown"
    return datetime.fromtimestamp(nanoseconds / 1_000_000_000, timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )


def _link(uri: str, path: str) -> str:
    label = html.escape(path)
    if not uri:
        return f'<span class="path">{label}</span>'
    return (
        f'<a class="path" href="{html.escape(uri, quote=True)}" '
        f'title="Open local file">{label}</a>'
    )


def _top_file_rows(path: Path, limits: dict[str, int]) -> dict[str, list[dict[str, str]]]:
    heaps: dict[str, list[tuple[int, int, dict[str, str]]]] = {
        status: [] for status in limits
    }
    sequence = count()
    with path.open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            status = row["status"]
            if status not in heaps:
                continue
            size = max(
                int(row["primary_size_bytes"] or 0),
                int(row["backup_size_bytes"] or 0),
            )
            item = (size, next(sequence), row)
            if len(heaps[status]) < limits[status]:
                heapq.heappush(heaps[status], item)
            else:
                heapq.heappushpop(heaps[status], item)
    return {
        status: [item[2] for item in sorted(values, reverse=True)]
        for status, values in heaps.items()
    }


def _locations(value: str, limit: int = 20) -> str:
    locations = json.loads(value)
    shown = locations[:limit]
    lines = "".join(f'<div class="path">{html.escape(item)}</div>' for item in shown)
    remaining = len(locations) - len(shown)
    if remaining:
        lines += f'<div class="muted">and {remaining:,} more in the CSV</div>'
    return lines


def _section(
    title: str,
    description: str,
    table_id: str,
    headers: list[str],
    rows: list[str],
    *,
    opened: bool = False,
) -> str:
    open_attribute = " open" if opened else ""
    body = "".join(rows) or '<tr><td colspan="8" class="muted">No rows.</td></tr>'
    return f'''<details{open_attribute}><summary>{html.escape(title)}</summary>
<p class="muted">{html.escape(description)}</p>
<input type="search" placeholder="Filter this table" oninput="filterRows(this, '{table_id}')">
<div class="table-wrap"><table id="{table_id}"><thead><tr>{''.join(f'<th>{html.escape(value)}</th>' for value in headers)}</tr></thead><tbody>{body}</tbody></table></div>
</details>'''


def _file_type_visual(rows: list[dict[str, str]]) -> str:
    totals: Counter[str] = Counter()
    for row in rows:
        suffix = Path(row["file_name"]).suffix.casefold()[1:] or "[no extension]"
        totals[suffix] += int(row["size_bytes"]) * int(row["secondary_file_count"])
    maximum = max(totals.values(), default=1)
    return "".join(
        f'''<div class="bar-row"><span>{html.escape(kind)}</span>
<div class="bar-track"><div class="bar" style="width:{size / maximum * 100:.2f}%;background:#367bf5"></div></div>
<b>{_size(size)}</b><span></span></div>'''
        for kind, size in totals.most_common(20)
    ) or '<p class="muted">No matching-file evidence.</p>'


def _folder_candidates(
    folders_csv: Path,
    folder_details_csv: Path,
    output: Path,
    limit: int = 500,
) -> tuple[str, int]:
    with folders_csv.open(newline="", encoding="utf-8") as source:
        candidates = list(islice(csv.DictReader(source), limit))
    candidate_ids = {row["candidate_id"] for row in candidates}
    evidence: dict[str, list[dict[str, str]]] = defaultdict(list)
    evidence_totals: Counter[str] = Counter()
    with folder_details_csv.open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            candidate_id = row["candidate_id"]
            if candidate_id not in candidate_ids:
                continue
            evidence_totals[candidate_id] += 1
            if len(evidence[candidate_id]) < 500:
                evidence[candidate_id].append(row)

    page_directory = output.with_name(output.stem + "-folders")
    page_directory.mkdir(parents=True, exist_ok=True)
    table_rows = []
    for candidate in candidates:
        candidate_id = candidate["candidate_id"]
        page_name = candidate_id.casefold() + ".html"
        rows = evidence[candidate_id]
        file_rows = "".join(
            f'''<tr><td>{html.escape(row['file_name'])}</td><td>{_size(int(row['size_bytes']))}</td>
<td>{int(row['secondary_file_count']):,}</td><td>{_locations(row['master_locations'])}</td>
<td>{_locations(row['secondary_locations'])}</td><td>{html.escape(row['verification_status'])}</td></tr>'''
            for row in rows
        ) or '<tr><td colspan="6" class="muted">No evidence rows.</td></tr>'
        truncated = evidence_totals[candidate_id] - len(rows)
        truncation_note = (
            f'<p class="muted">Showing 500 evidence rows; {truncated:,} more remain in the CSV.</p>'
            if truncated else ""
        )
        page_body = f'''<nav><a href="../{html.escape(output.name)}">← Cross-drive index</a></nav>
<h1>{html.escape(candidate_id)} folder candidate</h1>
<p><b>Master Folder:</b> <span class="path">{html.escape(candidate['master_folder'])}</span></p>
<p><b>Secondary Folder:</b> <span class="path">{html.escape(candidate['secondary_folder'])}</span></p>
<p><b>Parent Folder:</b> {html.escape(candidate['parent_folder'])}</p>
<p><b>Recommendation:</b> {html.escape(candidate['recommendation'])}</p>
<section class="cards"><div class="card">Confidence<b class="{html.escape(candidate['confidence'])}">{html.escape(candidate['confidence'].title())}</b></div>
<div class="card">Secondary coverage<b>{html.escape(candidate['secondary_coverage_pct'])}%</b></div>
<div class="card">Matching files<b>{int(candidate['matching_files']):,}</b></div>
<div class="card">Matching size<b>{_size(int(candidate['matching_bytes']))}</b></div>
<div class="card">Unmatched Secondary<b>{int(candidate['unmatched_secondary_files']):,} files</b><span>{_size(int(candidate['unmatched_secondary_bytes']))}</span></div></section>
<details open><summary>File types</summary>{_file_type_visual(rows)}</details>
<details open><summary>Matching-file evidence</summary>{truncation_note}<input type="search" placeholder="Filter filenames or locations" oninput="filterRows(this, 'folder-files')"><div class="table-wrap"><table id="folder-files"><thead><tr><th>Filename</th><th>Size</th><th>Secondary copies</th><th>Master locations</th><th>Secondary locations</th><th>Status</th></tr></thead><tbody>{file_rows}</tbody></table></div></details>
<script>function filterRows(input,id){{const q=input.value.toLowerCase();document.querySelectorAll('#'+id+' tbody tr').forEach(row=>row.hidden=!row.textContent.toLowerCase().includes(q));}}</script>'''
        page = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(candidate_id)} folder candidate</title><style>{STYLE}</style></head><body>{page_body}</body></html>'
        (page_directory / page_name).write_text(page, encoding="utf-8")
        table_rows.append(
            f'''<tr><td><a href="{html.escape(page_directory.name)}/{html.escape(page_name)}">{html.escape(candidate['master_folder'])}</a></td>
<td>{html.escape(candidate['secondary_folder'])}</td><td>{html.escape(candidate['parent_folder'])}</td>
<td class="{html.escape(candidate['confidence'])}">{html.escape(candidate['confidence'].title())}</td>
<td>{int(candidate['matching_files']):,}</td><td>{_size(int(candidate['matching_bytes']))}</td>
<td>{html.escape(candidate['secondary_coverage_pct'])}%</td><td>{int(candidate['unmatched_secondary_files']):,}</td>
<td>{html.escape(candidate['recommendation'])}</td></tr>'''
        )
    section = _section(
        "Folder details",
        "Master is always the media server. Secondary is always the cold store. A High label means at least 80% candidate coverage; only 100% advances to full hash verification.",
        "folder-candidates",
        ["Master Folder", "Secondary Folder", "Parent Folder", "Confidence", "Matching files", "Matching size", "Secondary coverage", "Unmatched files", "Recommendation"],
        table_rows,
        opened=True,
    )
    return section, len(candidates)


def generate(
    summary_json: Path,
    files_csv: Path,
    relocated_csv: Path,
    folders_csv: Path,
    folder_details_csv: Path,
    output: Path,
) -> tuple[int, int, int]:
    """Write the HTML report and return displayed row counts."""
    summary = json.loads(summary_json.read_text(encoding="utf-8"))
    if summary.get("complete") is not True:
        raise ValueError("Comparison summary is not marked complete.")
    primary = summary["primary"]
    backup = summary["backup"]
    categories = summary["categories"]
    top = _top_file_rows(files_csv, {
        "same_path_different_size": 1000,
        "primary_only": 500,
        "backup_only": 500,
        "same_path_same_size": 100,
    })
    with relocated_csv.open(newline="", encoding="utf-8") as source:
        relocated = list(islice(csv.DictReader(source), 500))
    folder_section, displayed_folders = _folder_candidates(
        folders_csv, folder_details_csv, output
    )

    maximum_count = max((int(values["paths"]) for values in categories.values()), default=1)
    category_rows = []
    for status in LABELS:
        values = categories[status]
        width = int(values["paths"]) / maximum_count * 100 if maximum_count else 0
        category_rows.append(
            f'''<div class="bar-row"><span>{html.escape(LABELS[status])}</span>
<div class="bar-track"><div class="bar" style="width:{width:.2f}%;background:{COLORS[status]}"></div></div>
<b>{int(values['paths']):,} paths</b><span>{_size(int(values['primary_bytes']))} / {_size(int(values['backup_bytes']))}</span></div>'''
        )

    conflict_rows = [
        f'''<tr><td>{html.escape(row['comparison_path'])}</td>
<td>{_link(row['primary_uri'], row['primary_absolute_path'])}</td>
<td>{_size(int(row['primary_size_bytes']))}</td><td>{_time(row['primary_modified_ns'])}</td>
<td>{_link(row['backup_uri'], row['backup_absolute_path'])}</td>
<td>{_size(int(row['backup_size_bytes']))}</td><td>{_time(row['backup_modified_ns'])}</td></tr>'''
        for row in top["same_path_different_size"]
    ]
    primary_only_rows = [
        f'''<tr><td>{html.escape(row['comparison_path'])}</td>
<td>{_link(row['primary_uri'], row['primary_absolute_path'])}</td>
<td>{_size(int(row['primary_size_bytes']))}</td><td>{_time(row['primary_modified_ns'])}</td></tr>'''
        for row in top["primary_only"]
    ]
    backup_only_rows = [
        f'''<tr><td>{html.escape(row['comparison_path'])}</td>
<td>{_link(row['backup_uri'], row['backup_absolute_path'])}</td>
<td>{_size(int(row['backup_size_bytes']))}</td><td>{_time(row['backup_modified_ns'])}</td></tr>'''
        for row in top["backup_only"]
    ]
    same_rows = [
        f'''<tr><td>{html.escape(row['comparison_path'])}</td>
<td>{_size(int(row['primary_size_bytes']))}</td>
<td>{_link(row['primary_uri'], row['primary_absolute_path'])}</td>
<td>{_link(row['backup_uri'], row['backup_absolute_path'])}</td></tr>'''
        for row in top["same_path_same_size"]
    ]
    relocated_rows = [
        f'''<tr><td>{html.escape(row['file_name'])}</td><td>{_size(int(row['size_bytes']))}</td>
<td>{int(row['candidate_copies']):,}</td><td>{_size(int(row['candidate_bytes']))}</td>
<td><details><summary>{int(row['primary_count']):,} location(s)</summary>{_locations(row['primary_absolute_paths'])}</details></td>
<td><details><summary>{int(row['backup_count']):,} location(s)</summary>{_locations(row['backup_absolute_paths'])}</details></td></tr>'''
        for row in relocated
    ]

    primary_unmapped_files = int(primary["full_files"]) - int(primary["selected_files"])
    primary_unmapped_bytes = int(primary["full_bytes"]) - int(primary["selected_bytes"])
    backup_unmapped_files = int(backup["full_files"]) - int(backup["selected_files"])
    backup_unmapped_bytes = int(backup["full_bytes"]) - int(backup["selected_bytes"])
    prefix_details = f'''<details open><summary>Compared roots and exclusions</summary>
<table><tbody>
<tr><th>Role</th><th>Label</th><th>Inventory prefix</th><th>Excluded prefixes</th><th>Outside comparison</th></tr>
<tr><td>Primary</td><td>{html.escape(str(primary['label']))}</td><td>{html.escape(str(primary['prefix']) or '(inventory root)')}</td><td>{html.escape(', '.join(primary.get('excluded_prefixes', [])) or 'None')}</td><td>{primary_unmapped_files:,} files / {_size(primary_unmapped_bytes)}</td></tr>
<tr><td>Backup</td><td>{html.escape(str(backup['label']))}</td><td>{html.escape(str(backup['prefix']) or '(inventory root)')}</td><td>{html.escape(', '.join(backup.get('excluded_prefixes', [])) or 'None')}</td><td>{backup_unmapped_files:,} files / {_size(backup_unmapped_bytes)}</td></tr>
</tbody></table></details>'''

    sections = "".join([
        _section(
            "Same path, different size",
            "Conflicts requiring review. The report never chooses a winner from timestamps alone.",
            "conflicts",
            ["Comparison path", "Primary location", "Primary size", "Primary modified", "Backup location", "Backup size", "Backup modified"],
            conflict_rows,
            opened=True,
        ),
        _section(
            "Primary-only files",
            "Largest copy candidates shown; the complete evidence remains in cross-drive-files.csv.",
            "primary-only",
            ["Comparison path", "Primary location", "Size", "Modified"],
            primary_only_rows,
            opened=True,
        ),
        _section(
            "Backup-only files",
            "Largest recovery or relocation candidates shown. These must not be deleted automatically.",
            "backup-only",
            ["Comparison path", "Backup location", "Size", "Modified"],
            backup_only_rows,
            opened=True,
        ),
        _section(
            "Same filename and size at different paths",
            "Top candidate groups. Names and sizes are evidence; selected hashes provide content proof.",
            "relocated",
            ["Filename", "File size", "Candidate copies", "Candidate bytes", "Primary locations", "Backup locations"],
            relocated_rows,
        ),
        _section(
            "Same path and size",
            "Largest already-present candidates shown. Hashes are needed before using either copy as deletion proof.",
            "same",
            ["Comparison path", "Size", "Primary location", "Backup location"],
            same_rows,
        ),
    ])

    definitions = '''<details open><summary>How to read this report</summary><ul>
<li><b>Same path and size:</b> both inventories contain the path with the same byte count.</li>
<li><b>Same path, different size:</b> both contain the path but their byte counts conflict.</li>
<li><b>Primary only:</b> a possible copy from Primary to Backup.</li>
<li><b>Backup only:</b> a possible recovery or relocated file; never an automatic deletion.</li>
<li><b>Different-path candidate:</b> normalized filenames and sizes match at different paths; hashes are still required for content proof.</li>
</ul><p class="muted">This is read-only inventory evidence. It does not run rsync, overwrite files, or calculate safe deletion totals.</p></details>'''
    body = f'''<h1>File Tidy cross-drive comparison</h1>
<p class="muted">{html.escape(str(primary['label']))} compared with {html.escape(str(backup['label']))}. Generated entirely from local inventory files.</p>
<section class="cards">
<div class="card">Primary full inventory<b>{int(primary['full_files']):,} files</b><span>{_size(int(primary['full_bytes']))}</span></div>
<div class="card">Primary selected<b>{int(primary['selected_files']):,} files</b><span>{_size(int(primary['selected_bytes']))}</span></div>
<div class="card">Backup full inventory<b>{int(backup['full_files']):,} files</b><span>{_size(int(backup['full_bytes']))}</span></div>
<div class="card">Backup selected<b>{int(backup['selected_files']):,} files</b><span>{_size(int(backup['selected_bytes']))}</span></div>
<div class="card">Same path and size<b>{int(categories['same_path_same_size']['paths']):,}</b></div>
<div class="card">Path conflicts<b>{int(categories['same_path_different_size']['paths']):,}</b></div>
<div class="card">Primary only<b>{int(categories['primary_only']['paths']):,}</b></div>
<div class="card">Backup only<b>{int(categories['backup_only']['paths']):,}</b></div>
<div class="card">High-confidence folders<b>{int(summary['folder_candidates']['high_confidence']):,}</b><span>{int(summary['folder_candidates']['complete_coverage']):,} at 100% candidate coverage</span></div>
<div class="card">Different-path candidates<b>{int(summary['relocated_candidate_groups']):,}</b><span>{_size(int(summary['relocated_candidate_bytes']))}</span></div>
</section>{folder_section}{definitions}{prefix_details}
<details open><summary>Comparison categories</summary><p class="muted">Path count; Primary bytes / Backup bytes.</p>{''.join(category_rows)}</details>
{sections}
<script>function filterRows(input,id){{const q=input.value.toLowerCase();document.querySelectorAll('#'+id+' tbody tr').forEach(row=>row.hidden=!row.textContent.toLowerCase().includes(q));}}</script>'''
    document = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>File Tidy cross-drive comparison</title><style>{STYLE}</style></head><body>{body}</body></html>'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return sum(len(rows) for rows in top.values()), len(relocated), displayed_folders


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument(
        "--files", type=Path, default=Path("scan-results/cross-drive-files.csv")
    )
    parser.add_argument(
        "--relocated", type=Path,
        default=Path("scan-results/cross-drive-relocated.csv"),
    )
    parser.add_argument(
        "--folders", type=Path,
        default=Path("scan-results/cross-drive-folders.csv"),
    )
    parser.add_argument(
        "--folder-details", type=Path,
        default=Path("scan-results/cross-drive-folder-files.csv"),
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("scan-results/cross-drive-comparison.html"),
    )
    args = parser.parse_args()
    files, relocated, folders = generate(
        args.summary,
        args.files,
        args.relocated,
        args.folders,
        args.folder_details,
        args.output,
    )
    print(
        f"Wrote {args.output} with {files:,} file rows and "
        f"{relocated:,} relocated-candidate rows and {folders:,} folder candidates"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
