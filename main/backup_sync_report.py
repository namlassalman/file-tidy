"""Generate an interactive HTML report from a read-only Backup Sync plan."""

from __future__ import annotations

import argparse
import csv
import heapq
import html
import json
from datetime import datetime, timezone
from itertools import count
from pathlib import Path, PurePosixPath


STYLE = '''body{font:15px system-ui,sans-serif;max-width:1550px;margin:1.5rem auto;padding:0 1rem;color:#17202a}h1{margin-bottom:.2rem}.muted{color:#617384}.notice{background:#fff4ce;border-left:5px solid #b7791f;padding:.8rem 1rem;margin:1rem 0}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:.8rem;margin:1rem 0}.card{background:#eef3f7;border-radius:.6rem;padding:1rem}.card b{display:block;font-size:1.35rem;margin-top:.3rem}.toolbar{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin:.7rem 0}.toolbar button{border:1px solid #9bacbb;background:white;border-radius:.35rem;padding:.45rem .7rem;cursor:pointer}.toolbar button.active{background:#145dcc;color:white;border-color:#145dcc}input{box-sizing:border-box;min-width:18rem;flex:1;padding:.5rem}.table-wrap{max-height:42rem;overflow:auto;border:1px solid #d9e1e8;border-radius:.4rem}table{border-collapse:collapse;width:100%;font-size:.84rem}th,td{padding:.45rem;border-bottom:1px solid #d9e1e8;text-align:left;vertical-align:top}thead th{position:sticky;top:0;background:white;z-index:1}.sort-button{border:0;background:transparent;color:inherit;font:inherit;font-weight:650;padding:0;cursor:pointer;white-space:nowrap}.sort-arrow{display:inline-block;min-width:1.1rem;color:#617384}.path{word-break:break-word;max-width:34rem}a{color:#145dcc}.status{font-weight:650;white-space:nowrap}.backed_up{color:#18783b}.needs_sync{color:#145dcc}.needs_sync_and_review,.recovery_and_relocation_review{color:#9a6500}.review_required,.recovery_review{color:#a43b32}.relocation_review{color:#805ad5}.coverage{min-width:8rem}.track{height:.7rem;background:#e5e9ed;border-radius:.3rem;overflow:hidden;margin-top:.25rem}.fill{height:100%;background:#2f855a}details{margin:1rem 0}summary{cursor:pointer;font-weight:650}nav{margin-bottom:1rem}@media(max-width:850px){.table-wrap{max-height:none}.cards{grid-template-columns:1fr 1fr}}'''


SORT_SCRIPT = '''function sortTable(button,tableId,column,type){const table=document.getElementById(tableId);const body=table.tBodies[0];const headers=table.querySelectorAll('.sort-button');const next=button.dataset.direction==='asc'?'desc':'asc';headers.forEach(item=>{item.dataset.direction='';item.setAttribute('aria-sort','none');item.querySelector('.sort-arrow').textContent='↕';});button.dataset.direction=next;button.setAttribute('aria-sort',next==='asc'?'ascending':'descending');button.querySelector('.sort-arrow').textContent=next==='asc'?'↑':'↓';const rows=Array.from(body.rows);rows.sort((left,right)=>{const a=left.cells[column]?.dataset.sort??left.cells[column]?.textContent.trim()??'';const b=right.cells[column]?.dataset.sort??right.cells[column]?.textContent.trim()??'';let value;if(type==='number'||type==='status'){value=Number(a)-Number(b);}else{value=a.localeCompare(b,undefined,{numeric:true,sensitivity:'base'});}return next==='asc'?value:-value;});rows.forEach(row=>body.appendChild(row));}function filterRows(input,id){const q=input.value.toLowerCase();document.querySelectorAll('#'+id+' tbody tr').forEach(row=>row.hidden=!row.textContent.toLowerCase().includes(q));}'''


STATUS_LABELS = {
    "backed_up": "Backed up",
    "needs_sync": "Needs sync",
    "needs_sync_and_review": "Needs sync and review",
    "review_required": "Review required",
}

STATUS_SORT = {
    "needs_sync_and_review": 0,
    "needs_sync": 1,
    "review_required": 2,
    "backed_up": 3,
}

