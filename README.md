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
- [ ] Duplicate detection: group identical filenames and byte sizes as high-confidence candidates, preserve complete paths for evidence, allow shortened parent-folder labels in summaries, mark incomplete scans, and require content hashes before automated deletion recommendations.
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

Run a read-only inventory with `python -m main.scanner`. The scanner uses `source_path` for local sources. For an NAS source, set `nas_mount_path` to an already accessible mounted share; it does not handle credentials or mount storage itself. Inventory records are written as JSONL, with a summary and an error file alongside them.

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

Keep your actual connection details and personal folder names in `config.local.json`, which is ignored by Git. Keep passwords in the system credential manager or an interactive authentication prompt. Store local inventories and reports in the ignored `local-data/` or `scan-results/` directories.

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for environment setup and the short implementation checklist.
