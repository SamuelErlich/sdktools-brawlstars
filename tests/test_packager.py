"""Portable fixtures exercise binary guards; none of these bytes is a deliverable framework."""
import hashlib
import json
from pathlib import Path
import plistlib
import stat
import struct
import sys
import tempfile
import unittest
from unittest import mock
import uuid
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import macho
import package_ipa as pack


def dylib(name, command=0xC):
    raw = name.encode() + b'\0'
    size = (24 + len(raw) + 7) & ~7
    return struct.pack('<6I', command, size, 24, 0, 0x100, 0x100) + raw + bytes(size - 24 - len(raw))


def native(kind=2, platform=2, encrypted=0, subtype=0):
    section = struct.pack('<16s16sQQIIIIIIII', b'__text', b'__TEXT', 0x100001000, 16, 4096, 2, 0, 0, 0x80000400, 0, 0, 0)
    segment = struct.pack('<II16sQQQQIIII', 0x19, 152, b'__TEXT', 0x100000000, 8192, 0, 8192, 7, 5, 1, 0) + section
    commands = [segment, struct.pack('<II16s', 0x1B, 24, uuid.UUID('8a5d0b64-1201-45cd-a952-ff77acb8aed6').bytes),
                struct.pack('<6I', 0x32, 24, platform, 14 << 16, 18 << 16, 0),
                dylib('/usr/lib/libobjc.A.dylib'), struct.pack('<6I', 0x2C, 24, 4096, 16, encrypted, 0)]
    if kind == 6:
        commands.append(dylib('@rpath/SDKLaserCapture.framework/SDKLaserCapture', 0xD))
    else:
        commands.append(struct.pack('<IIQQ', 0x28 | 0x80000000, 24, 4096, 0))
    header = struct.pack('<8I', 0xFEEDFACF, 0x100000C, subtype, kind, len(commands), sum(map(len, commands)), 0, 0)
    data = bytearray(8192)
    data[:len(header)] = header
    data[len(header):len(header) + sum(map(len, commands))] = b''.join(commands)
    data[4096:4112] = b'\xC0\x03\x5F\xD6' * 4
    return bytes(data)


def entry(name, content, mode=stat.S_IFREG | 0o644):
    item = zipfile.ZipInfo(name, (2026, 10, 21, 0, 0, 0))
    item.create_system = 3
    item.external_attr = mode << 16
    item.comment = b'fixture-entry'
    item.compress_type = zipfile.ZIP_DEFLATED
    return item, content


class BinaryGuards(unittest.TestCase):
    def test_only_header_count_and_new_command_are_modified(self):
        base = native()
        original = macho.inspect_macho(base)
        out, report = macho.append_dependency(base, pack.DEPENDENCY)
        parsed = macho.inspect_macho(out)
        self.assertEqual(parsed['dependencies'][:-1], original['dependencies'])
        self.assertEqual(parsed['sections'], original['sections'])
        self.assertEqual(len(out), len(base))
        permitted = {i for start, end in report['changed_byte_ranges'] for i in range(start, end)}
        self.assertTrue({i for i, pair in enumerate(zip(base, out)) if pair[0] != pair[1]} <= permitted)

    def test_nonzero_padding_and_repeat_injection_are_refused(self):
        data = bytearray(native())
        data[macho.inspect_macho(data)['header_end']] = 1
        with self.assertRaisesRegex(ValueError, 'not zero'):
            macho.append_dependency(bytes(data), pack.DEPENDENCY)
        patched, _ = macho.append_dependency(native(), pack.DEPENDENCY)
        with self.assertRaisesRegex(ValueError, 'already present'):
            macho.append_dependency(patched, pack.DEPENDENCY)

    def test_wrong_cpu_platform_encryption_and_fat_are_refused(self):
        for data in (native(subtype=2), native(platform=7), native(encrypted=1), b'\xca\xfe\xba\xbe' + native()[4:]):
            with self.subTest(data=data[:16]):
                with self.assertRaises(ValueError):
                    macho.append_dependency(data, pack.DEPENDENCY)

    def test_declared_ranges_cannot_point_into_header(self):
        data = bytearray(native())
        # The section's file offset sits 48 bytes into its section structure.
        struct.pack_into('<I', data, 32 + 72 + 48, 32)
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            macho.inspect_macho(bytes(data))

    def test_insufficient_padding_is_refused(self):
        data = bytearray(native())
        header_end = macho.inspect_macho(data)['header_end']
        struct.pack_into('<I', data, 32 + 72 + 48, header_end + 8)
        struct.pack_into('<Q', data, 32 + 72 + 32, 0x100000000 + header_end + 8)
        with self.assertRaisesRegex(ValueError, 'insufficient'):
            macho.append_dependency(bytes(data), pack.DEPENDENCY)

    def test_bad_load_command_size_is_refused(self):
        data = bytearray(native())
        struct.pack_into('<I', data, 36, 153)
        with self.assertRaisesRegex(ValueError, 'alignment'):
            macho.inspect_macho(data)