RECOVERY_STATUS_LABELS = {
    "recovery_and_relocation_review": "Recovery and relocation review",
    "recovery_review": "Recovery review",
    "relocation_review": "Relocation review",
}

RECOVERY_STATUS_SORT = {
    "recovery_and_relocation_review": 0,
    "recovery_review": 1,
    "relocation_review": 2,
}


def _size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(amount) < 1000 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount):,} B"
        amount /= 1000
    return f"{value:,} B"


def _time(value: str) -> str:
    try:
        nanoseconds = int(value)
    except (TypeError, ValueError):
        return "Unknown"
    if not nanoseconds:
        return "Unknown"
    return datetime.fromtimestamp(nanoseconds / 1_000_000_000, timezone.utc).strftime(
        "%Y-%m-%d"
    )


def _resolve(summary_path: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute() or path.exists():
        return path
    candidate = summary_path.parent / path.name
    return candidate if candidate.exists() else path


def _load_summary(path: Path) -> dict[str, object]:
    summary = json.loads(path.read_text(encoding="utf-8"))
    if summary.get("complete") is not True or summary.get("mode") != "backup_sync":
        raise ValueError("Backup Sync summary is not complete or has the wrong mode.")
    if summary.get("read_only") is not True:
        raise ValueError("Backup Sync summary is not marked read-only.")
    return summary


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as source:
            return list(csv.DictReader(source))
    except FileNotFoundError:
        raise ValueError(f"Report input does not exist: {path}") from None


def _ancestors(comparison_path: str) -> list[str]:
    parts = tuple(
        part for part in PurePosixPath(comparison_path).parts if part not in {"", "."}
    )[:-1]
    return ["."] + ["/".join(parts[:depth]) for depth in range(1, len(parts) + 1)]


def _folder_selection(
    rows: list[dict[str, str]], per_status: int
) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    seen: set[str] = set()
    root = next((row for row in rows if row["comparison_folder"] == "."), None)
    if root:
        selected.append(root)
        seen.add(root["folder_id"])
    for status in (
        "needs_sync_and_review",
        "needs_sync",
        "review_required",
        "backed_up",
    ):
        matches = [row for row in rows if row["status"] == status]
        if status == "backed_up":
            matches.sort(key=lambda row: int(row["primary_bytes"]), reverse=True)
        for row in matches[:per_status]:
            if row["folder_id"] not in seen:
                selected.append(row)
                seen.add(row["folder_id"])
    selected.sort(
        key=lambda row: (
            int(row["copy_candidate_bytes"]),
            int(row["relocated_candidate_bytes"]),
            int(row["conflict_files"]),
        ),
        reverse=True,
    )
    return selected


def _recovery_selection(
    rows: list[dict[str, str]], per_status: int
) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    seen: set[str] = set()
    root = next((row for row in rows if row["comparison_folder"] == "."), None)
    if root:
        selected.append(root)
        seen.add(root["folder_id"])
    for status in (
        "recovery_and_relocation_review",
        "recovery_review",
        "relocation_review",
    ):
        for row in (item for item in rows if item["status"] == status):
            if row["folder_id"] in seen:
                continue
            selected.append(row)
            seen.add(row["folder_id"])
            if sum(item["status"] == status for item in selected) >= per_status:
                break
    selected.sort(key=lambda row: int(row["backup_only_bytes"]), reverse=True)
    return selected


def _add_sample(
    samples: dict[tuple[str, str], list[tuple[int, int, dict[str, str]]]],
    folder: str,
    category: str,
    size: int,
    row: dict[str, str],
    sequence,
    limit: int,
) -> None:
    heap = samples.setdefault((folder, category), [])
    item = (size, next(sequence), row)
    if len(heap) < limit:
        heapq.heappush(heap, item)
    else:
        heapq.heappushpop(heap, item)


def _collect_samples(
    selected_folders: set[str],
    copies: list[dict[str, str]],
    review: list[dict[str, str]],
    comparison_files: Path,
    limit: int,
) -> dict[tuple[str, str], list[dict[str, str]]]:
    samples: dict[tuple[str, str], list[tuple[int, int, dict[str, str]]]] = {}
    sequence = count()
    for row in copies:
        for folder in set(_ancestors(row["comparison_path"])) & selected_folders:
            _add_sample(
                samples, folder, "copy", int(row["size_bytes"]), row, sequence, limit
            )
    for row in review:
        category = {
            "review_conflict": "conflict",
            "review_backup_only": "backup_only",
            "review_relocated_candidate": "relocated",
        }[row["action"]]
        size = max(
            int(row["primary_size_bytes"] or 0),
            int(row["backup_size_bytes"] or 0),
        )
        for folder in set(_ancestors(row["comparison_path"])) & selected_folders:
            _add_sample(samples, folder, category, size, row, sequence, limit)
    with comparison_files.open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            if row["status"] != "same_path_same_size":
                continue
            for folder in set(_ancestors(row["comparison_path"])) & selected_folders:
                _add_sample(
                    samples,
                    folder,
                    "backed_up",
                    int(row["primary_size_bytes"] or 0),
                    row,
                    sequence,
                    limit,
                )
    return {
        key: [item[2] for item in sorted(heap, reverse=True)]
        for key, heap in samples.items()
    }


def _file_uri(path: str) -> str:
    if not path or not Path(path).is_absolute():
        return ""
    try:
        return Path(path).as_uri()
    except ValueError:
        return ""


def _path(value: str, *, linked: bool = True) -> str:
    label = html.escape(value)
    uri = _file_uri(value) if linked else ""
    if not uri:
        return f'<span class="path">{label}</span>'
    return f'<a class="path" href="{html.escape(uri, quote=True)}">{label}</a>'


def _sort_header(
    label: str,
    table_id: str,
    column: int,
    kind: str = "text",
    *,
    direction: str = "",
) -> str:
    arrow = {"asc": "↑", "desc": "↓"}.get(direction, "↕")
    aria = {"asc": "ascending", "desc": "descending"}.get(direction, "none")
    return (
        f'<button class="sort-button" type="button" data-direction="{direction}" '
        f'aria-sort="{aria}" onclick="sortTable(this,\'{table_id}\',{column},\'{kind}\')">'
        f'{html.escape(label)} <span class="sort-arrow">{arrow}</span></button>'
    )


def _table(
    headers: list[tuple[str, str]], rows: list[str], table_id: str
) -> str:
    body = "".join(rows) or (
        f'<tr><td colspan="{len(headers)}" class="muted">No files in this category.</td></tr>'
    )
    return f'''<input type="search" placeholder="Filter this table" oninput="filterRows(this,'{table_id}')">
<div class="table-wrap"><table id="{table_id}"><thead><tr>{''.join(f'<th>{_sort_header(label, table_id, index, kind)}</th>' for index, (label, kind) in enumerate(headers))}</tr></thead><tbody>{body}</tbody></table></div>'''


def _detail_page(
    output: Path,
    page_directory: Path,
    folder: dict[str, str],
    samples: dict[tuple[str, str], list[dict[str, str]]],
    notice: str,
) -> None:
    key = folder["comparison_folder"]
    copy_rows = [
        f'''<tr><td data-sort="{html.escape(row['comparison_path'], quote=True)}">{html.escape(row['comparison_path'])}</td><td data-sort="{int(row['size_bytes'])}">{_size(int(row['size_bytes']))}</td><td data-sort="{html.escape(row['primary_path'], quote=True)}">{_path(row['primary_path'])}</td><td data-sort="{html.escape(row['backup_target_path'], quote=True)}">{_path(row['backup_target_path'], linked=False)}</td></tr>'''
        for row in samples.get((key, "copy"), [])
    ]
    relocated_rows = [
        f'''<tr><td data-sort="{html.escape(row['comparison_path'], quote=True)}">{html.escape(row['comparison_path'])}</td><td data-sort="{int(row['primary_size_bytes'])}">{_size(int(row['primary_size_bytes']))}</td><td data-sort="{html.escape(row['primary_path'], quote=True)}">{_path(row['primary_path'])}</td><td class="path">{html.escape(row['candidate_backup_paths'])}</td></tr>'''
        for row in samples.get((key, "relocated"), [])
    ]
    conflict_rows = [
        f'''<tr><td data-sort="{html.escape(row['comparison_path'], quote=True)}">{html.escape(row['comparison_path'])}</td><td data-sort="{html.escape(row['primary_path'], quote=True)}">{_path(row['primary_path'])}</td><td data-sort="{int(row['primary_size_bytes'])}">{_size(int(row['primary_size_bytes']))}</td><td data-sort="{html.escape(row['backup_path'], quote=True)}">{_path(row['backup_path'])}</td><td data-sort="{int(row['backup_size_bytes'])}">{_size(int(row['backup_size_bytes']))}</td></tr>'''
        for row in samples.get((key, "conflict"), [])
    ]
    backup_only_rows = [
        f'''<tr><td data-sort="{html.escape(row['comparison_path'], quote=True)}">{html.escape(row['comparison_path'])}</td><td data-sort="{int(row['backup_size_bytes'])}">{_size(int(row['backup_size_bytes']))}</td><td data-sort="{html.escape(row['backup_path'], quote=True)}">{_path(row['backup_path'])}</td></tr>'''
        for row in samples.get((key, "backup_only"), [])
    ]
    backed_rows = [
        f'''<tr><td data-sort="{html.escape(row['comparison_path'], quote=True)}">{html.escape(row['comparison_path'])}</td><td data-sort="{int(row['primary_size_bytes'])}">{_size(int(row['primary_size_bytes']))}</td><td data-sort="{html.escape(row['primary_absolute_path'], quote=True)}">{_path(row['primary_absolute_path'])}</td><td data-sort="{html.escape(row['backup_absolute_path'], quote=True)}">{_path(row['backup_absolute_path'])}</td></tr>'''
        for row in samples.get((key, "backed_up"), [])
    ]
    body = f'''<nav><a href="../{html.escape(output.name)}">← Backup Sync report</a></nav>
<h1>{html.escape(key)} backup details</h1><div class="notice">{html.escape(notice)}</div>
<p><b>Primary:</b> {_path(folder['primary_folder'])}</p><p><b>Backup:</b> {_path(folder['backup_folder'], linked=False)}</p>
<section class="cards"><div class="card">Primary total<b>{int(folder['primary_files']):,} files</b><span>{_size(int(folder['primary_bytes']))}</span></div><div class="card">Backed up<b>{folder['byte_coverage_pct']}%</b><span>{int(folder['backed_up_files']):,} files / {_size(int(folder['backed_up_bytes']))}</span></div><div class="card">Ready to copy<b>{int(folder['copy_candidate_files']):,} files</b><span>{_size(int(folder['copy_candidate_bytes']))}</span></div><div class="card">Relocated review<b>{int(folder['relocated_candidate_files']):,} files</b><span>{_size(int(folder['relocated_candidate_bytes']))}</span></div><div class="card">Conflicts<b>{int(folder['conflict_files']):,}</b><span>{_size(int(folder['conflict_bytes']))}</span></div></section>
<details open><summary>Ready to copy</summary><p class="muted">Files absent from the expected Backup path and without a known relocated candidate. Showing the largest bounded sample.</p>{_table([('Relative path','text'),('Size','number'),('Primary','text'),('Proposed Backup target','text')],copy_rows,'copy')}</details>
<details open><summary>Possible relocated matches</summary><p class="muted">Hold these files for content verification and folder review instead of copying another copy.</p>{_table([('Relative path','text'),('Size','number'),('Primary','text'),('Candidate Backup locations','text')],relocated_rows,'relocated')}</details>
<details open><summary>Different-size conflicts</summary>{_table([('Relative path','text'),('Primary','text'),('Primary size','number'),('Backup','text'),('Backup size','number')],conflict_rows,'conflicts')}</details>
<details><summary>Backup-only recovery review</summary>{_table([('Relative path','text'),('Size','number'),('Backup location','text')],backup_only_rows,'backup-only')}</details>
<details><summary>Already backed up</summary><p class="muted">Same expected path and byte size. Showing the largest bounded sample.</p>{_table([('Relative path','text'),('Size','number'),('Primary','text'),('Backup','text')],backed_rows,'backed')}</details>
<script>{SORT_SCRIPT}</script>'''
    document = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(key)} backup details</title><style>{STYLE}</style></head><body>{body}</body></html>'
    (page_directory / f"{folder['folder_id'].casefold()}.html").write_text(
        document, encoding="utf-8"
    )


