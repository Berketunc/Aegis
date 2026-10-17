#!/usr/bin/env bash
# Private on-VM archive for later off-VM transfer. Does not print credentials.
set -euo pipefail
if [[ $# -gt 1 ]]; then echo "Usage: bash linuxone/backup_runtime.sh [private-output.tar.gz]" >&2; exit 2; fi
script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="${AEGIS_PROJECT_DIR:-$(dirname -- "$script_dir")}"
output_file="${1:-$project_dir/.deliverables/private/aegis-runtime-$(date -u +%Y%m%dT%H%M%SZ).tar.gz}"
umask 077
python3 - "$project_dir" "$output_file" <<'PY'
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tarfile
import tempfile
import time
import urllib.parse


def digest_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def sqlite_snapshot(source, destination):
    deadline = time.monotonic() + 30
    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise TimeoutError('SQLite backup exceeded its time limit')
    incoming = sqlite3.connect('file:' + urllib.parse.quote(str(source)) + '?mode=ro', uri=True, timeout=5)
    outgoing = sqlite3.connect(str(destination))
    try:
        incoming.backup(outgoing, pages=256, progress=progress, sleep=.05)
        outgoing.execute('PRAGMA journal_mode=DELETE')
        rows = outgoing.execute('PRAGMA integrity_check').fetchall()
        if rows != [('ok',)]:
            raise ValueError('SQLite snapshot failed integrity_check')
    finally:
        outgoing.close()
        incoming.close()
    destination.chmod(0o600)


class HashingReader:
    def __init__(self, source):
        self.source = source
        self.digest = hashlib.sha256()
    def read(self, size=-1):
        block = self.source.read(size)
        self.digest.update(block)
        return block


def backup(project, output):
    project = project.resolve()
    runtime, results = project / '.runtime', project / 'eval'
    if runtime.is_symlink() or not runtime.is_dir() or not (runtime / 'services.env').is_file():
        raise ValueError('A real .runtime directory and its original services.env are required')
    # Never include the output archive in its own inputs, or follow output links.
    if output.is_symlink() or output.exists():
        raise ValueError('Backup output must be a new file; existing paths are never overwritten')
    output = output.resolve()
    for source_root in (runtime, results):
        if output == source_root or source_root in output.parents:
            raise ValueError('Backup output must be outside .runtime and eval')
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    paths = []
    for source_root in (runtime, results):
        if not source_root.exists():
            continue
        if source_root.is_symlink():
            raise ValueError('Backup source directories must not be symlinks')
        for path in sorted(source_root.rglob('*')):
            relative = path.relative_to(source_root)
            if source_root == runtime and relative.parts[0] == 'backups':
                continue  # Old private archives do not recursively grow each new backup.
            if path.is_symlink():
                raise ValueError('Backup inputs contain a symlink; provide real files before backing up')
            if path.is_dir():
                continue
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError('Backup inputs must be regular files')
            paths.append(path)
    databases = set()
    for path in paths:
        with path.open('rb') as source:
            if source.read(16) == b'SQLite format 3\x00':
                databases.add(path)
    sidecars = {Path(str(path) + suffix) for path in databases for suffix in ('-wal', '-shm', '-journal')}
    for path in paths:
        if path.name.endswith(('-wal', '-shm', '-journal')) and path not in sidecars:
            raise ValueError('Orphan SQLite sidecar found; restore/check its main database first')
    manifest = {'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
                'scope': 'Private runtime keys/data/models/configuration plus saved evaluation results',
                'sqlite_method': 'Python sqlite3.Connection.backup; each DB individually consistent; integrity_check=ok',
                'cross_database_atomic_snapshot': False,
                'restore_note': 'Keep the complete archive private. Restore keys and data together; stop services before restoring.',
                'excluded': ['.runtime/backups', 'SQLite -wal/-shm/-journal sidecars superseded by consistent snapshots'],
                'files': []}
    temporary_archive = None
    try:
        with tempfile.TemporaryDirectory(prefix='aegis-db-snapshots-') as staging:
            staged = {}
            for number, database in enumerate(sorted(databases)):
                snapshot = Path(staging) / f'database-{number}.sqlite3'
                sqlite_snapshot(database, snapshot)
                staged[database] = snapshot
            with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.aegis-backup-', delete=False) as temporary:
                temporary_archive = Path(temporary.name)
                os.chmod(temporary_archive, 0o600)
                with tarfile.open(fileobj=temporary, mode='w:gz') as archive:
                    for path in paths:
                        if path in sidecars:
                            continue
                        source = staged.get(path, path)
                        with source.open('rb') as stream:
                            metadata = os.fstat(stream.fileno())
                            name = str(path.relative_to(project))
                            info = tarfile.TarInfo(name)
                            info.size, info.mtime = metadata.st_size, int(metadata.st_mtime)
                            info.mode = 0o700 if metadata.st_mode & stat.S_IXUSR else 0o600
                            reader = HashingReader(stream)
                            archive.addfile(info, reader)
                            manifest['files'].append({'path': name, 'size_bytes': info.size,
                                'sha256': reader.digest.hexdigest(), 'sqlite_snapshot': path in databases})
                    content = (json.dumps(manifest, indent=2) + '\n').encode()
                    info = tarfile.TarInfo('BACKUP_MANIFEST.json')
                    info.size, info.mode = len(content), 0o600
                    archive.addfile(info, io.BytesIO(content))
                temporary.flush()
                os.fsync(temporary.fileno())
            # Exclusive publication: never overwrite a path created during backup.
            os.link(temporary_archive, output)
            output.chmod(0o600)
        print(json.dumps({'status': 'backed_up', 'archive': str(output),
                          'sha256': digest_file(output), 'files': len(manifest['files']),
                          'sqlite_databases': len(databases), 'off_vm_copy_performed': False,
                          'next_step': 'Copy this private archive off the VM; do not commit or share it publicly.'}))
    finally:
        if temporary_archive is not None:
            temporary_archive.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        backup(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, ValueError, sqlite3.Error, TimeoutError, tarfile.TarError) as error:
        # Values from services.env and database rows are never printed.
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__, 'error': str(error)}))
        raise SystemExit(1)
PY
