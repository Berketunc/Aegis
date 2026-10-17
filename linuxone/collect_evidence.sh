#!/usr/bin/env bash
# Actual command output and sanitized database summaries; no screenshots fabricated.
set -euo pipefail
if [[ $# -gt 1 ]]; then echo "Usage: bash linuxone/collect_evidence.sh [output-directory]" >&2; exit 2; fi
script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="${AEGIS_PROJECT_DIR:-$(dirname -- "$script_dir")}"
output_dir="${1:-$project_dir/evidence/platform-$(date -u +%Y%m%dT%H%M%SZ)}"
umask 077
python3 - "$project_dir" "$output_dir" <<'PY'
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone

# These scripts never enumerate container environments or read token-vault values.
HEALTH_CODE = r'''
import json, sys, urllib.error, urllib.request
try:
    try:
        response = urllib.request.urlopen('http://127.0.0.1:' + sys.argv[1] + '/health', timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    body = json.load(response)
    allowed = ('status', 'ready', 'upload_ready', 'txn', 'thresholds_version', 'policy_engine',
               'preprocessing_version', 'model_loaded', 'runtime', 'queue_depth', 'in_flight')
    result = {key: body[key] for key in allowed if key in body}
    for section, keys in {'model': ('ready', 'version', 'device', 'artifact_sha256', 'error'),
                          'privacy': ('ready', 'error'),
                          'evidence_storage': ('ready', 'original_encryption', 'error')}.items():
        value = body.get(section)
        if isinstance(value, dict):
            result[section] = {key: value[key] for key in keys if key in value}
    print(json.dumps({'http_status': response.code, 'health': result}))
except Exception as error:
    print(json.dumps({'health_unavailable': True, 'error_type': type(error).__name__}))
    raise SystemExit(1)
'''

GATEWAY_SUMMARY_CODE = r'''
import json, os, sqlite3, urllib.parse
path = os.environ.get('DB_PATH', '/data/clmi.db')
db = sqlite3.connect('file:' + urllib.parse.quote(path) + '?mode=ro', uri=True)
tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
result = {'counts': {name: db.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                     for name in ('claims', 'decisions', 'journal', 'egress', 'notes', 'token_vault') if name in tables}}
if 'decisions' in tables:
    result['decision_bands'] = dict(db.execute('SELECT band, COUNT(*) FROM decisions GROUP BY band'))
if 'journal' in tables:
    result['journal_metadata'] = [dict(zip(('seq', 'ts', 'txn', 'claim_id', 'event'), row)) for row in
        db.execute('SELECT seq, ts, txn, claim_id, event FROM journal ORDER BY seq DESC LIMIT 20')]
    result['journal_triggers'] = [row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='journal'")]
if 'egress' in tables:
    result['egress_metadata'] = [{'egress_id': row[0], 'claim_id': row[1],
                                  'field_names_only': sorted(json.loads(row[2]).keys())}
                                 for row in db.execute('SELECT egress_id, claim_id, payload_json FROM egress ORDER BY ts DESC LIMIT 20')]
db.close()
result['privacy_note'] = 'No token-vault values, descriptions, payload values or raw PII were exported.'
print(json.dumps(result, indent=2))
'''

RISK_SUMMARY_CODE = r'''
from collections import Counter
import json, os, pathlib, sqlite3, urllib.parse
root = pathlib.Path(os.environ.get('AEGIS_DATA_DIR', '/data'))
result = {'evidence_files': {}, 'sqlite_counts': {}}
originals = list((root / 'objects').glob('*/original.aesgcm'))
valid_headers = 0
for path in originals:
    with path.open('rb') as stream:
        valid_headers += stream.read(7) == b'AEGIS1\x00'
result['evidence_files'] = {'encrypted_original_count': len(originals), 'aegis_format_headers': valid_headers,
                            'scope': 'Header inspection only; cryptographic round-trip evidence is recorded separately.'}
try:
    import numpy as np
    arrays = Counter()
    for path in (root / 'objects').glob('*/crops.npy'):
        array = np.load(path, mmap_mode='r', allow_pickle=False)
        arrays[str(tuple(array.shape)) + '/' + str(array.dtype)] += 1
    result['evidence_files']['crop_shapes_and_dtypes'] = dict(arrays)
except Exception as error:
    result['evidence_files']['crop_inspection_error'] = type(error).__name__
for filename, wanted in (('index.sqlite3', ('evidence', 'claim_evidence')),
                         ('privacy_egress.sqlite3', ('prepared_egress',))):
    path = root / filename
    if path.is_file():
        db = sqlite3.connect('file:' + urllib.parse.quote(str(path)) + '?mode=ro', uri=True)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result['sqlite_counts'][filename] = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                                             for table in wanted if table in tables}
        db.close()
print(json.dumps(result, indent=2))
'''


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def collect(project_dir, output_dir):
    output_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    docker = ['docker']
    try:
        direct = subprocess.run(docker + ['info'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        direct = None
    if direct is None or direct.returncode:
        docker = ['sudo', '-n', 'docker']
    manifest = {'recorded_at_utc': datetime.now(timezone.utc).isoformat(), 'machine': platform.machine(),
                'linuxone_s390x': platform.machine() == 's390x', 'screenshots_generated': False,
                'note': 'Actual text/JSON evidence only. Capture real portal/terminal/UI screenshots separately.',
                'captures': [], 'files': []}

    def capture(name, command, input_text=None):
        try:
            completed = subprocess.run(command, input=input_text, text=True, capture_output=True, timeout=20)
            stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            stdout, stderr, returncode = '', type(error).__name__, 1
        (output_dir / name).write_text(stdout, encoding='utf-8')
        if stderr:
            (output_dir / (name + '.stderr.txt')).write_text(stderr, encoding='utf-8')
        manifest['captures'].append({'file': name, 'command': command, 'returncode': returncode})
        return returncode

    for name, command in (
        ('architecture.txt', ['uname', '-m']), ('kernel.txt', ['uname', '-r']),
        ('cpu.txt', ['lscpu']), ('memory.txt', ['free', '-h']), ('disk.txt', ['df', '-h', '/']),
        ('listening_tcp.txt', ['ss', '-lnt']),
        ('firewall_ipv4.txt', ['sudo', '-n', 'iptables', '-L', '-n']),
        ('firewall_ipv6.txt', ['sudo', '-n', 'ip6tables', '-L', '-n']),
    ):
        capture(name, command)
    capture('containers.txt', docker + ['ps', '--filter', 'label=aegis.owner=person-b', '--format',
        '{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'])
    running = []
    for name, port in (('aegis-risk', 8000), ('aegis-gateway', 8080), ('aegis-granite', 8001)):
        # Select fields: never dump Config.Env, command arguments, logs or mounts.
        template = ('{"name":{{json .Name}},"image":{{json .Config.Image}},"image_id":{{json .Image}},'
                    '"status":{{json .State.Status}},"running":{{json .State.Running}},'
                    '"oom_killed":{{json .State.OOMKilled}},"exit_code":{{json .State.ExitCode}},'
                    '"started_at":{{json .State.StartedAt}},"network_mode":{{json .HostConfig.NetworkMode}},'
                    '"ports":{{json .NetworkSettings.Ports}}}')
        if capture(name + '-container.json', docker + ['inspect', '--format', template, name]):
            continue
        running.append(name)
        capture(name + '-health.json', docker + ['exec', '-i', name, 'python3', '-', str(port)], HEALTH_CODE)
    if running:
        capture('container_stats.jsonl', docker + ['stats', '--no-stream', '--format', '{{json .}}'] + running)
    if 'aegis-gateway' in running:
        capture('gateway-journal-egress.json', docker + ['exec', '-i', 'aegis-gateway', 'python3', '-'], GATEWAY_SUMMARY_CODE)
    if 'aegis-risk' in running:
        capture('encrypted-evidence-summary.json', docker + ['exec', '-i', 'aegis-risk', 'python3', '-'], RISK_SUMMARY_CODE)

    reports = project_dir / 'eval'
    wanted = {'stock_resnet18.json', 'upload_smoke.json', 'gateway_smoke.json', 'native_gateway_tests.json',
              'risk_service_health.json', 'latency.md', 'latency.svg'}
    if reports.is_dir():
        for path in sorted(reports.iterdir()):
            if path.is_file() and not path.is_symlink() and (path.name in wanted or
                    (path.name.startswith('gateway_benchmark_') and path.suffix == '.json')):
                shutil.copyfile(path, output_dir / ('report-' + path.name))
    manifest['missing_manual_evidence'] = ['Lab completion screenshots', 'Community Cloud portal screenshot',
        'Actual terminal/UI screenshots', 'Granite model/load/generation evidence if enabled',
        'docker stats while the final concurrency workload is running']
    for path in sorted(output_dir.iterdir()):
        if path.is_file():
            path.chmod(0o600)
            manifest['files'].append({'path': path.name, 'sha256': digest(path), 'size_bytes': path.stat().st_size})
    (output_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    (output_dir / 'manifest.json').chmod(0o600)
    print(json.dumps({'status': 'collected', 'directory': str(output_dir),
                      'failed_or_unavailable_captures': sum(item['returncode'] != 0 for item in manifest['captures']),
                      'screenshots_generated': False}))


if __name__ == '__main__':
    try:
        collect(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
    except (OSError, ValueError) as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__, 'error': str(error)}))
        raise SystemExit(1)
PY
