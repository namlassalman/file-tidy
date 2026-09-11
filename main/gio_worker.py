"""Stream a recursive GIO metadata inventory for the scanner parent process."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path, PurePosixPath

from gi.repository import Gio


def excluded(path: str, values: list[str]) -> bool:
    parts = tuple(p.casefold() for p in PurePosixPath(path).parts)
    for value in values:
        wanted = tuple(p.casefold() for p in PurePosixPath(value.replace('\\', '/')).parts)
        if len(wanted) == 1 and wanted[0] in parts:
            return True
        if wanted and parts[:len(wanted)] == wanted:
            return True
    return False


def main() -> int:
    payload = json.loads(sys.argv[1])
    root_path = Path(payload['root'])
    root_uri = payload.get('root_uri')
    excluded_folders = payload.get('excluded_folders', [])
    stack = []
    for folder in payload.get('folders', []) or ['.']:
        relative = folder.replace('\\', '/')
        if root_uri:
            file = Gio.File.new_for_uri(root_uri)
            if relative != '.':
                for part in relative.split('/'):
                    file = file.get_child(part)
        else:
            path = root_path if relative == '.' else root_path.joinpath(*relative.split('/'))
            file = Gio.File.new_for_path(str(path))
        stack.append((relative if relative != '.' else '.', file))
    files = folders = 0
    batch: list[str] = []

    def emit_batch() -> None:
        if batch:
            sys.stdout.write("\n".join(batch) + "\n")
            sys.stdout.flush()
            batch.clear()
    last = time.monotonic()
    while stack:
        relative_folder, folder = stack.pop()
        folders += 1
        try:
            enum = folder.enumerate_children(
                'standard::name,standard::type,standard::size',
                Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS,
                None,
            )
            while True:
                info = enum.next_file(None)
                if info is None:
                    break
                name = info.get_name()
                relative_path = f'{relative_folder}/{name}' if relative_folder != '.' else name
                file_type = info.get_file_type()
                if file_type == Gio.FileType.DIRECTORY:
                    if not excluded(relative_path, excluded_folders):
                        stack.append((relative_path, folder.get_child(name)))
                    continue
                if file_type == Gio.FileType.SYMBOLIC_LINK:
                    batch.append(json.dumps({'_error': {'path': relative_path, 'error': 'symlink skipped'}}))
                    continue
                suffix = Path(name).suffix.casefold()
                record = {
                    'path': relative_path,
                    'source': relative_path.split('/', 1)[0],
                    'size': info.get_size(),
                    'type': suffix[1:] if suffix else '[no extension]',
                    'depth': len(PurePosixPath(relative_path).parts) - 1,
                }
                batch.append(json.dumps(record, ensure_ascii=False))
                files += 1
                if len(batch) >= 1000:
                    emit_batch()
            enum.close(None)
        except Exception as exc:
            batch.append(json.dumps({'_error': {'path': relative_folder, 'error': str(exc)}}))
        if time.monotonic() - last >= 10:
            print(f'GIO progress: {files:,} files, {folders:,} folders', file=sys.stderr, flush=True)
            last = time.monotonic()
    emit_batch()
    print(json.dumps({'_summary': {'files': files, 'folders': folders}}), flush=True)
    print(f'GIO complete: {files:,} files, {folders:,} folders', file=sys.stderr, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