class ArchiveGuards(unittest.TestCase):
    def check_bad(self, members):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.zip'
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                with zipfile.ZipFile(path, 'w') as archive:
                    for info, content in members:
                        archive.writestr(info, content)
            with zipfile.ZipFile(path) as archive, self.assertRaises(ValueError):
                pack.validate_zip(archive)

    def test_traversal_absolute_backslash_and_control(self):
        for name in ('../escape', '/escape', 'dir\\escape', 'a/./b', 'a//b', 'a/\x01b'):
            with self.subTest(name=name):
                self.check_bad([entry(name, b'x')])

    def test_case_unicode_and_duplicate_collisions(self):
        for a, b in [('A', 'a'), ('é', 'e\u0301'), ('same', 'same')]:
            with self.subTest(a=a, b=b):
                self.check_bad([entry(a, b'x'), entry(b, b'y')])

    def test_symlinks_special_files_and_file_parent_conflict(self):
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600):
            self.check_bad([entry('a', b'target', mode)])
        self.check_bad([entry('a', b'x'), entry('a/b', b'y')])

    def test_declared_size_limit(self):
        with mock.patch.object(pack, 'MAX_MEMBER', 3):
            self.check_bad([entry('a', b'abcd')])


class PackageIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = self.root / 'base.ipa'
        self.output = self.root / 'out.ipa'
        self.framework = self.root / pack.FRAMEWORK
        self.framework.mkdir()
        (self.root / 'resources').mkdir()
        binary = native()
        app_plist = {'CFBundleIdentifier': 'com.example.fixture', 'CFBundleExecutable': 'fixture', 'CFBundleShortVersionString': '1.0', 'CFBundleVersion': '1', 'MinimumOSVersion': '14.0'}
        with zipfile.ZipFile(self.base, 'w') as archive:
            for info, content in [entry('Payload/Fixture.app/Info.plist', plistlib.dumps(app_plist)),
                                  entry('Payload/Fixture.app/fixture', binary, stat.S_IFREG | 0o755),
                                  entry('Payload/Fixture.app/assets/data.bin', b'preserve exactly'),
                                  entry('Payload/Fixture.app/_CodeSignature/CodeResources', b'stale signature'),
                                  entry('Payload/Fixture.app/Frameworks/Keep.framework/_CodeSignature/CodeResources', b'keep nested metadata')]:
                archive.writestr(info, content)
        self.document = {'schema_version': 1, 'target': {'ipa_sha256': pack.sha256_file(self.base), 'ipa_size': self.base.stat().st_size,
                         'bundle_identifier': app_plist['CFBundleIdentifier'], 'version': '1.0', 'build': '1', 'executable': 'fixture',
                         'app_path': 'Payload/Fixture.app', 'main_sha256': hashlib.sha256(binary).hexdigest(),
                         'main_uuid': macho.inspect_macho(binary)['uuid'], 'minimum_os_version': '14.0'}}
        (self.framework / pack.EXECUTABLE).write_bytes(native(kind=6))
        (self.framework / pack.EXECUTABLE).chmod(0o755)
        plist = {'CFBundleExecutable': pack.EXECUTABLE, 'CFBundleIdentifier': 'com.sdktools.lasercapture', 'CFBundlePackageType': 'FMWK',
                 'CFBundleShortVersionString': '0.1.0', 'CFBundleVersion': '1', 'MinimumOSVersion': '14.0', 'CFBundleSupportedPlatforms': ['iPhoneOS']}
        (self.framework / 'Info.plist').write_bytes(plistlib.dumps(plist))
        for root in [self.framework, self.root / 'resources']:
            (root / 'Target.json').write_text(json.dumps(self.document))
            (root / 'AssetIndex.json').write_text('{"schema_version":1,"assets":[]}\n')
        self.root_patch = mock.patch.object(pack, 'ROOT', self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def test_package_preserves_every_unrelated_member_and_original_input(self):
        before = self.base.read_bytes()
        report = pack.package(self.base, self.framework, self.output, self.document)
        self.assertTrue(report['unsigned'])
        self.assertTrue(self.output.with_name('out.ipa.report.json').is_file())
        self.assertEqual(self.base.read_bytes(), before)
        with zipfile.ZipFile(self.base) as original, zipfile.ZipFile(self.output) as output:
            for name in ['Payload/Fixture.app/Info.plist', 'Payload/Fixture.app/assets/data.bin', 'Payload/Fixture.app/Frameworks/Keep.framework/_CodeSignature/CodeResources']:
                self.assertEqual(original.read(name), output.read(name))
                self.assertEqual(original.getinfo(name).external_attr, output.getinfo(name).external_attr)
            self.assertNotIn('Payload/Fixture.app/_CodeSignature/CodeResources', output.namelist())
            self.assertIn('Payload/Fixture.app/Frameworks/SDKLaserCapture.framework/SDKLaserCapture', output.namelist())
        self.assertEqual(report['output']['sha256'], pack.sha256_file(self.output))

    def test_wrong_base_hash_fails_before_writing_output(self):
        self.document['target']['ipa_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'pinned'):
            pack.package(self.base, self.framework, self.output, self.document)
        self.assertFalse(self.output.exists())

    def test_framework_platform_install_name_and_resource_guards(self):
        original = (self.framework / pack.EXECUTABLE).read_bytes()
        (self.framework / pack.EXECUTABLE).write_bytes(native(kind=6, platform=7))
        with self.assertRaisesRegex(ValueError, 'iOS MH_DYLIB'):
            pack.validate_framework(self.framework, self.document)
        (self.framework / pack.EXECUTABLE).write_bytes(original)
        (self.framework / 'AssetIndex.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'asset index differs'):
            pack.validate_framework(self.framework, self.document)

    def test_framework_symlink_and_missing_execute_bit(self):
        (self.framework / 'link').symlink_to(self.base)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            pack.validate_framework(self.framework, self.document)
        (self.framework / 'link').unlink()
        (self.framework / pack.EXECUTABLE).chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'permission'):
            pack.validate_framework(self.framework, self.document)

    def test_refuses_overwrite_without_mutating_existing_file(self):
        self.output.write_bytes(b'existing')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            pack.package(self.base, self.framework, self.output, self.document)
        self.assertEqual(self.output.read_bytes(), b'existing')

    def test_report_publication_failure_rolls_back_new_ipa(self):
        original_link = pack.os.link
        calls = []
        def fail_second(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError('simulated report publication failure')
            original_link(src, dst)
        with mock.patch.object(pack.os, 'link', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'publication'):
                pack.package(self.base, self.framework, self.output, self.document)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_name('out.ipa.report.json').exists())

    def test_inspect_base_exposes_reproducible_preflight(self):
        report = pack.inspect_base(self.base)
        self.assertEqual(report['target'], self.document['target'])
        self.assertEqual(report['preflight']['new_ncmds'], report['preflight']['original_ncmds'] + 1)


if __name__ == '__main__':
    unittest.main()
