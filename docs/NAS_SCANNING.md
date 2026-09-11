# Scanning large NAS volumes efficiently

Walking a large folder tree through SMB can be slow even when copying large files across the same connection is fast. Inventory scanning makes a metadata request for every file and directory, so network and SMB round-trip latency can dominate the work.

When the NAS host can run Python, run File Tidy there and scan the filesystem's local mount. A development comparison on the same storage volume was approximately 55 times faster than SMB traversal. This is a workload-specific result rather than a general performance guarantee.

## Recommended layout

- Run the Python scanner on the NAS host.
- Set `source_type` to `local` because the path is local from that host's perspective.
- Point `source_path` at the storage mount.
- Write results to a dedicated directory on the storage volume instead of the host's SD card or system drive.
- Exclude that results directory from the scan so the inventory does not include itself.
- Access the completed JSONL, CSV, and HTML outputs through SMB as ordinary bulk files.

## Example setup

Clone the repository on the NAS host:

```bash
git clone https://github.com/namlassalman/file-tidy.git
cd file-tidy
```

Create an ignored `config.local.json` using local placeholders appropriate to the host:

```json
{
  "source_type": "local",
  "source_path": "/mnt/storage",
  "folders": [],
  "excluded_folders": ["WBEM", "file-tidy-results"]
}
```

An empty `folders` list scans the complete source. Exclusions are case-insensitive and apply to matching folders and all their descendants.

Create a results directory on the storage volume and run the scanner:

```bash
mkdir -p /mnt/storage/file-tidy-results

python3 -m main.scanner \
  --config config.local.json \
  --output /mnt/storage/file-tidy-results/inventory.jsonl \
  --errors /mnt/storage/file-tidy-results/inventory-errors.jsonl \
  --progress-every 10000
```

The scanner creates these files together:

- `inventory.jsonl`
- `inventory-errors.jsonl`
- `inventory.summary.json`

The summary must contain `"complete": true` before its inventory is used for comparisons or recommendations.

## Why SMB can still be useful

SMB remains suitable for retrieving a completed inventory or viewing generated HTML reports. Those are bulk transfers involving a few files. The performance problem comes from using SMB for hundreds of thousands of separate metadata lookups.

The scanner remains read-only with respect to the source files. Inventories and reports stay in the user-selected local results directory and are ignored by Git.
