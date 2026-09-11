# file-tidy

File Tidy helps you understand storage usage, find duplicate files, and review opportunities to free space.

This is a small prototype in development. The first version will work with local folders and accessible network-attached storage (NAS) shares. Dedicated mobile phone and cloud storage integrations are future work.

## Background and motivation

Files accumulate across phones, laptops, desktops, external drives, cloud storage, and NAS devices. Over time, backups overlap, folders become deeply nested, and old copies are difficult to distinguish from the files people still need. Storage costs also continue to rise, while the available space on personal devices and shared storage remains limited.

People need a practical way to understand what is taking up space before deciding what to keep, archive, or remove. A folder with a familiar name is not necessarily a duplicate, and matching file names or sizes do not prove that two files have the same contents. Any useful tool must show its evidence and keep the final decision with the owner of the files.

File Tidy is intended to make that review manageable. It will start by examining folders the user selects, respecting explicit exclusions, and producing a read-only inventory. It will then summarize storage usage, identify areas that deserve closer attention, and make recommendations that the user can review before any change is applied.

The project is designed for four common storage sources:

- Mobile phones and removable media
- Cloud storage
- Desktops and laptops
- Network-attached storage (NAS)

The initial prototype focuses on local folders and accessible NAS shares so that the scanning and review workflow can be tested before adding dedicated integrations.

## Current status

- [x] Python configuration loader
- [x] Development checklist
- [x] File scanning and inventory
- [x] Storage usage charts
- [ ] Overall storage dashboard: make `index.html` summarize the complete inventory by category, file type, size band, and meaningful folder, with an include/exclude-trash control and links to duplicate and folder reports.
- [x] Drive-wide duplicate candidate tables and interactive evidence reports
- [ ] Duplicate confirmation: compare meaningful ancestor context so generic folder names such as `Inbox` do not join unrelated collections, then hash selected candidate files before automated deletion recommendations.
- [ ] Candidate review controls: let the user mark each Master/Secondary relationship as Agree, Disagree, or Needs Review; allow Master/Secondary swapping and optional notes; export decisions locally using stable candidate identifiers.
- [ ] Small-file candidate workflow: let the user filter and batch candidates below 1 GB, distinguish storage-saving priorities from organizational cleanup, and estimate verification time using both file count and total bytes. Make file-type totals clickable so types such as `.msg` drill down into meaningful folder roots, counts, sizes, and organizational context.
- [ ] Verified action controls: offer Verify, Sync missing files, Delete fully verified Secondary, and Keep separate. Keep deletion disabled while any Secondary file is missing, conflicting, unreadable, or unverified.
- [ ] User-approved file changes: use the aggregated folder table and detailed file table to define the proposed change, run an `rsync` dry run and summarize its potential changes, require explicit approval, then run the transfer with existing destination paths skipped and hidden files excluded when requested. Allow optional manual verification before deleting remaining source files; leave skipped conflicts or errors for review. If a folder was not fully scanned, require manual destination verification and explicit approval before deleting the complete source folder.

## Planned workflow

1. Choose a folder and specify any folders to exclude before scanning begins.
2. Inventory files with visible progress. Show file count and total size by file type, and highlight deeply nested folders for review.
3. Choose folders for deeper analysis. Estimate scan time using file and folder counts, nesting depth, and observed scanning speed. The initial inventory also takes time; estimates will be refined as scanning progresses.
4. Review recommendations for saving space, including duplicate candidates and the evidence supporting each recommendation.
5. Approve specific file changes before they are applied. Scanning and analysis are read-only.

## Duplicate detection and savings

Matching names or file sizes identify possible duplicates. Matching content hashes confirm identical file contents, including files with different names or locations. Folder names alone do not establish that entire folders are duplicates.

Reports will distinguish potential savings from verified duplicate content. Actual space savings depend on which copies are approved for removal and how the storage system handles deleted files. Verifying a sample does not verify all candidates.

## Privacy and local data

File Tidy is designed to keep scan data on the user’s machine. File contents, file names, inventories, manifests, reports, and access-error logs are written locally to the configured output directory. The scanner does not upload files or manifests to a remote service. Network-attached storage is read through an already accessible connection, and the scanner does not mount shares or handle passwords.

