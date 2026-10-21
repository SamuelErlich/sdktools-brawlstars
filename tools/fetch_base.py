#!/usr/bin/env python3
"""Fetch only the exact pinned IPA. No target code is executed and no URL is logged."""
from __future__ import annotations

import argparse
import hashlib
import html.parser
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DRIVE_ID = '1MPjR6cm79C_OLsCHtjX0PPXkQ66onpPo'
DEFAULT_URL = f'https://drive.google.com/file/d/{DEFAULT_DRIVE_ID}/view?usp=sharing'
MAX_HTML = 2 * 1024 * 1024
CHUNK = 1024 * 1024
MAX_IPA = 3 * 1024 * 1024 * 1024


class DownloadError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise DownloadError(message)


def https_url(url: str) -> str:
    require(isinstance(url, str) and 0 < len(url) <= 8192, 'Invalid download address')
    require(not any(ord(c) < 32 or ord(c) == 127 for c in url), 'Invalid download address')
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password,
            'Only HTTPS addresses without embedded credentials are accepted')
    require(parsed.port in (None, 443), 'Only standard HTTPS port is accepted')
    return url


def normalize_url(url: str) -> tuple[str, str | None]:
    parsed = urllib.parse.urlsplit(https_url(url))
    drive_id = None
    if parsed.hostname == 'drive.google.com':
        match = re.fullmatch(r'/file/d/([A-Za-z0-9_-]+)/view', parsed.path)
        if match:
            drive_id = match.group(1)
        elif parsed.path in ('/uc', '/open'):
            ids = urllib.parse.parse_qs(parsed.query).get('id', [])
            if len(ids) == 1 and re.fullmatch(r'[A-Za-z0-9_-]+', ids[0]):
                drive_id = ids[0]
        require(drive_id, 'Unsupported Drive address format')
        url = 'https://drive.usercontent.google.com/download?' + urllib.parse.urlencode({'id': drive_id, 'export': 'download'})
    elif parsed.hostname == 'drive.usercontent.google.com':
        ids = urllib.parse.parse_qs(parsed.query).get('id', [])
        if len(ids) == 1 and re.fullmatch(r'[A-Za-z0-9_-]+', ids[0]):
            drive_id = ids[0]
    return url, drive_id


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ConfirmationParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.current = None
        self.blocked = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        # Ignore inert CSS mentioning a badge; refuse actual login/CAPTCHA UI.
        classes = (attrs.get('class', '') + ' ' + attrs.get('id', '')).lower()
        source = (attrs.get('src', '') + ' ' + attrs.get('action', '')).lower()
        if (tag in ('iframe', 'script') and ('recaptcha' in source or 'hcaptcha' in source)) or \
           (tag in ('div', 'form') and any(x in classes.split() for x in ('g-recaptcha', 'h-captcha'))) or \
           (tag == 'input' and attrs.get('type', '').lower() == 'password') or \
           ('accounts.google.com' in source):
            self.blocked = True
        if tag == 'form':
            require(self.current is None, 'Nested confirmation forms are not accepted')
            self.current = {'id': attrs.get('id'), 'action': attrs.get('action', ''),
                            'method': attrs.get('method', 'get').lower(), 'fields': {}}
        elif tag == 'input' and self.current is not None and attrs.get('type', '').lower() == 'hidden':
            name = attrs.get('name', '')
            require(name and name not in self.current['fields'], 'Duplicate or unnamed confirmation field')
            self.current['fields'][name] = attrs.get('value', '')

    def handle_endtag(self, tag):
        if tag == 'form' and self.current is not None:
            self.forms.append(self.current)
            self.current = None


def confirmation_url(body: bytes, drive_id: str | None) -> str:
    require(drive_id is not None, 'Unexpected HTML response; no automatic login is available')
    require(len(body) <= MAX_HTML, 'Confirmation page exceeds its byte limit')
    parser = ConfirmationParser()
    parser.feed(body.decode('utf-8', 'strict'))
    require(not parser.blocked, 'Download needs login or CAPTCHA; use an accessible base URL')
    candidates = [f for f in parser.forms if f['id'] == 'download-form']
    require(len(candidates) == 1, 'Expected a single ordinary Drive download confirmation')
    form = candidates[0]
    action = urllib.parse.urlsplit(https_url(form['action']))
    require(action.hostname == 'drive.usercontent.google.com' and action.path == '/download' and
            not action.query and not action.fragment and form['method'] == 'get', 'Unsupported confirmation destination')
    fields = form['fields']
    require(set(fields) <= {'id', 'export', 'confirm', 'uuid'} and
            fields.get('id') == drive_id and fields.get('export') == 'download' and
            fields.get('confirm') == 't', 'Unexpected confirmation fields')
    require(all(len(v) <= 256 and not any(ord(c) < 32 for c in v) for v in fields.values()), 'Invalid confirmation value')
    return urllib.parse.urlunsplit(('https', 'drive.usercontent.google.com', '/download', urllib.parse.urlencode(fields), ''))


