"""Generate an interactive HTML report from a read-only Backup Sync plan."""

from __future__ import annotations

import argparse
import csv
import heapq
import html
import json
from itertools import count
from pathlib import Path, PurePosixPath


STYLE = '''body{font:15px system-ui,sans-serif;max-width:1550px;margin:1.5rem auto;padding:0 1rem;color:#17202a}h1{margin-bottom:.2rem}.muted{color:#617384}.notice{background:#fff4ce;border-left:5px solid #b7791f;padding:.8rem 1rem;margin:1rem 0}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:.8rem;margin:1rem 0}.card{background:#eef3f7;border-radius:.6rem;padding:1rem}.card b{display:block;font-size:1.35rem;margin-top:.3rem}.toolbar{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin:.7rem 0}.toolbar button{border:1px solid #9bacbb;background:white;border-radius:.35rem;padding:.45rem .7rem;cursor:pointer}.toolbar button.active{background:#145dcc;color:white;border-color:#145dcc}input{box-sizing:border-box;min-width:18rem;flex:1;padding:.5rem}.table-wrap{max-height:42rem;overflow:auto;border:1px solid #d9e1e8;border-radius:.4rem}table{border-collapse:collapse;width:100%;font-size:.84rem}th,td{padding:.45rem;border-bottom:1px solid #d9e1e8;text-align:left;vertical-align:top}thead th{position:sticky;top:0;background:white;z-index:1}.path{word-break:break-word;max-width:34rem}a{color:#145dcc}.status{font-weight:650;white-space:nowrap}.backed_up{color:#18783b}.needs_sync{color:#145dcc}.needs_sync_and_review{color:#9a6500}.review_required{color:#a43b32}.coverage{min-width:8rem}.track{height:.7rem;background:#e5e9ed;border-radius:.3rem;overflow:hidden;margin-top:.25rem}.fill{height:100%;background:#2f855a}details{margin:1rem 0}summary{cursor:pointer;font-weight:650}nav{margin-bottom:1rem}@media(max-width:850px){.table-wrap{max-height:none}.cards{grid-template-columns:1fr 1fr}}'''


STATUS_LABELS = {
    "backed_up": "Backed up",
    "needs_sync": "Needs sync",
    "needs_sync_and_review": "Needs sync and review",
    "review_required": "Review required",
}


def _size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(amount) < 1000 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount):,} B"
        amount /= 1000
    return f"{value:,} B"


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


def _table(headers: list[str], rows: list[str], table_id: str) -> str:
    body = "".join(rows) or (
        f'<tr><td colspan="{len(headers)}" class="muted">No files in this category.</td></tr>'
    )
    return f'''<input type="search" placeholder="Filter this table" oninput="filterRows(this,'{table_id}')">
<div class="table-wrap"><table id="{table_id}"><thead><tr>{''.join(f'<th>{html.escape(header)}</th>' for header in headers)}</tr></thead><tbody>{body}</tbody></table></div>'''