Local configuration belongs in the ignored `config.local.json` file. Generated inventories and reports belong in the ignored `scan-results/` directory, so they are not included in a public Git commit unless a user explicitly moves or force-adds them.

## Local setup

Use Python 3.11 or later. No third-party dependencies are currently required.

1. Copy `config.example.json` to `config.local.json`.
2. Choose a source by setting `source_type` to `local` or `nas`.
   - For a local folder, set `source_path` to a path such as `C:/Users/YourName/Documents` or `/home/yourname/Documents`. Use forward slashes in JSON, or escape Windows backslashes as `C:\\Users\\YourName\\Documents`.
   - For an NAS share, set `nas_host`, `nas_username`, and `nas_share`. Folder entries are relative to that share.
3. Add folders to scan and exclusions. An empty `folders` list means the source root is the starting point.
4. Load the settings from Python:

   ```python
   from main.config import load_config

   settings = load_config()
   ```

Run a read-only inventory with `python -m main.scanner`. The scanner uses `source_path` for local sources. For an NAS source, set `nas_mount_path` to an already accessible mounted share; it does not handle credentials or mount storage itself. Each JSONL inventory record includes the relative path, absolute path, URI, byte size, file type, path depth, file modification time, and parent-folder modification time. A summary and error file are written alongside the inventory.

For large NAS inventories, prefer running File Tidy on the NAS host against its local filesystem mount. This avoids a separate SMB metadata request for every file and folder. See [Scanning large NAS volumes efficiently](docs/NAS_SCANNING.md) for the setup and direct-output workflow.

When the scanner runs directly on a NAS host or another small Linux device, write large scan results to a dedicated folder on the storage volume instead of the device's system drive or SD card. Supplying the inventory and error paths places all three outputs in that folder and avoids a separate copy step:

```bash
python -m main.scanner \
  --config config.local.json \
  --output /path/on/nas/file-tidy-results/inventory.jsonl \
  --errors /path/on/nas/file-tidy-results/inventory-errors.jsonl \
  --progress-every 10000
```

The scanner creates `inventory.summary.json` beside the inventory. Choose an output folder that remains local to the user and is excluded from Git.

For a pre-scan count, run `python -m main.scanner --estimate-only`. NAS scans use the GIO metadata walker by default; local scans keep the Python filesystem walker. Long scans can write a checkpoint with `--checkpoint scan-results/inventory.checkpoint.json` and resume with `--resume`. Only a completed scan produces a summary suitable for comparison and reporting.

Generate an interactive, self-contained HTML report from the summary:

```bash
python -m main.report scan-results/inventory.summary.json
```

The report includes storage-by-file-type bars, sortable details, totals, and access-error counts. It uses no third-party charting library.

To visualize the earlier folder comparison while a new scan runs:

```bash
python -m main.comparison_report scan-results/comparison.csv
```

This creates a local HTML index with exploratory charts for overlap categories, file types, path depth, and folder concentration. Each top-level folder links to a separate detail page with filtering and sortable file rows. It shows candidate overlap; content hashes are required to confirm duplicate files.

For a completed drive-wide inventory, build the Master/Secondary candidate tables and their interactive report:

```bash
python -m main.duplicates scan-results/inventory.jsonl
python -m main.duplicate_report \
  scan-results/duplicate-folders.csv \
  scan-results/duplicate-files.csv \
  --output scan-results/duplicates.html
```

The aggregate table ranks related folders using normalized exact filenames, byte sizes, folder-name similarity, overlap coverage, and modification activity. Before action, generic leaf names such as `Inbox`, `Sent Items`, or `Camera` must also be checked against meaningful ancestor context so a shared leaf name does not join unrelated organizations or collections. The detail table preserves the complete locations supporting each candidate. Matching names and sizes are high-confidence candidates, but only matching content hashes confirm duplicate files. Candidate savings from overlapping folder rows must not be added together.

Keep your actual connection details and personal folder names in `config.local.json`, which is ignored by Git. Keep passwords in the system credential manager or an interactive authentication prompt. Store local inventories and reports in the ignored `local-data/` or `scan-results/` directories.

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for environment setup and the short implementation checklist.
