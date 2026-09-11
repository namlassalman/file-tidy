# file-tidy

File Tidy helps you understand storage usage, find duplicate files, and review opportunities to free space.

This is a small prototype in development. The first version will work with local folders and accessible network-attached storage (NAS) shares. Dedicated mobile phone and cloud storage integrations are future work.

## Current status

- [x] Python configuration loader
- [x] Development checklist
- [ ] File scanning and inventory
- [ ] Storage usage charts
- [ ] Duplicate detection
- [ ] User-approved file changes

## Planned workflow

1. Choose a folder and specify any folders to exclude before scanning begins.
2. Inventory files with visible progress. Show file count and total size by file type, and highlight deeply nested folders for review.
3. Choose folders for deeper analysis. Estimate scan time using file and folder counts, nesting depth, and observed scanning speed. The initial inventory also takes time; estimates will be refined as scanning progresses.
4. Review recommendations for saving space, including duplicate candidates and the evidence supporting each recommendation.
5. Approve specific file changes before they are applied. Scanning and analysis are read-only.

## Duplicate detection and savings

Matching names or file sizes identify possible duplicates. Matching content hashes confirm identical file contents, including files with different names or locations. Folder names alone do not establish that entire folders are duplicates.

Reports will distinguish potential savings from verified duplicate content. Actual space savings depend on which copies are approved for removal and how the storage system handles deleted files. Verifying a sample does not verify all candidates.

## Local setup

Use Python 3.11 or later. No third-party dependencies are currently required.

1. Copy `config.example.json` to `config.local.json`.
2. Enter your NAS host, username, share, folders to compare, and exclusions. Folder entries are relative to the share.
3. Load the settings from Python:

   ```python
   from config import load_config

   settings = load_config()
   ```

This loads configuration only; it does not connect to storage or start a scan. The current configuration describes a NAS share; local-folder selection will be added with the scanner.

Keep your actual connection details and personal folder names in `config.local.json`, which is ignored by Git. Keep passwords in the system credential manager or an interactive authentication prompt. Store local inventories and reports in the ignored `local-data/` or `scan-results/` directories.

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for environment setup and the short implementation checklist.
