#!/usr/bin/env python3
"""Package the pinned IPA and a real Apple-built framework into a new unsigned IPA."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import stat
import tempfile
import unicodedata
import zipfile

from macho import append_dependency, inspect_macho, require

ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK = 'SDKLaserCapture.framework'
EXECUTABLE = 'SDKLaserCapture'
DEPENDENCY = '@executable_path/Frameworks/' + FRAMEWORK + '/' + EXECUTABLE
MAX_MEMBER = 1024 * 1024 * 1024
MAX_TOTAL = 6 * 1024 * 1024 * 1024
MAX_ENTRIES = 50000
BLOCK = 1024 * 1024


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(BLOCK), b''):
            digest.update(block)
    return digest.hexdigest()


def checked_name(name):
    require(isinstance(name, str) and name and '\\' not in name and not name.startswith('/'), 'unsafe ZIP path')
    require(not any(ord(c) < 32 or ord(c) == 127 for c in name), 'control character in ZIP path')
    parts = name.rstrip('/').split('/')
    require(len(parts) <= 32 and all(p not in ('', '.', '..') and ':' not in p for p in parts), 'unsafe ZIP path component')
    require(len(name.encode('utf-8')) <= 4096, 'ZIP path too long')
    return unicodedata.normalize('NFC', '/'.join(parts)).casefold()


def validate_zip(archive):
    infos = archive.infolist()
    require(0 < len(infos) <= MAX_ENTRIES, 'ZIP member count exceeds limit')
    names, total = {}, 0
    for info in infos:
        require(info.orig_filename == info.filename, 'ZIP path was truncated')
        key = checked_name(info.filename)
        require(key not in names, 'duplicate or case/Unicode-colliding ZIP path')
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        require(kind in (0, stat.S_IFREG, stat.S_IFDIR), 'ZIP symlink or special file refused')
        require((kind != stat.S_IFDIR or info.is_dir()) and (kind != stat.S_IFREG or not info.is_dir()), 'ZIP file mode/path mismatch')
        require(not info.flag_bits & 1, 'encrypted ZIP member refused')
        require(info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), 'unsupported ZIP compression')
        require(0 <= info.file_size <= MAX_MEMBER, 'ZIP member exceeds size limit')
        require(not info.is_dir() or info.file_size == 0, 'nonempty ZIP directory')
        total += info.file_size
        require(total <= MAX_TOTAL, 'ZIP expanded size exceeds limit')
        names[key] = info.is_dir()
    for name in names:
        parts = name.split('/')
        for i in range(1, len(parts)):
            prefix = '/'.join(parts[:i])
            require(prefix not in names or names[prefix], 'ZIP file/directory conflict')
    return infos


def read_member(archive, name, maximum):
    info = archive.getinfo(name)
    require(not info.is_dir() and info.file_size <= maximum, 'member exceeds read limit')
    with archive.open(info) as source:
        data = source.read(maximum + 1)
        require(len(data) <= maximum and len(data) == info.file_size and source.read(1) == b'', 'member length mismatch')
    return data


def load_target(path=None):
    path = Path(path) if path is not None else ROOT / 'resources/Target.json'
    require(path.is_file() and path.stat().st_size <= 1024 * 1024, 'missing/oversized target manifest')
    data = json.loads(path.read_text(encoding='utf-8'))
    require(isinstance(data, dict) and data.get('schema_version') == 1 and isinstance(data.get('target'), dict), 'unsupported target manifest')
    target = data['target']
    for key in ('ipa_sha256', 'bundle_identifier', 'version', 'build', 'executable', 'app_path', 'main_sha256', 'main_uuid', 'minimum_os_version'):
        require(isinstance(target.get(key), str) and target[key], 'missing target field: ' + key)
    require(isinstance(target.get('ipa_size'), int) and 0 < target['ipa_size'] <= MAX_TOTAL, 'invalid pinned IPA size')
    for key in ('ipa_sha256', 'main_sha256'):
        require(len(target[key]) == 64 and all(c in '0123456789abcdef' for c in target[key]), 'invalid pinned hash')
    require('/' not in target['executable'] and '\\' not in target['executable'] and target['executable'] not in ('.', '..'), 'unsafe executable name')
    checked_name(target['app_path'])
    require(target['app_path'].startswith('Payload/') and target['app_path'].endswith('.app') and len(target['app_path'].split('/')) == 2, 'invalid top-level app path')
    return data


def inspect_base(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'base must be a regular file')
    require(0 < path.stat().st_size <= MAX_TOTAL, 'base IPA size exceeds limit')
    with zipfile.ZipFile(path) as archive:
        infos = validate_zip(archive)
        apps = [i.filename for i in infos if len(i.filename.split('/')) == 3 and i.filename.startswith('Payload/') and i.filename.split('/')[1].endswith('.app') and i.filename.endswith('/Info.plist')]
        require(len(apps) == 1, 'expected exactly one top-level application')
        plist = plistlib.loads(read_member(archive, apps[0], 4 * 1024 * 1024))
        executable = plist.get('CFBundleExecutable')
        require(isinstance(executable, str) and executable and '/' not in executable and '\\' not in executable and executable not in ('.', '..'), 'invalid executable name')
        app_path = apps[0].rsplit('/', 1)[0]
        binary = read_member(archive, app_path + '/' + executable, 512 * 1024 * 1024)
        info = inspect_macho(binary)
        _, preflight = append_dependency(binary, DEPENDENCY)
        target = {'ipa_sha256': sha256_file(path), 'ipa_size': path.stat().st_size,
                  'bundle_identifier': plist.get('CFBundleIdentifier'), 'version': plist.get('CFBundleShortVersionString'),
                  'build': plist.get('CFBundleVersion'), 'executable': executable, 'app_path': app_path,
                  'main_sha256': hashlib.sha256(binary).hexdigest(), 'main_uuid': info['uuid'],
                  'minimum_os_version': plist.get('MinimumOSVersion', info['minimum_os_version'])}
        return {'schema_version': 1, 'target': target, 'macho': info, 'preflight': preflight,
                'zip_members': len(infos), 'zip_uncompressed_bytes': sum(i.file_size for i in infos)}


def version_tuple(text):
    parts = text.split('.')
    require(1 <= len(parts) <= 3 and all(x.isdigit() for x in parts), 'invalid OS version')
    return tuple(int(x) for x in parts) + (0,) * (3 - len(parts))


def validate_framework(path, target_document=None):
    path = Path(path)
    require(path.is_dir() and not path.is_symlink() and path.name == FRAMEWORK, 'invalid framework directory')
    target_document = load_target() if target_document is None else target_document
    files, total = {}, 0
    for child in sorted(path.rglob('*')):
        require(not child.is_symlink(), 'framework symlink refused')
        require(child.is_file() or child.is_dir(), 'framework special file refused')
        relative = child.relative_to(path).as_posix()
        checked_name(relative)
        if child.is_file():
            size = child.stat().st_size
            total += size
            require(size <= 32 * 1024 * 1024 and total <= 64 * 1024 * 1024 and len(files) < 128, 'framework size/count limit exceeded')
            files[relative] = {'sha256': sha256_file(child), 'size': size, 'mode': stat.S_IMODE(child.stat().st_mode)}
    require({'Info.plist', EXECUTABLE, 'Target.json', 'AssetIndex.json'} <= set(files), 'framework missing required file')
    plist = plistlib.loads((path / 'Info.plist').read_bytes())
    expected = {'CFBundleExecutable': EXECUTABLE, 'CFBundleIdentifier': 'com.sdktools.lasercapture',
                'CFBundlePackageType': 'FMWK', 'CFBundleShortVersionString': '0.1.0', 'CFBundleVersion': '1'}
    for key, value in expected.items():
        require(plist.get(key) == value, 'framework plist mismatch: ' + key)
    require(version_tuple(plist.get('MinimumOSVersion', '')) == (14, 0, 0), 'framework must target minimum iOS 14.0')
    require(plist.get('CFBundleSupportedPlatforms') == ['iPhoneOS'], 'framework plist platform must be iPhoneOS')
    require(files[EXECUTABLE]['mode'] & 0o111, 'framework executable permission is missing')
    packaged_target = json.loads((path / 'Target.json').read_text())
    require(packaged_target == target_document, 'framework target manifest differs from source target')
    asset_reference = ROOT / 'resources/AssetIndex.json'
    require(asset_reference.is_file() and asset_reference.stat().st_size <= 1024 * 1024,
            'missing/oversized source asset index')
    require((path / 'AssetIndex.json').read_bytes() == asset_reference.read_bytes(),
            'framework asset index differs from reviewed source asset index')
    binary = (path / EXECUTABLE).read_bytes()
    info = inspect_macho(binary)
    require(info['file_type'] == 6 and info['platform'] == 2, 'framework must be an iOS MH_DYLIB')
    require(version_tuple(info['minimum_os_version']) == (14, 0, 0), 'framework Mach-O minimum OS mismatch')
    require(info['install_name'] == '@rpath/' + FRAMEWORK + '/' + EXECUTABLE, 'framework install name mismatch')
    require(all(x['cryptid'] == 0 for x in info['encryption']), 'framework declares active encryption')
    require(any(s['segment'] == '__TEXT' and s['name'] == '__text' and s['size'] > 0 for s in info['sections']), 'framework has no executable text')
    require(all(d['name'].startswith(('/System/Library/', '/usr/lib/')) for d in info['dependencies']), 'framework has unresolved non-system dependencies')
    return {'macho': info, 'files': files, 'total_bytes': total}


def _fingerprint(path):
    s = Path(path).stat()
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)


def _stream_member(source, info, destination):
    digest, count = hashlib.sha256(), 0
    with source.open(info) as reader:
        while True:
            block = reader.read(BLOCK)
            if not block:
                break
            count += len(block)
            require(count <= info.file_size and count <= MAX_MEMBER, 'stream exceeds declared size')
            digest.update(block)
            if destination is not None:
                destination.write(block)
    require(count == info.file_size, 'stream length mismatch')
    return digest.hexdigest()


def package(base, framework, output, target_document=None):
    base, framework, output = Path(base), Path(framework), Path(output)
    document = load_target() if target_document is None else target_document
    target = document['target']
    report_path = output.with_name(output.name + '.report.json')
    require(not output.exists() and not report_path.exists() and not output.is_symlink() and not report_path.is_symlink(), 'output or report already exists')
    require(base.is_file() and not base.is_symlink(), 'base must be a regular file')
    base_fingerprint = _fingerprint(base)
    require(base.stat().st_size == target['ipa_size'] and sha256_file(base) == target['ipa_sha256'], 'base IPA does not match pinned size/hash')
    fw = validate_framework(framework, document)
    output.parent.mkdir(parents=True, exist_ok=True)
    main_name = target['app_path'] + '/' + target['executable']
    prefix = target['app_path'] + '/Frameworks/' + FRAMEWORK + '/'
    signature_prefix = target['app_path'] + '/_CodeSignature/'
    published = []
    with tempfile.TemporaryDirectory(prefix='.sdk-laser-package-', dir=output.parent) as temporary:
        temp_ipa, temp_report = Path(temporary) / 'new.ipa', Path(temporary) / 'report.json'
        expected, original_metadata, removed = {}, {}, []
        with zipfile.ZipFile(base) as source:
            infos = validate_zip(source)
            require(not any(i.filename.casefold().startswith(prefix.casefold()) for i in infos), 'collector framework already exists in base')
            plist = plistlib.loads(read_member(source, target['app_path'] + '/Info.plist', 4 * 1024 * 1024))
            for field, key in [('CFBundleIdentifier', 'bundle_identifier'), ('CFBundleShortVersionString', 'version'), ('CFBundleVersion', 'build'), ('CFBundleExecutable', 'executable')]:
                require(plist.get(field) == target[key], 'base identity mismatch: ' + key)
            original_main = read_member(source, main_name, 512 * 1024 * 1024)
            require(hashlib.sha256(original_main).hexdigest() == target['main_sha256'], 'main executable hash mismatch')
            info = inspect_macho(original_main)
            require(info['uuid'] == target['main_uuid'], 'main executable UUID mismatch')
            require(version_tuple(plist.get('MinimumOSVersion', '')) == version_tuple(target['minimum_os_version']), 'base minimum OS mismatch')
            patched_main, patch_report = append_dependency(original_main, DEPENDENCY)
            with zipfile.ZipFile(temp_ipa, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as destination:
                destination.comment = source.comment
                for entry in infos:
                    if entry.filename.startswith(signature_prefix) or entry.filename == signature_prefix.rstrip('/'):
                        removed.append({'path': entry.filename, 'sha256': _stream_member(source, entry, None)})
                        continue
                    original_metadata[entry.filename] = (entry.external_attr, entry.date_time, entry.comment, entry.extra, entry.create_system, entry.compress_type)
                    copied = copy.copy(entry)
                    with destination.open(copied, 'w', force_zip64=True) as writer:
                        if entry.filename == main_name:
                            writer.write(patched_main)
                            digest = hashlib.sha256(patched_main).hexdigest()
                        else:
                            digest = _stream_member(source, entry, writer)
                    expected[entry.filename] = digest
                for relative, record in fw['files'].items():
                    content = (framework / relative).read_bytes()
                    require(hashlib.sha256(content).hexdigest() == record['sha256'], 'framework changed during packaging')
                    name = prefix + relative
                    item = zipfile.ZipInfo(name, (2026, 10, 21, 0, 0, 0))
                    item.create_system = 3
                    item.external_attr = (stat.S_IFREG | record['mode']) << 16
                    item.compress_type = zipfile.ZIP_DEFLATED
                    destination.writestr(item, content)
                    expected[name] = record['sha256']
        require(_fingerprint(base) == base_fingerprint, 'base changed during packaging')
        require(validate_framework(framework, document) == fw, 'framework changed during packaging')
        with zipfile.ZipFile(temp_ipa) as check:
            check_infos = validate_zip(check)
            require(set(i.filename for i in check_infos) == set(expected), 'output member set mismatch')
            for entry in check_infos:
                require(_stream_member(check, entry, None) == expected[entry.filename], 'output content verification failed')
                if entry.filename in original_metadata:
                    actual = (entry.external_attr, entry.date_time, entry.comment, entry.extra, entry.create_system, entry.compress_type)
                    require(actual == original_metadata[entry.filename], 'unrelated ZIP metadata changed')
            require(inspect_macho(read_member(check, main_name, 512 * 1024 * 1024))['dependencies'][-1]['name'] == DEPENDENCY, 'output dylib load command missing')
        report = {'schema': 'SDKLaserCapture.PackageReport', 'schema_version': 1, 'module_version': '0.1.0',
                  'base': target, 'framework': fw, 'main_patch': patch_report,
                  'main_sha256_after': hashlib.sha256(patched_main).hexdigest(),
                  'removed_stale_signature_metadata': removed, 'added_files': [prefix + x for x in fw['files']],
                  'unchanged_members_verified': len(original_metadata) - 1,
                  'output': {'filename': output.name, 'size': temp_ipa.stat().st_size, 'sha256': sha256_file(temp_ipa)},
                  'unsigned': True, 'requires_full_resigning': True, 'device_runtime_tested': False,
                  'limitations': ['Existing executable signature blobs become stale; re-sign app and nested code.',
                                  'Protection components and existing frameworks are preserved.',
                                  'Structural verification does not establish installation or runtime compatibility.']}
        temp_report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        try:
            # Hard links create atomically and refuse a racing existing destination.
            os.link(temp_ipa, output); published.append(output)
            os.link(temp_report, report_path); published.append(report_path)
        except BaseException:
            for path in reversed(published):
                path.unlink(missing_ok=True)
            raise
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--framework', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--target', type=Path)
    parser.add_argument('--inspect', action='store_true', help='read-only identity and header preflight, no framework required')
    args = parser.parse_args()
    if args.inspect:
        print(json.dumps(inspect_base(args.base), indent=2))
        return
    parser.error('--framework and --output are required unless --inspect') if not args.framework or not args.output else None
    report = package(args.base, args.framework, args.output, load_target(args.target))
    print(json.dumps({'ipa': str(args.output), 'report': str(args.output) + '.report.json', 'sha256': report['output']['sha256'], 'unsigned': True}))


if __name__ == '__main__':
    main()