def _recovery_detail_page(
    output: Path,
    page_directory: Path,
    folder: dict[str, str],
    samples: dict[tuple[str, str], list[dict[str, str]]],
    notice: str,
) -> None:
    key = folder["comparison_folder"]
    evidence_rows = []
    for row in samples.get((key, "backup_only"), []):
        possible_primary = row.get("candidate_primary_paths", "")
        evidence_rows.append(
            f'''<tr><td data-sort="{html.escape(row['comparison_path'], quote=True)}">{html.escape(row['comparison_path'])}</td><td data-sort="{int(row['backup_size_bytes'])}">{_size(int(row['backup_size_bytes']))}</td><td data-sort="{html.escape(row['backup_path'], quote=True)}">{_path(row['backup_path'])}</td><td data-sort="{1 if possible_primary else 0}" class="path">{html.escape(possible_primary or 'No filename-and-size candidate')}</td><td data-sort="{html.escape(row['review_status'], quote=True)}">{html.escape(row['review_status'])}</td></tr>'''
        )
    body = f'''<nav><a href="../{html.escape(output.name)}">← Backup Sync report</a></nav>
<h1>{html.escape(key)} Cold Store-only details</h1><div class="notice">{html.escape(notice)}</div>
<p><b>Cold Store folder:</b> {_path(folder['backup_folder'])}</p>
<section class="cards"><div class="card">Cold Store-only total<b>{int(folder['backup_only_files']):,} files</b><span>{_size(int(folder['backup_only_bytes']))}</span></div><div class="card">Recovery review<b>{int(folder['recovery_review_files']):,} files</b><span>{_size(int(folder['recovery_review_bytes']))}</span></div><div class="card">Possible relocated matches<b>{int(folder['relocated_candidate_files']):,} files</b><span>{_size(int(folder['relocated_candidate_bytes']))}</span></div><div class="card">Latest recorded change<b>{_time(folder['latest_modified_ns'])}</b></div></section>
<details open><summary>Cold Store-only file evidence</summary><p class="muted">Review these files for restoration, intentional cold-only retention, or possible relocation. Showing the largest bounded sample.</p>{_table([('Relative path','text'),('Size','number'),('Cold Store location','text'),('Possible Primary locations','number'),('Review status','text')],evidence_rows,'recovery-files')}</details>
<p class="muted">No selection on this page authorizes deletion. Verify content and retention intent first.</p><script>{SORT_SCRIPT}</script>'''
    document = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(key)} Cold Store-only details</title><style>{STYLE}</style></head><body>{body}</body></html>'
    (page_directory / f"{folder['folder_id'].casefold()}.html").write_text(
        document, encoding="utf-8"
    )


