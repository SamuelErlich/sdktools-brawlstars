#!/usr/bin/env python3
"""Strict, read-only parser for the thin ARM64 Mach-O contract used by this project."""
from __future__ import annotations

import struct
import uuid

MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x100000C
LC_LOAD_DYLIB = 0xC
DYLIB_COMMANDS = {0xC, 0x18 | 0x80000000, 0x1F | 0x80000000, 0x20, 0x23 | 0x80000000}
LINKEDIT_COMMANDS = {0x1D, 0x1E, 0x26, 0x29, 0x2B, 0x2E, 0x33 | 0x80000000, 0x34 | 0x80000000, 0x36, 0x38}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def version_string(value):
    return '.'.join(str(x) for x in (value >> 16, (value >> 8) & 255, value & 255))


def _fixed_name(raw):
    return raw.split(b'\0', 1)[0].decode('utf-8', 'strict')


def inspect_macho(data: bytes) -> dict:
    require(len(data) >= 32, 'truncated Mach-O header')
    magic, cpu, subtype, kind, ncmds, sizeofcmds, flags, reserved = struct.unpack_from('<8I', data)
    require(magic == MH_MAGIC_64, 'only thin little-endian Mach-O 64 is supported; FAT is not modified')
    require(cpu == CPU_TYPE_ARM64 and subtype == 0, 'only ARM64 ALL subtype is supported; ARM64e is not modified')
    require(0 < ncmds <= 4096 and sizeofcmds <= 1024 * 1024, 'invalid load-command count/size')
    header_end = 32 + sizeofcmds
    require(header_end <= len(data), 'load commands extend beyond file')
    result = {'architecture': 'arm64', 'cpu_subtype': subtype, 'file_type': kind,
              'ncmds': ncmds, 'sizeofcmds': sizeofcmds, 'header_end': header_end,
              'uuid': None, 'dependencies': [], 'install_name': None, 'platform': None,
              'minimum_os_version': None, 'encryption': [], 'segments': [], 'sections': [],
              'code_signatures': [], 'flags': flags}
    ranges = []

    def extent(offset, size, label):
        require(0 <= offset <= len(data) and 0 <= size <= len(data) - offset, 'out-of-file range: ' + label)
        if size:
            ranges.append((offset, size, label))

    pos = 32
    for _ in range(ncmds):
        require(pos + 8 <= header_end, 'truncated load command')
        cmd, size = struct.unpack_from('<II', data, pos)
        require(size >= 8 and size % 8 == 0 and size <= header_end - pos, 'invalid load-command size/alignment')
        body = data[pos:pos + size]
        if cmd == 0x19:  # LC_SEGMENT_64
            require(size >= 72, 'truncated segment command')
            _, _, name, vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, segflags = struct.unpack_from('<II16sQQQQIIII', body)
            require(nsects <= (size - 72) // 80 and size == 72 + nsects * 80, 'invalid section table')
            require(fileoff <= len(data) and filesize <= len(data) - fileoff, 'segment exceeds file')
            segname = _fixed_name(name)
            result['segments'].append({'name': segname, 'vmaddr': vmaddr, 'vmsize': vmsize, 'fileoff': fileoff, 'filesize': filesize})
            if fileoff and filesize:
                extent(fileoff, filesize, 'segment ' + segname)
            for j in range(nsects):
                sec = struct.unpack_from('<16s16sQQIIIIIIII', body, 72 + j * 80)
                sname, owner, address, count, offset, align, reloff, nreloc, sflags, r1, r2, r3 = sec
                section = {'name': _fixed_name(sname), 'segment': _fixed_name(owner), 'address': address,
                           'size': count, 'offset': offset, 'flags': sflags}
                require(section['segment'] == segname, 'section segment name mismatch')
                require(vmaddr <= address <= vmaddr + vmsize and count <= vmaddr + vmsize - address, 'section exceeds virtual segment')
                if sflags & 0xFF not in {1, 0xC, 0x12} and count:
                    require(fileoff <= offset and count <= fileoff + filesize - offset, 'section exceeds file-backed segment')
                    extent(offset, count, 'section ' + section['name'])
                if nreloc:
                    extent(reloff, nreloc * 8, 'section relocations')
                result['sections'].append(section)
        elif cmd == 0x1B:
            require(size == 24 and result['uuid'] is None, 'invalid/duplicate UUID command')
            result['uuid'] = str(uuid.UUID(bytes=bytes(body[8:24])))
        elif cmd == 0x32:
            require(size >= 24 and result['platform'] is None, 'invalid/duplicate build version')
            platform, minimum, sdk, ntools = struct.unpack_from('<4I', body, 8)
            require(size == 24 + ntools * 8, 'invalid build tool list')
            result.update(platform=platform, minimum_os_version=version_string(minimum), sdk_version=version_string(sdk))
        elif cmd in {0x24, 0x25, 0x2F, 0x30}:
            require(size == 16 and result['platform'] is None, 'invalid/duplicate minimum OS command')
            minimum, sdk = struct.unpack_from('<II', body, 8)
            result.update(platform={0x24: 1, 0x25: 2, 0x2F: 3, 0x30: 4}[cmd], minimum_os_version=version_string(minimum), sdk_version=version_string(sdk))
        elif cmd == 0xD or cmd in DYLIB_COMMANDS:
            require(size >= 32, 'truncated dylib command')
            offset, stamp, current, compatibility = struct.unpack_from('<4I', body, 8)
            require(24 <= offset < size and b'\0' in body[offset:], 'invalid dylib name')
            name = body[offset:].split(b'\0', 1)[0].decode('utf-8', 'strict')
            require(name and all(ord(c) >= 32 for c in name), 'invalid dylib path')
            if cmd == 0xD:
                require(result['install_name'] is None, 'duplicate install name')
                result['install_name'] = name
            else:
                result['dependencies'].append({'command': cmd, 'name': name})
        elif cmd in {0x21, 0x2C}:
            require(size == (24 if cmd == 0x2C else 24), 'invalid encryption command')
            offset, count, cryptid = struct.unpack_from('<3I', body, 8)
            extent(offset, count, 'encrypted range')
            result['encryption'].append({'cryptoff': offset, 'cryptsize': count, 'cryptid': cryptid})
        elif cmd == 0x2:
            require(size == 24, 'invalid symtab command')
            symoff, nsyms, stroff, strsize = struct.unpack_from('<4I', body, 8)
            extent(symoff, nsyms * 16, 'symbols'); extent(stroff, strsize, 'strings')
        elif cmd == 0xB:
            require(size == 80, 'invalid dysymtab command')
            values = struct.unpack_from('<18I', body, 8)
            for i, unit in ((6, 8), (8, 56), (10, 4), (12, 4), (14, 8), (16, 8)):
                extent(values[i], values[i + 1] * unit, 'dynamic symbol data')
        elif cmd in {0x22, 0x22 | 0x80000000}:
            require(size == 48, 'invalid dyld-info command')
            values = struct.unpack_from('<10I', body, 8)
            for j in range(0, 10, 2):
                extent(values[j], values[j + 1], 'dyld data')
        elif cmd in LINKEDIT_COMMANDS:
            require(size == 16, 'invalid linkedit data command')
            offset, count = struct.unpack_from('<II', body, 8)
            extent(offset, count, 'linkedit data')
            if cmd == 0x1D:
                result['code_signatures'].append({'dataoff': offset, 'datasize': count})
        elif cmd == (0x28 | 0x80000000):
            require(size == 24, 'invalid main-entry command')
            entryoff, stacksize = struct.unpack_from('<QQ', body, 8)
            extent(entryoff, 1, 'entry point')
        elif cmd == 0x16:
            require(size == 16, 'invalid two-level hints command')
            offset, count = struct.unpack_from('<II', body, 8)
            extent(offset, count * 4, 'two-level hints')
        pos += size
    require(pos == header_end, 'load-command size/count mismatch')
    require(result['uuid'] and result['uuid'] != '00000000-0000-0000-0000-000000000000', 'missing or zero UUID')
    require(result['segments'] and result['sections'], 'Mach-O has no sections')
    for offset, count, label in ranges:
        require(offset >= header_end, 'data overlaps Mach-O header: ' + label)
    result['first_file_data_offset'] = min((x[0] for x in ranges), default=len(data))
    result['available_header_padding'] = result['first_file_data_offset'] - header_end
    return result


def append_dependency(data: bytes, install_name: str) -> tuple[bytes, dict]:
    info = inspect_macho(data)
    require(info['file_type'] == 2, 'base executable must be MH_EXECUTE')
    require(info['platform'] == 2, 'base executable must target iOS')
    require(all(x['cryptid'] == 0 for x in info['encryption']), 'base executable declares active encryption')
    require(install_name not in [x['name'] for x in info['dependencies']], 'collector dependency already present')
    name = install_name.encode('utf-8') + b'\0'
    size = (24 + len(name) + 7) & ~7
    start, end = info['header_end'], info['header_end'] + size
    require(end <= info['first_file_data_offset'], 'insufficient header padding; relocation is not supported')
    require(data[start:end] == bytes(size), 'header padding is not zero')
    command = struct.pack('<6I', LC_LOAD_DYLIB, size, 24, 0, 0x100, 0x100) + name
    command += bytes(size - len(command))
    patched = bytearray(data)
    struct.pack_into('<II', patched, 16, info['ncmds'] + 1, info['sizeofcmds'] + size)
    patched[start:end] = command
    after = inspect_macho(bytes(patched))
    require(after['dependencies'][:-1] == info['dependencies'], 'dependency ordinal preservation failed')
    require(after['segments'] == info['segments'] and after['sections'] == info['sections'], 'segment/section preservation failed')
    require(patched[:16] == data[:16] and patched[24:start] == data[24:start] and patched[end:] == data[end:], 'unapproved byte change')
    return bytes(patched), {'load_command': 'LC_LOAD_DYLIB', 'install_name': install_name,
                           'changed_byte_ranges': [[16, 24], [start, end]], 'new_command_bytes': size,
                           'first_file_data_offset': info['first_file_data_offset'],
                           'original_ncmds': info['ncmds'], 'new_ncmds': after['ncmds'],
                           'original_sizeofcmds': info['sizeofcmds'], 'new_sizeofcmds': after['sizeofcmds'],
                           'existing_code_signature_blobs': info['code_signatures'],
                           'existing_signatures_valid_after_change': False}