def _detail_page(
    output: Path,
    page_directory: Path,
    folder: dict[str, str],
    samples: dict[tuple[str, str], list[dict[str, str]]],
    notice: str,
) -> None:
    key = folder["comparison_folder"]
    copy_rows = [
        f'''<tr><td>{html.escape(row['comparison_path'])}</td><td>{_size(int(row['size_bytes']))}</td><td>{_path(row['primary_path'])}</td><td>{_path(row['backup_target_path'], linked=False)}</td></tr>'''
        for row in samples.get((key, "copy"), [])
    ]
    relocated_rows = [
        f'''<tr><td>{html.escape(row['comparison_path'])}</td><td>{_size(int(row['primary_size_bytes']))}</td><td>{_path(row['primary_path'])}</td><td class="path">{html.escape(row['candidate_backup_paths'])}</td></tr>'''
        for row in samples.get((key, "relocated"), [])
    ]
    conflict_rows = [
        f'''<tr><td>{html.escape(row['comparison_path'])}</td><td>{_path(row['primary_path'])}</td><td>{_size(int(row['primary_size_bytes']))}</td><td>{_path(row['backup_path'])}</td><td>{_size(int(row['backup_size_bytes']))}</td></tr>'''
        for row in samples.get((key, "conflict"), [])
    ]
    backup_only_rows = [
        f'''<tr><td>{html.escape(row['comparison_path'])}</td><td>{_size(int(row['backup_size_bytes']))}</td><td>{_path(row['backup_path'])}</td></tr>'''
        for row in samples.get((key, "backup_only"), [])
    ]
    backed_rows = [
        f'''<tr><td>{html.escape(row['comparison_path'])}</td><td>{_size(int(row['primary_size_bytes']))}</td><td>{_path(row['primary_absolute_path'])}</td><td>{_path(row['backup_absolute_path'])}</td></tr>'''
        for row in samples.get((key, "backed_up"), [])
    ]
    body = f'''<nav><a href="../{html.escape(output.name)}">← Backup Sync report</a></nav>
<h1>{html.escape(key)} backup details</h1><div class="notice">{html.escape(notice)}</div>
<p><b>Primary:</b> {_path(folder['primary_folder'])}</p><p><b>Backup:</b> {_path(folder['backup_folder'], linked=False)}</p>
<section class="cards"><div class="card">Primary total<b>{int(folder['primary_files']):,} files</b><span>{_size(int(folder['primary_bytes']))}</span></div><div class="card">Backed up<b>{folder['byte_coverage_pct']}%</b><span>{int(folder['backed_up_files']):,} files / {_size(int(folder['backed_up_bytes']))}</span></div><div class="card">Ready to copy<b>{int(folder['copy_candidate_files']):,} files</b><span>{_size(int(folder['copy_candidate_bytes']))}</span></div><div class="card">Relocated review<b>{int(folder['relocated_candidate_files']):,} files</b><span>{_size(int(folder['relocated_candidate_bytes']))}</span></div><div class="card">Conflicts<b>{int(folder['conflict_files']):,}</b><span>{_size(int(folder['conflict_bytes']))}</span></div></section>
<details open><summary>Ready to copy</summary><p class="muted">Files absent from the expected Backup path and without a known relocated candidate. Showing the largest bounded sample.</p>{_table(['Relative path','Size','Primary','Proposed Backup target'],copy_rows,'copy')}</details>
<details open><summary>Possible relocated matches</summary><p class="muted">Hold these files for content verification and folder review instead of copying another copy.</p>{_table(['Relative path','Size','Primary','Candidate Backup locations'],relocated_rows,'relocated')}</details>
<details open><summary>Different-size conflicts</summary>{_table(['Relative path','Primary','Primary size','Backup','Backup size'],conflict_rows,'conflicts')}</details>
<details><summary>Backup-only recovery review</summary>{_table(['Relative path','Size','Backup location'],backup_only_rows,'backup-only')}</details>
<details><summary>Already backed up</summary><p class="muted">Same expected path and byte size. Showing the largest bounded sample.</p>{_table(['Relative path','Size','Primary','Backup'],backed_rows,'backed')}</details>
<script>function filterRows(input,id){{const q=input.value.toLowerCase();document.querySelectorAll('#'+id+' tbody tr').forEach(row=>row.hidden=!row.textContent.toLowerCase().includes(q));}}</script>'''
    document = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(key)} backup details</title><style>{STYLE}</style></head><body>{body}</body></html>'
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
    comparison_files = comparison_files or _resolve(
        summary_path, summary["comparison_files"]
    )
    gaps = _read_csv(gaps_csv)
    copies = _read_csv(copy_csv)
    review = _read_csv(review_csv)
    selected = _folder_selection(gaps, folders_per_status)
    selected_keys = {row["comparison_folder"] for row in selected}
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
            f'''<tr data-status="{html.escape(status)}"><td><a class="path" href="{html.escape(page_directory.name)}/{html.escape(page)}">{html.escape(row['primary_folder'])}</a></td><td class="path">{html.escape(row['backup_folder'])}</td><td class="status {html.escape(status)}">{html.escape(STATUS_LABELS.get(status,status))}</td><td class="coverage"><b>{coverage:.1f}%</b><div class="track"><div class="fill" style="width:{coverage:.1f}%"></div></div></td><td>{int(row['backed_up_files']):,}<br><span class="muted">{_size(int(row['backed_up_bytes']))}</span></td><td>{int(row['copy_candidate_files']):,}<br><span class="muted">{_size(int(row['copy_candidate_bytes']))}</span></td><td>{int(row['relocated_candidate_files']):,}<br><span class="muted">{_size(int(row['relocated_candidate_bytes']))}</span></td><td>{int(row['conflict_files']):,}</td><td>{html.escape(row['approval_status'])}</td></tr>'''
        )

    coverage = summary["coverage"]
    primary = summary["primary"]
    backup = summary["backup"]
    definitions = '''<details open><summary>How to use this report</summary><ul><li><b>Backed up:</b> the same expected path and byte size exists on both drives.</li><li><b>Ready to copy:</b> the Primary path is absent from the Backup and no same-name-and-size candidate is known elsewhere.</li><li><b>Relocated review:</b> a possible copy exists elsewhere on the Backup; verify it before copying or reorganizing.</li><li><b>Conflict:</b> both drives contain the expected path with different byte sizes.</li><li><b>Backup only:</b> preserve for recovery review; never delete automatically.</li></ul><p class="muted">Folder rows contain recursive totals and overlap with their parents. File-level copy candidates are unique. This report cannot copy, overwrite, or delete files.</p></details>'''
    body = f'''<h1>File Tidy Backup Sync</h1><p class="muted">{html.escape(primary['label'])} is the Primary; {html.escape(backup['label'])} is the Backup.</p><div class="notice">{html.escape(notice)}</div>{definitions}
<section class="cards"><div class="card">Primary selected<b>{int(primary['files']):,} files</b><span>{_size(int(primary['bytes']))}</span></div><div class="card">Backed up at expected path<b>{coverage['byte_coverage_pct']}%</b><span>{int(coverage['backed_up_files']):,} files / {_size(int(coverage['backed_up_bytes']))}</span></div><div class="card">Ready to copy<b>{int(coverage['copy_candidate_files']):,} files</b><span>{_size(int(coverage['copy_candidate_bytes']))}</span></div><div class="card">Relocated review<b>{int(coverage['relocated_candidate_files']):,} files</b><span>{_size(int(coverage['relocated_candidate_bytes']))}</span></div><div class="card">Path conflicts<b>{int(coverage['conflict_files']):,}</b><span>{_size(int(coverage['conflict_bytes']))}</span></div><div class="card">Backup-only review<b>{int(coverage['backup_only_files']):,} files</b><span>{_size(int(coverage['backup_only_bytes']))}</span></div></section>
<details open><summary>Folder Details — Backup Gaps</summary><p class="muted">Ranked by unambiguous copy-candidate bytes. Open a Primary folder to inspect the bounded file evidence.</p><div class="toolbar"><button class="active" data-filter="all" onclick="setFolderStatus('all',this)">All</button><button data-filter="needs_sync" onclick="setFolderStatus('needs_sync',this)">Needs sync</button><button data-filter="needs_sync_and_review" onclick="setFolderStatus('needs_sync_and_review',this)">Sync and review</button><button data-filter="review_required" onclick="setFolderStatus('review_required',this)">Review</button><button data-filter="backed_up" onclick="setFolderStatus('backed_up',this)">Backed up</button><input id="folder-search" type="search" placeholder="Filter folders" oninput="filterFolders()"></div><div class="table-wrap"><table id="folder-gaps"><thead><tr><th>Primary folder</th><th>Backup folder</th><th>Status</th><th>Byte coverage</th><th>Backed up</th><th>Ready to copy</th><th>Relocated review</th><th>Conflicts</th><th>Decision</th></tr></thead><tbody>{''.join(folder_rows)}</tbody></table></div><p class="muted">Showing {len(selected):,} ranked folder rows from {len(gaps):,}; complete data remains in {html.escape(str(gaps_csv))}.</p></details>
<details><summary>Plan roots and local evidence</summary><table><tbody><tr><th>Primary selected root</th><td class="path">{html.escape(primary['selected_root'])}</td></tr><tr><th>Backup selected root</th><td class="path">{html.escape(backup['selected_root'])}</td></tr><tr><th>Copy candidates CSV</th><td class="path">{html.escape(str(copy_csv))}</td></tr><tr><th>Review CSV</th><td class="path">{html.escape(str(review_csv))}</td></tr></tbody></table></details>
<script>let folderStatus='all';function setFolderStatus(status,button){{folderStatus=status;document.querySelectorAll('[data-filter]').forEach(item=>item.classList.remove('active'));button.classList.add('active');filterFolders();}}function filterFolders(){{const q=document.getElementById('folder-search').value.toLowerCase();document.querySelectorAll('#folder-gaps tbody tr').forEach(row=>{{const statusOk=folderStatus==='all'||row.dataset.status===folderStatus;const textOk=row.textContent.toLowerCase().includes(q);row.hidden=!(statusOk&&textOk);}});}}</script>'''
    document = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>File Tidy Backup Sync</title><style>{STYLE}</style></head><body>{body}</body></html>'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return len(selected), len(list(page_directory.glob("*.html")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("--gaps", type=Path)
    parser.add_argument("--copy-candidates", type=Path)
    parser.add_argument("--review", type=Path)
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
        comparison_files=args.comparison_files,
        notice=args.notice,
    )
    print(f"Wrote {args.output} with {displayed:,} folder rows and {pages:,} drill-down pages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
