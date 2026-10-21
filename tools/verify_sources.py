#!/usr/bin/env python3
"""Verify the reviewed source bundle before building or running its tools."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'SOURCE_MANIFEST.json'

def files():
    result = []
    for name in ('Sources', 'resources', 'tools', 'tests', '.github', 'Validation'):
        result.extend(p for p in (ROOT / name).rglob('*') if p.is_file()
                      and '__pycache__' not in p.parts and p.suffix != '.pyc')
    result.extend(p for p in ROOT.iterdir() if p.is_file()
                  and (p.suffix in ('.md', '.yml') or p.name == '.gitignore'))
    return sorted(result)

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def verify():
    doc = json.loads(MANIFEST.read_text())
    if doc.get('schema_version') != 1 or not isinstance(doc.get('files'), dict):
        raise ValueError('Manifesto de fontes inválido')
    expected = {p.relative_to(ROOT).as_posix() for p in files()}
    if set(doc['files']) != expected:
        raise ValueError('Arquivos mudaram; revise as alterações antes de atualizar o manifesto')
    for name, digest in doc['files'].items():
        rel = PurePosixPath(name)
        if rel.is_absolute() or '..' in rel.parts or '\\' in name:
            raise ValueError('Caminho inválido no manifesto')
        p = ROOT / rel
        if p.is_symlink() or not p.is_file() or sha(p) != digest:
            raise ValueError('Hash diferente: ' + name)
    return {'status': 'verified', 'files': len(expected), 'manifest_sha256': sha(MANIFEST)}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Atualizar somente após revisão intencional das mudanças')
    args = parser.parse_args()
    if args.write:
        entries = files()
        if any(p.is_symlink() for p in entries): raise ValueError('Symlink não permitido')
        MANIFEST.write_text(json.dumps({'schema_version': 1, 'version': '0.1.0',
            'files': {p.relative_to(ROOT).as_posix(): sha(p) for p in entries}}, indent=2)+'\n')
    print(json.dumps(verify()))

if __name__ == '__main__': main()
