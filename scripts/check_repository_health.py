"""Read-only repository/environment checks, with optional frozen local data audit."""
from pathlib import Path
from collections import Counter
import argparse
import ast
import hashlib
import importlib.metadata
import json
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]

def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], stderr=subprocess.PIPE).decode('utf-8').strip()

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''): h.update(block)
    return h.hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--require-clean', action='store_true')
    parser.add_argument('--reconstruction', action='store_true')
    args = parser.parse_args()
    errors = []; counts = Counter()
    base = Path(sys.base_prefix)
    if Path(sys.prefix).resolve() != (ROOT / '.venv').resolve(): errors.append('Interpreter must use project .venv')
    if 'conda' in str(base).lower() or 'anaconda' in sys.version.lower() or (base / 'conda-meta').exists(): errors.append('Conda base interpreter')
    names = sorted(set(p for p in git('ls-files', '-co', '--exclude-standard', '-z').split('\0') if p))
    files = [ROOT / p for p in names if (ROOT / p).is_file()]
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        if path.stat().st_size >= 100 * 1024 * 1024: errors.append('GitHub oversized file: ' + rel)
        if path.relative_to(ROOT).parts[0] in ('.venv', 'runs', 'backups'): errors.append('Local-only directory selected for Git: ' + rel)
        try:
            if path.suffix == '.json': json.loads(path.read_text(encoding='utf-8-sig')); counts['json'] += 1
            elif path.suffix == '.py': ast.parse(path.read_text(encoding='utf-8-sig')); counts['python'] += 1
            elif path.suffix in ('.yaml', '.yml'): yaml.safe_load(path.read_text(encoding='utf-8-sig')); counts['yaml'] += 1
            elif path.suffix == '.ipynb':
                book = json.loads(path.read_text(encoding='utf-8'))
                for cell in book['cells']:
                    if cell['cell_type'] == 'code': ast.parse(''.join(cell.get('source', [])))
                counts['notebooks'] += 1
        except Exception as exc: errors.append(rel + ': ' + repr(exc))
    for path in [ROOT / 'README.md', *(ROOT / 'docs').glob('*.md')]:
        for target in re.findall(r'\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
            if '://' in target or target.startswith('#'): continue
            target = target.split('#')[0]
            if target and not (path.parent / target).exists(): errors.append(f'{path.name}: missing link {target}')
            counts['document_links'] += 1
    for old in ('Test', 'ABCG-Simulation', 'src/udc/controller/coverage.py'):
        if (ROOT / old).exists(): errors.append('Retired framework path still present: ' + old)
    status = git('status', '--porcelain=v1')
    if args.require_clean and status: errors.append('Working tree is not clean')
    data = {'status': 'not_requested'}
    if args.reconstruction:
        report = ROOT / 'reports/project_rebuild_20261023'
        listing = json.loads((report / 'reconstruction_files.json').read_text(encoding='utf-8'))
        for rel, info in listing.items():
            path = ROOT / rel
            if not path.is_file() or sha(path) != info['sha256']: errors.append('Reconstruction mismatch: ' + rel)
        import gzip
        with gzip.open(report / 'historical_references_after_reconstruction.json.gz', 'rt', encoding='utf-8') as stream:
            refs = json.load(stream)
        raw = [r for r in refs if Path(r['path']).suffix.lower() in ('.npz', '.sqlite', '.wkb')]
        for row in raw:
            path = ROOT / row['path']
            if not path.is_file() or sha(path) != row['expected_sha256']: errors.append('Historical raw-data mismatch: ' + row['path'])
        data = {'status': 'checked', 'reconstruction_files': len(listing), 'historical_raw_references': len(raw)}
    deps = {name: importlib.metadata.version(name) for name in ('numpy', 'scipy', 'shapely', 'PyYAML', 'jupedsim', 'PedPy', 'pytest')}
    result = {'healthy': not errors, 'errors': errors, 'files_checked': len(files), 'checks': dict(counts),
              'commit': git('rev-parse', 'HEAD'), 'tree': git('rev-parse', 'HEAD^{tree}'),
              'working_tree_clean': not status, 'python': sys.version, 'executable': sys.executable,
              'base_prefix': str(base), 'dependencies': deps, 'data': data}
    output = Path(args.output).resolve()
    if output != ROOT / 'runs' and not output.is_relative_to(ROOT / 'runs'): raise ValueError('Health outputs belong under project runs/')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in ('python', 'dependencies')}, ensure_ascii=False))
    if errors: raise SystemExit(1)

if __name__ == '__main__': main()
