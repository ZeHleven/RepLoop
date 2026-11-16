"""Package current backend and matching H5 from the repository root, without secrets."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def validate_h5_build():
    web = ROOT / 'miniapp/dist-h5'
    version = (ROOT / 'backend/VERSION').read_text(encoding='utf-8').strip()
    identity = json.loads((web / 'build-info.json').read_text(encoding='utf-8'))
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
    if identity['build_version'] != version or identity['build_commit'] != head or identity['source_dirty'] != dirty:
        raise SystemExit('H5 build identity differs from the workspace. Rebuild H5 before packaging.')
    sources = json.loads((web / 'source-hashes.json').read_text(encoding='utf-8'))
    current_names = {path.relative_to(ROOT / 'miniapp').as_posix()
                     for folder in ('src', 'config', 'types', 'scripts')
                     for path in (ROOT / 'miniapp' / folder).rglob('*') if path.is_file()}
    current_names.update(('package.json', 'pnpm-lock.yaml', 'babel.config.js', 'tsconfig.json'))
    if set(sources) != current_names:
        raise SystemExit('H5 source files were added or removed after the build. Rebuild first.')
    for name, expected in sources.items():
        source = (ROOT / 'miniapp' / name).resolve()
        if not source.is_relative_to((ROOT / 'miniapp').resolve()) or not source.is_file():
            raise SystemExit(f'Invalid or missing H5 source: {name}')
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise SystemExit(f'H5 source changed after the build: {name}. Rebuild first.')
    return identity


def main():
    web = ROOT / 'miniapp/dist-h5'
    identity = validate_h5_build()
    version = identity['build_version']
    files = {}
    for directory in ('backend/app', 'backend/alembic', 'backend/scripts', 'miniapp/dist-h5'):
        for source in (ROOT / directory).rglob('*'):
            if not source.is_file() or '__pycache__' in source.parts or source.suffix in {'.pyc', '.pyo'}:
                continue
            if source.name.startswith('.env') or source.name == 'build_metadata.json':
                continue
            files[source.relative_to(ROOT).as_posix()] = source.read_bytes()
    for name in ('backend/VERSION', 'backend/requirements.txt', 'backend/alembic.ini',
                 'deploy/demo/Dockerfile', 'deploy/demo/Dockerfile.dockerignore',
                 'deploy/demo/start_demo.sh', 'deploy/demo/README.md'):
        files[name] = (ROOT / name).read_bytes()
    files['backend/app/build_metadata.json'] = (json.dumps(identity, indent=2) + '\n').encode()
    manifest = {'build': identity, 'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}}
    files['package-manifest.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    destination = ROOT / 'deploy/demo' / f'fitness-agent-demo-{version}.zip'
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    with zipfile.ZipFile(destination) as archive:
        for name, digest in manifest['files'].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise SystemExit(f'Archive verification failed: {name}')
    print(json.dumps({'archive': str(destination), 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
                      'files': len(files), **identity}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