def generate(
    summary_path: Path,
    output: Path,
    *,
    gaps_csv: Path | None = None,
    copy_csv: Path | None = None,
    review_csv: Path | None = None,
    backup_only_folders_csv: Path | None = None,
    comparison_files: Path | None = None,
    notice: str = "Planning data only. Confirm both inventories are current before approving any copy operation.",
    folders_per_status: int = 125,
    files_per_section: int = 100,
) -> tuple[int, int]:
    """Generate the Backup Sync index and bounded folder drill-down pages."""
    summary = _load_summary(summary_path)
    outputs = summary["outputs"]
    gaps_csv = gaps_csv or _resolve(summary_path, outputs["gaps"])
    copy_csv = copy_csv or _resolve(summary_path, outputs["copy_candidates"])
    review_csv = review_csv or _resolve(summary_path, outputs["review"])
    backup_only_folders_csv = backup_only_folders_csv or _resolve(
        summary_path, outputs["backup_only_folders"]
    )
    comparison_files = comparison_files or _resolve(
        summary_path, summary["comparison_files"]
    )
    gaps = _read_csv(gaps_csv)
    copies = _read_csv(copy_csv)
    review = _read_csv(review_csv)
    recovery_folders = _read_csv(backup_only_folders_csv)
    selected = _folder_selection(gaps, folders_per_status)
    recovery_selected = _recovery_selection(
        recovery_folders, folders_per_status
    )
    selected_keys = {
        row["comparison_folder"] for row in selected + recovery_selected
    }
    samples = _collect_samples(
        selected_keys, copies, review, comparison_files, files_per_section
    )

    page_directory = output.with_name(output.stem + "-folders")
    page_directory.mkdir(parents=True, exist_ok=True)
    for old_page in page_directory.glob("*.html"):
        old_page.unlink()
    folder_rows: list[str] = []
    for row in selected:
        _detail_page(output, page_directory, row, samples, notice)
        status = row["status"]
        coverage = float(row["byte_coverage_pct"])
        page = f"{row['folder_id'].casefold()}.html"
        folder_rows.append(
            f'''<tr data-status="{html.escape(status)}"><td data-sort="{html.escape(row['primary_folder'], quote=True)}"><a class="path" href="{html.escape(page_directory.name)}/{html.escape(page)}">{html.escape(row['primary_folder'])}</a></td><td class="path" data-sort="{html.escape(row['backup_folder'], quote=True)}">{html.escape(row['backup_folder'])}</td><td class="status {html.escape(status)}" data-sort="{STATUS_SORT.get(status, 99)}">{html.escape(STATUS_LABELS.get(status,status))}</td><td class="coverage" data-sort="{coverage}"><b>{coverage:.1f}%</b><div class="track"><div class="fill" style="width:{coverage:.1f}%"></div></div></td><td data-sort="{int(row['backed_up_bytes'])}">{int(row['backed_up_files']):,}<br><span class="muted">{_size(int(row['backed_up_bytes']))}</span></td><td data-sort="{int(row['copy_candidate_bytes'])}">{int(row['copy_candidate_files']):,}<br><span class="muted">{_size(int(row['copy_candidate_bytes']))}</span></td><td data-sort="{int(row['relocated_candidate_bytes'])}">{int(row['relocated_candidate_files']):,}<br><span class="muted">{_size(int(row['relocated_candidate_bytes']))}</span></td><td data-sort="{int(row['conflict_files'])}">{int(row['conflict_files']):,}</td><td data-sort="{html.escape(row['approval_status'], quote=True)}">{html.escape(row['approval_status'])}</td></tr>'''
        )

    recovery_rows: list[str] = []
    for row in recovery_selected:
        _recovery_detail_page(output, page_directory, row, samples, notice)
        status = row["status"]
        page = f"{row['folder_id'].casefold()}.html"
        recovery_rows.append(
            f'''<tr data-recovery-status="{html.escape(status)}"><td data-sort="{html.escape(row['backup_folder'], quote=True)}"><a class="path" href="{html.escape(page_directory.name)}/{html.escape(page)}">{html.escape(row['backup_folder'])}</a></td><td class="status {html.escape(status)}" data-sort="{RECOVERY_STATUS_SORT.get(status, 99)}">{html.escape(RECOVERY_STATUS_LABELS.get(status,status))}</td><td data-sort="{int(row['backup_only_bytes'])}">{int(row['backup_only_files']):,}<br><span class="muted">{_size(int(row['backup_only_bytes']))}</span></td><td data-sort="{int(row['recovery_review_bytes'])}">{int(row['recovery_review_files']):,}<br><span class="muted">{_size(int(row['recovery_review_bytes']))}</span></td><td data-sort="{int(row['relocated_candidate_bytes'])}">{int(row['relocated_candidate_files']):,}<br><span class="muted">{_size(int(row['relocated_candidate_bytes']))}</span></td><td data-sort="{int(row['latest_modified_ns'])}">{_time(row['latest_modified_ns'])}</td><td data-sort="{html.escape(row['review_status'], quote=True)}">{html.escape(row['review_status'])}</td></tr>'''
        )

    coverage = summary["coverage"]
    primary = summary["primary"]
    backup = summary["backup"]
    definitions = '''<details open><summary>How to use this report</summary><ul><li><b>Backed up:</b> the same expected path and byte size exists on both drives.</li><li><b>Ready to copy:</b> the Primary path is absent from the Backup and no same-name-and-size candidate is known elsewhere.</li><li><b>Relocated review:</b> a possible copy exists elsewhere on the Backup; verify it before copying or reorganizing.</li><li><b>Conflict:</b> both drives contain the expected path with different byte sizes.</li><li><b>Backup only:</b> preserve for recovery review; never delete automatically.</li></ul><p class="muted">Folder rows contain recursive totals and overlap with their parents. File-level copy candidates are unique. This report cannot copy, overwrite, or delete files.</p></details>'''
    body = f'''<h1>File Tidy Backup Sync</h1><p class="muted">{html.escape(primary['label'])} is the Primary; {html.escape(backup['label'])} is the Backup.</p><div class="notice">{html.escape(notice)}</div>{definitions}
<section class="cards"><div class="card">Primary selected<b>{int(primary['files']):,} files</b><span>{_size(int(primary['bytes']))}</span></div><div class="card">Backed up at expected path<b>{coverage['byte_coverage_pct']}%</b><span>{int(coverage['backed_up_files']):,} files / {_size(int(coverage['backed_up_bytes']))}</span></div><div class="card">Ready to copy<b>{int(coverage['copy_candidate_files']):,} files</b><span>{_size(int(coverage['copy_candidate_bytes']))}</span></div><div class="card">Relocated review<b>{int(coverage['relocated_candidate_files']):,} files</b><span>{_size(int(coverage['relocated_candidate_bytes']))}</span></div><div class="card">Path conflicts<b>{int(coverage['conflict_files']):,}</b><span>{_size(int(coverage['conflict_bytes']))}</span></div><div class="card">Backup-only review<b>{int(coverage['backup_only_files']):,} files</b><span>{_size(int(coverage['backup_only_bytes']))}</span></div></section>
<details open><summary>Folder Details — Backup Gaps</summary><p class="muted">Ranked by unambiguous copy-candidate bytes. Open a Primary folder to inspect the bounded file evidence. Combined count-and-size columns sort by bytes.</p><div class="toolbar"><button class="active" data-filter="all" onclick="setFolderStatus('all',this)">All</button><button data-filter="needs_sync" onclick="setFolderStatus('needs_sync',this)">Needs sync</button><button data-filter="needs_sync_and_review" onclick="setFolderStatus('needs_sync_and_review',this)">Sync and review</button><button data-filter="review_required" onclick="setFolderStatus('review_required',this)">Review</button><button data-filter="backed_up" onclick="setFolderStatus('backed_up',this)">Backed up</button><input id="folder-search" type="search" placeholder="Filter folders" oninput="filterFolders()"></div><div class="table-wrap"><table id="folder-gaps"><thead><tr><th>{_sort_header('Primary folder','folder-gaps',0)}</th><th>{_sort_header('Backup folder','folder-gaps',1)}</th><th>{_sort_header('Status','folder-gaps',2,'status')}</th><th>{_sort_header('Byte coverage','folder-gaps',3,'number')}</th><th>{_sort_header('Backed up','folder-gaps',4,'number')}</th><th>{_sort_header('Ready to copy','folder-gaps',5,'number',direction='desc')}</th><th>{_sort_header('Relocated review','folder-gaps',6,'number')}</th><th>{_sort_header('Conflicts','folder-gaps',7,'number')}</th><th>{_sort_header('Decision','folder-gaps',8)}</th></tr></thead><tbody>{''.join(folder_rows)}</tbody></table></div><p class="muted">Showing {len(selected):,} ranked folder rows from {len(gaps):,}; complete data remains in {html.escape(str(gaps_csv))}.</p></details>
<details open><summary>Cold Store-only — Recovery Review</summary><p class="muted">Folders present only on the Backup, ranked by total size. Review them for restoration, intentional cold-only retention, or possible relocation. Recursive rows overlap with their parents.</p><div class="toolbar"><button class="active" data-recovery-filter="all" onclick="setRecoveryStatus('all',this)">All</button><button data-recovery-filter="recovery_review" onclick="setRecoveryStatus('recovery_review',this)">Recovery</button><button data-recovery-filter="relocation_review" onclick="setRecoveryStatus('relocation_review',this)">Relocation</button><button data-recovery-filter="recovery_and_relocation_review" onclick="setRecoveryStatus('recovery_and_relocation_review',this)">Mixed</button><input id="recovery-search" type="search" placeholder="Filter Cold Store folders" oninput="filterRecovery()"></div><div class="table-wrap"><table id="recovery-folders"><thead><tr><th>{_sort_header('Cold Store folder','recovery-folders',0)}</th><th>{_sort_header('Status','recovery-folders',1,'status')}</th><th>{_sort_header('Cold Store only','recovery-folders',2,'number',direction='desc')}</th><th>{_sort_header('Recovery review','recovery-folders',3,'number')}</th><th>{_sort_header('Possible relocated','recovery-folders',4,'number')}</th><th>{_sort_header('Latest modified','recovery-folders',5,'number')}</th><th>{_sort_header('Decision','recovery-folders',6)}</th></tr></thead><tbody>{''.join(recovery_rows)}</tbody></table></div><p class="muted">Showing {len(recovery_selected):,} ranked folder rows from {len(recovery_folders):,}; complete data remains in {html.escape(str(backup_only_folders_csv))}.</p></details>
<details><summary>Plan roots and local evidence</summary><table><tbody><tr><th>Primary selected root</th><td class="path">{html.escape(primary['selected_root'])}</td></tr><tr><th>Backup selected root</th><td class="path">{html.escape(backup['selected_root'])}</td></tr><tr><th>Copy candidates CSV</th><td class="path">{html.escape(str(copy_csv))}</td></tr><tr><th>Review CSV</th><td class="path">{html.escape(str(review_csv))}</td></tr></tbody></table></details>
<script>{SORT_SCRIPT}let folderStatus='all';let recoveryStatus='all';function setFolderStatus(status,button){{folderStatus=status;document.querySelectorAll('[data-filter]').forEach(item=>item.classList.remove('active'));button.classList.add('active');filterFolders();}}function filterFolders(){{const q=document.getElementById('folder-search').value.toLowerCase();document.querySelectorAll('#folder-gaps tbody tr').forEach(row=>{{const statusOk=folderStatus==='all'||row.dataset.status===folderStatus;const textOk=row.textContent.toLowerCase().includes(q);row.hidden=!(statusOk&&textOk);}});}}function setRecoveryStatus(status,button){{recoveryStatus=status;document.querySelectorAll('[data-recovery-filter]').forEach(item=>item.classList.remove('active'));button.classList.add('active');filterRecovery();}}function filterRecovery(){{const q=document.getElementById('recovery-search').value.toLowerCase();document.querySelectorAll('#recovery-folders tbody tr').forEach(row=>{{const statusOk=recoveryStatus==='all'||row.dataset.recoveryStatus===recoveryStatus;const textOk=row.textContent.toLowerCase().includes(q);row.hidden=!(statusOk&&textOk);}});}}</script>'''
    document = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>File Tidy Backup Sync</title><style>{STYLE}</style></head><body>{body}</body></html>'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return len(selected) + len(recovery_selected), len(
        list(page_directory.glob("*.html"))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("--gaps", type=Path)
    parser.add_argument("--copy-candidates", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--backup-only-folders", type=Path)
    parser.add_argument("--comparison-files", type=Path)
    parser.add_argument("--notice", default=(
        "Planning data only. Confirm both inventories are current before approving any copy operation."
    ))
    parser.add_argument(
        "--output", type=Path, default=Path("scan-results/backup-sync.html")
    )
    args = parser.parse_args()
    displayed, pages = generate(
        args.summary,
        args.output,
        gaps_csv=args.gaps,
        copy_csv=args.copy_candidates,
        review_csv=args.review,
        backup_only_folders_csv=args.backup_only_folders,
        comparison_files=args.comparison_files,
        notice=args.notice,
    )
    print(f"Wrote {args.output} with {displayed:,} folder rows and {pages:,} drill-down pages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
