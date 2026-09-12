# Development

This is a small prototype intended for a few hours of development. The README defines the product scope; use the checklist below to track the first implementation.

## Local setup

Start with Python 3.11 or later. Create an isolated environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

`main/config.py` loads local settings using the Python standard library, `main/scanner.py` provides the first read-only inventory implementation, and `main/report.py` generates an interactive HTML summary. Add dependencies only when an implementation requires them.

The first version must support both local folders and accessible NAS shares. Copy `config.example.json` to `config.local.json`, set `source_type` to `local` or `nas`, and fill in the matching source settings. For a local source, set `source_path` to the folder to scan. For an NAS source, fill in the host, username, share, and `nas_mount_path` for the already mounted share. `config.local.json` is ignored by Git and must stay on your machine. Folder entries are relative to the selected source. Exclusions are case-insensitive; a bare entry such as `WBEM` skips every folder with that name and all descendants, while a path such as `System/Cache` skips that path and its descendants. Keep passwords in the system credential manager or an interactive authentication prompt.

Application code should obtain these values through `from main.config import load_config` and `settings = load_config()`, rather than hardcoding connection details or personal folder names. Do not print settings into logs or commit real scan output. The public example must contain placeholders only.

Run tests with `python -m unittest discover -s test`.

## First working version

- [x] Let the user select a local folder or accessible NAS share and specify exclusions before scanning.
- [x] Inventory file paths, types, sizes, modification times, and folder depths without changing files; report access errors and progress.
- [x] Show file counts and total size by type in one chart, and highlight deeply nested folders.
- [x] Compare two separately completed inventories. Validate their summaries, align optional relative-root prefixes, classify exact-path states, and preserve full evidence for same-name-and-size candidates found at different paths.
- [x] Generate a bounded interactive cross-drive HTML report with Primary/Secondary folder candidates and drill-down evidence while retaining complete comparison data in local CSV files.
- [ ] Generate an overall storage `index.html` from the complete inventory. Show total and non-trash storage by category, file type, size band, and meaningful folder, then link to duplicate-candidate and folder drill-down reports.
- [ ] Let the user choose folders for deeper analysis; estimate time from observed scanning speed and explain uncertainty.
- [ ] Compare duplicate candidates by filename and size, reject generic folder-name matches when meaningful ancestor context conflicts, then verify matching content with hashes before recommending removal.
- [ ] Present recommendations and their evidence. Let the user mark candidates as Agree, Disagree, or Needs Review; swap Master and Secondary; add notes; and export decisions locally using stable identifiers.
- [ ] Add size-band filters and batch review for candidates below 1 GB. Estimate hashing and file operations from both byte throughput and per-file overhead, and checkpoint large small-file batches by elapsed time or file count.
- [ ] Add file-type drill-down pages that group files by meaningful folder root and organizational context. Show file count, total size, percentage of the type, and candidate overlap so users can understand where large collections of small files reside before reviewing merges.
- [ ] Offer Verify, Sync missing files, Delete fully verified Secondary, and Keep separate. Disable deletion until every Secondary file is confirmed in the Master, with no missing, conflicting, unreadable, or unverified files.
- [ ] Require explicit approval of the specific `rsync` dry run, transfer, verification, and source deletion steps.

Validate the first implementation against both a local folder and an already accessible NAS share. Additional phone and cloud integrations can follow if time permits.

## Lessons from the initial NAS investigation

- Similar folder names do not establish identical contents.
- Matching relative paths and sizes identify candidates; content hashes establish file equality.
- Files with different paths may still be duplicates, so same-path comparison alone is incomplete.
- A mounted SMB filesystem can stall even when direct SMB listings work. Show progress and surface connection failures.
- When possible, run the scanner on the storage host against its local mount. A direct filesystem inventory avoids per-directory SMB metadata round trips. One development comparison on the same NAS volume was approximately 55 times faster than traversing it through SMB; actual gains depend on the filesystem, network, and folder structure.
- A pre-scan estimate must use bounded sampling and observed throughput. A complete counting traversal simply performs the expensive part twice.
- Save inventory checkpoints periodically, rather than rewriting the entire inventory after each directory.
- Batch checkpoint writes by elapsed time or record count so checkpointing does not dominate a fast local scan.
- Write large inventories directly to a dedicated results folder on the storage volume instead of filling a small host's system drive or SD card.
- Build drive-wide candidates from normalized exact filenames and byte sizes, then use folder-name similarity and smaller-folder coverage for prioritization.
- Treat generic leaf folders such as `Inbox`, `Sent Items`, `Camera`, and `Miscellaneous` as weak context. Compare several meaningful ancestor components and reject or downgrade candidates when those contexts conflict, such as folders belonging to different organizations.
- Allow explicit user decisions and future configuration rules to keep unrelated organizational or personal contexts separate even when files happen to match.
- Hash only selected candidates. Hashing the complete source during initial inventory would add unnecessary I/O.
- Treat many-small-file workloads separately from large-file workloads. Opening and checking thousands of small files can take longer than their combined byte size suggests, so estimates must include file count as well as bytes.
- Keep sub-1-GB candidates visible for organizational cleanup, but allow users focused on reclaiming capacity to prioritize larger verified savings first.
- Generate recommendations only from inventories explicitly marked complete, and keep candidate savings separate when folder rows overlap.
- A sample of matching hashes does not verify every candidate or establish reclaimable space.

## Development data

Use synthetic folders for repeatable verification, including empty files, same-size files with different contents, renamed duplicates, exclusions, and inaccessible paths. Keep credentials, real file inventories, reports, and personal files out of Git. Local output belongs in `local-data/` or `scan-results/`, both ignored by Git.

The privacy boundary is local: the scanner writes inventories, manifests, reports, and errors to local output files and does not upload them. Any future network feature must document its data flow and obtain explicit user approval before sending file metadata or contents elsewhere.
