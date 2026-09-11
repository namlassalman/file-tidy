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
2. Choose a source by setting `source_type` to `local` or `nas`.
   - For a local folder, set `source_path` to a path such as `C:/Users/YourName/Documents` or `/home/yourname/Documents`. Use forward slashes in JSON, or escape Windows backslashes as `C:\\Users\\YourName\\Documents`.
   - For an NAS share, set `nas_host`, `nas_username`, and `nas_share`. Folder entries are relative to that share.
3. Add folders to scan and exclusions. An empty `folders` list means the source root is the starting point.
4. Load the settings from Python:

   ```python
   from config import load_config

   settings = load_config()
   ```

This loads configuration only; it does not connect to storage or start a scan. The scanner will use `source_path` for local sources and the NAS fields for NAS sources.

Keep your actual connection details and personal folder names in `config.local.json`, which is ignored by Git. Keep passwords in the system credential manager or an interactive authentication prompt. Store local inventories and reports in the ignored `local-data/` or `scan-results/` directories.

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for environment setup and the short implementation checklist.
