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


def modified_ns(info: Gio.FileInfo) -> int:
    seconds = info.get_attribute_uint64('time::modified')
    microseconds = info.get_attribute_uint32('time::modified-usec')
    return seconds * 1_000_000_000 + microseconds * 1_000


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
            folder_info = folder.query_info(
                'time::modified,time::modified-usec',
                Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS,
                None,
            )
            parent_modified_ns = modified_ns(folder_info)
            enum = folder.enumerate_children(
                'standard::name,standard::type,standard::size,time::modified,time::modified-usec',
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
                child = folder.get_child(name)
                if file_type == Gio.FileType.DIRECTORY:
                    if not excluded(relative_path, excluded_folders):
                        stack.append((relative_path, child))
                    continue
                if file_type == Gio.FileType.SYMBOLIC_LINK:
                    batch.append(json.dumps({'_error': {'path': relative_path, 'error': 'symlink skipped'}}))
                    continue
                suffix = Path(name).suffix.casefold()
                record = {
                    'path': relative_path,
                    'source': relative_path.split('/', 1)[0],
                    'absolute_path': child.get_path() or child.get_uri(),
                    'uri': child.get_uri(),
                    'size': info.get_size(),
                    'type': suffix[1:] if suffix else '[no extension]',
                    'depth': len(PurePosixPath(relative_path).parts) - 1,
                    'modified_ns': modified_ns(info),
                    'parent_modified_ns': parent_modified_ns,
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
