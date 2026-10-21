#!/usr/bin/env python3
"""Compile the real unsigned iPhoneOS framework using Xcode's Apple toolchain."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK = 'SDKLaserCapture'
VERSION = '0.1.0'
MIN_IOS = '14.0'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist', help='New output directory; existing paths are refused')
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        print('Build refused: macOS, Xcode and the iPhoneOS SDK are required. No framework was built.', file=sys.stderr)
        return 2
    if not shutil.which('xcrun'):
        print('Build refused: xcrun is unavailable; select a complete Xcode installation.', file=sys.stderr)
        return 2
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        print('Build refused: output already exists; choose a new directory.', file=sys.stderr)
        return 2
    source_paths = sorted((ROOT / 'Sources').glob('*.m'))
    headers = sorted((ROOT / 'Sources').glob('*.h'))
    resources = [ROOT / 'resources/Target.json', ROOT / 'resources/AssetIndex.json']
    if not source_paths or any(not p.is_file() or p.is_symlink() for p in source_paths + headers + resources):
        print('Build refused: source files or target resource are missing/invalid.', file=sys.stderr)
        return 2
    inputs = {p.relative_to(ROOT).as_posix(): {'sha256': sha(p), 'size_bytes': p.stat().st_size} for p in source_paths + headers + resources}
    logs = ROOT / 'work/build-logs'
    logs.mkdir(parents=True, exist_ok=True)
    build_id = uuid.uuid4().hex
    log_path = logs / f'build-{build_id}.log'
    report_path = logs / f'build-{build_id}.json'
    report = {'project': FRAMEWORK, 'version': VERSION, 'build_id': build_id,
              'started_utc': datetime.now(timezone.utc).isoformat(), 'host': platform.platform(),
              'inputs': inputs, 'commands': [], 'status': 'running', 'signed': False, 'device_tested': False}
    try:
        with log_path.open('x', encoding='utf-8') as log:
            def run(argv, required=True):
                completed = subprocess.run([str(x) for x in argv], cwd=ROOT, text=True, capture_output=True)
                report['commands'].append({'argv': [str(x) for x in argv], 'exit_code': completed.returncode})
                log.write(json.dumps([str(x) for x in argv]) + '\n')
                log.write(completed.stdout + completed.stderr + '\n')
                log.flush()
                if required and completed.returncode:
                    raise RuntimeError('An Apple tool failed; inspect the build log')
                return completed

            clang = run(['xcrun', '--sdk', 'iphoneos', '--find', 'clang']).stdout.strip()
            sdk = run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path']).stdout.strip()
            report['sdk_version'] = run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-version']).stdout.strip()
            report['xcode_version'] = run(['xcodebuild', '-version']).stdout.strip()
            report['clang_version'] = run([clang, '--version']).stdout.strip()
            if 'Apple clang' not in report['clang_version'] or not Path(sdk).is_dir():
                raise RuntimeError('Expected Apple clang and the iPhoneOS SDK')
            output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.laser-build-', dir=output.parent) as temp:
                stage = Path(temp) / 'product'
                framework = stage / f'{FRAMEWORK}.framework'
                framework.mkdir(parents=True)
                objects = []
                common = ['-arch', 'arm64', '-isysroot', sdk, '-miphoneos-version-min=' + MIN_IOS,
                          '-fobjc-arc', '-fblocks', '-g', '-Wall', '-Wextra',
                          '-Werror=implicit-function-declaration', '-Werror=incompatible-pointer-types',
                          '-Werror=return-type', '-Wno-deprecated-declarations', '-I', str(ROOT / 'Sources')]
                for index, source in enumerate(source_paths):
                    obj = Path(temp) / f'{index}-{source.stem}.o'
                    run([clang, *common, '-c', str(source), '-o', str(obj)])
                    objects.append(obj)
                binary = framework / FRAMEWORK
                run([clang, '-arch', 'arm64', '-isysroot', sdk, '-miphoneos-version-min=' + MIN_IOS,
                     '-dynamiclib', '-Wl,-no_adhoc_codesign', '-install_name', f'@rpath/{FRAMEWORK}.framework/{FRAMEWORK}',
                     '-current_version', VERSION, '-compatibility_version', VERSION,
                     *objects, '-framework', 'UIKit', '-framework', 'Foundation', '-framework', 'QuartzCore',
                     '-lobjc', '-o', binary])
                binary.chmod(0o755)
                metadata = {'CFBundleExecutable': FRAMEWORK, 'CFBundleName': FRAMEWORK,
                            'CFBundleIdentifier': 'com.sdktools.lasercapture', 'CFBundlePackageType': 'FMWK',
                            'CFBundleShortVersionString': VERSION, 'CFBundleVersion': '1',
                            'CFBundleSupportedPlatforms': ['iPhoneOS'], 'MinimumOSVersion': MIN_IOS}
                (framework / 'Info.plist').write_bytes(plistlib.dumps(metadata))
                for resource in resources:
                    shutil.copyfile(resource, framework / resource.name)
                from package_ipa import validate_framework
                report['framework_validation'] = validate_framework(framework)
                dsym = run(['xcrun', '--find', 'dsymutil'], required=False)
                if dsym.returncode == 0:
                    result = run([dsym.stdout.strip(), binary, '-o', stage / f'{FRAMEWORK}.framework.dSYM'], required=False)
                    report['dsym'] = 'created' if result.returncode == 0 else 'failed_optional'
                    if result.returncode:
                        shutil.rmtree(stage / f'{FRAMEWORK}.framework.dSYM', ignore_errors=True)
                else:
                    report['dsym'] = 'tool_unavailable'
                if any(sha(ROOT / name) != entry['sha256'] for name, entry in inputs.items()):
                    raise RuntimeError('Source changed during compilation; output not published')
                report['status'] = 'compiled_and_structurally_validated'
                report['finished_utc'] = datetime.now(timezone.utc).isoformat()
                (stage / 'buildreport.json').write_text(json.dumps(report, indent=2) + '\n')
                log.flush()
                shutil.copyfile(log_path, stage / 'build.log')
                if output.exists() or output.is_symlink():
                    raise RuntimeError('Output appeared during build; publication stopped')
                os.rename(stage, output)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        report['status'] = 'failed'
        report['failure'] = type(error).__name__
        report_path.write_text(json.dumps(report, indent=2) + '\n')
        print(f'Build failed. Review {log_path.relative_to(ROOT)} and its JSON report.', file=sys.stderr)
        return 2
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    print(f'Unsigned framework compiled and validated: {output / (FRAMEWORK + ".framework")}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
