# Development

This is a small prototype intended for a few hours of development. The README defines the product scope; use the checklist below to track the first implementation.

## Local setup

Start with Python 3.11 or later. Create an isolated environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

`config.py` loads local settings using the Python standard library. The scanner and interface are not implemented yet. Add dependencies only when an implementation requires them.

Copy `config.example.json` to `config.local.json` and fill in the NAS host, username, share, folders to compare, and exclusions. `config.local.json` is ignored by Git and must stay on your machine. Folder entries are relative to the configured share. Keep passwords in the system credential manager or an interactive authentication prompt.

Application code should obtain these values through `from config import load_config` and `settings = load_config()`, rather than hardcoding connection details or personal folder names. Do not print settings into logs or commit real scan output. The public example must contain placeholders only.

## First working version

- [ ] Let the user select a folder and exclusions before scanning.
- [ ] Inventory file paths, types, sizes, and folder depths without changing files; report access errors and progress.
- [ ] Show file counts and total size by type in one chart, and highlight deeply nested folders.
- [ ] Let the user choose folders for deeper analysis; estimate time from observed scanning speed and explain uncertainty.
- [ ] Compare duplicate candidates by size, then verify matching content with hashes before recommending removal.
- [ ] Present recommendations and their evidence. Require explicit approval of the specific changes before applying them.

Begin with a local folder or an already accessible NAS share. Additional phone and cloud integrations can follow if time permits.

## Lessons from the initial NAS investigation

- Similar folder names do not establish identical contents.
- Matching relative paths and sizes identify candidates; content hashes establish file equality.
- Files with different paths may still be duplicates, so same-path comparison alone is incomplete.
- A mounted SMB filesystem can stall even when direct SMB listings work. Show progress and surface connection failures.
- Save inventory checkpoints periodically, rather than rewriting the entire inventory after each directory.
- A sample of matching hashes does not verify every candidate or establish reclaimable space.

## Development data

Use synthetic folders for repeatable verification, including empty files, same-size files with different contents, renamed duplicates, exclusions, and inaccessible paths. Keep credentials, real file inventories, reports, and personal files out of Git. Local output belongs in `local-data/` or `scan-results/`, both ignored by Git.