def read_target(path: Path) -> dict:
    data = json.loads(path.read_text(encoding='utf-8'))
    require(data.get('schema_version') == 1 and isinstance(data.get('target'), dict), 'Unsupported target manifest')
    target = data['target']
    require(isinstance(target.get('ipa_size'), int) and not isinstance(target['ipa_size'], bool) and
            0 < target['ipa_size'] <= MAX_IPA, 'Invalid pinned IPA size')
    require(isinstance(target.get('ipa_sha256'), str) and re.fullmatch(r'[a-f0-9]{64}', target['ipa_sha256']), 'Invalid pinned IPA hash')
    return target


def validate_ipa(path: Path, target: dict) -> dict:
    require(path.is_file() and not path.is_symlink(), 'Base input is not a regular file')
    require(path.stat().st_size == target['ipa_size'], 'Base IPA size differs from the pinned target')
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while block := source.read(CHUNK):
            digest.update(block)
    require(digest.hexdigest() == target['ipa_sha256'], 'Base IPA SHA-256 differs from the pinned target')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        require(0 < len(entries) <= 50000, 'Invalid IPA entry count')
        require(sum(x.file_size for x in entries) <= 6 * 1024**3, 'IPA expands beyond the limit')
        require(all(0 <= x.file_size <= 3 * 1024**3 and not (x.flag_bits & 1) for x in entries), 'Unsupported IPA member')
        # No extraction takes place; full ZIP/path validation follows in package_ipa.
        for entry in entries:
            with archive.open(entry) as member:
                count = 0
                while block := member.read(CHUNK):
                    count += len(block)
                    require(count <= entry.file_size, 'IPA member exceeded its declared byte count')
                require(count == entry.file_size, 'Truncated IPA member')
    return {'size_bytes': path.stat().st_size, 'sha256': digest.hexdigest(), 'members_crc_checked': len(entries)}


def write_response(response, destination: Path, expected_size: int):
    require(getattr(response, 'status', 200) == 200, 'Expected a complete HTTP response')
    declared = response.headers.get('Content-Length')
    if declared:
        require(declared.isdigit() and int(declared) <= expected_size, 'Download exceeds the pinned byte limit')
    count = 0
    with destination.open('xb') as output:
        while block := response.read(CHUNK):
            count += len(block)
            require(count <= expected_size, 'Download exceeds the pinned byte limit')
            output.write(block)
        output.flush()
        os.fsync(output.fileno())
    require(count == expected_size, 'Download has an unexpected byte count')


def download(url: str, destination: Path, target: dict, opener=None):
    url, drive_id = normalize_url(url)
    opener = opener or urllib.request.build_opener(HTTPSRedirect())
    for attempt in range(2):
        request = urllib.request.Request(url, headers={'User-Agent': 'SDKLaserCapture-BaseFetcher/0.1.0', 'Accept-Encoding': 'identity'})
        with opener.open(request, timeout=60) as response:
            require(getattr(response, 'status', 200) == 200, 'Expected a complete HTTP response')
            https_url(response.geturl())
            content_type = response.headers.get('Content-Type', '').lower()
            if 'text/html' in content_type:
                require(attempt == 0, 'Repeated confirmation page; download stopped')
                body = response.read(MAX_HTML + 1)
                url = confirmation_url(body, drive_id)
                continue
            write_response(response, destination, target['ipa_size'])
            return
    raise DownloadError('The base IPA download did not complete')


def fetch(output: Path, target: dict, url: str | None = None, local: Path | None = None, opener=None):
    output = output.absolute()
    require(not output.exists() and not output.is_symlink(), 'Output already exists; use a new destination')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.laser-base-', dir=output.parent) as temporary:
        staged = Path(temporary) / 'base.ipa'
        if local is not None:
            validate_ipa(local, target)
            with local.open('rb') as source, staged.open('xb') as destination:
                shutil.copyfileobj(source, destination, CHUNK)
        else:
            download(url or DEFAULT_URL, staged, target, opener=opener)
        report = validate_ipa(staged, target)
        # Atomic publication without replacing any existing destination.
        os.link(staged, output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--url', help='HTTPS base IPA address; defaults to BASE_URL or the supplied Drive file')
    source.add_argument('--local', type=Path, help='Copy and verify an existing local base IPA')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--target', type=Path, default=ROOT / 'resources/Target.json')
    args = parser.parse_args()
    try:
        result = fetch(args.output, read_target(args.target),
                       url=args.url or os.environ.get('BASE_URL', '').strip() or DEFAULT_URL, local=args.local)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, urllib.error.URLError):
        # Exception strings from HTTP libraries may contain signed URLs or tokens.
        print('Base download/verification failed. Check accessible HTTPS URL, free disk space, exact pinned IPA and a new output path. No output was published.', file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
