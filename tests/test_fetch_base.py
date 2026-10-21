import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import fetch_base as fetcher


def ipa_fixture():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('Payload/Test.app/Info.plist', b'test-only-fixture')
        archive.writestr('Payload/Test.app/Test', b'not-an-executable')
    return buffer.getvalue()


def target_for(body):
    return {'ipa_size': len(body), 'ipa_sha256': hashlib.sha256(body).hexdigest()}


def confirmation(extra=''):
    return ('''<html><style>.grecaptcha-badge {visibility:hidden}</style>
      <form id="download-form" action="https://drive.usercontent.google.com/download" method="get">
      <input type="hidden" name="id" value="FILEID">
      <input type="hidden" name="export" value="download">
      <input type="hidden" name="confirm" value="t">
      <input type="hidden" name="uuid" value="normal-confirmation">
      ''' + extra + '</form></html>').encode()


class Response(io.BytesIO):
    status = 200

    def __init__(self, body, content_type='application/octet-stream', url='https://example.test/base.ipa', declared=None):
        super().__init__(body)
        self.headers = {'Content-Type': content_type}
        if declared is not None:
            self.headers['Content-Length'] = str(declared)
        self.url = url

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return self.responses.pop(0)


class FetchBaseTests(unittest.TestCase):
    def test_drive_view_is_normalized(self):
        url, file_id = fetcher.normalize_url('https://drive.google.com/file/d/FILEID/view?usp=sharing')
        self.assertEqual(file_id, 'FILEID')
        self.assertIn('drive.usercontent.google.com/download?', url)

    def test_http_and_credentials_refused(self):
        for url in ('http://example.test/base', 'https://user:pass@example.test/base', 'https://example.test:8443/a'):
            with self.assertRaises(fetcher.DownloadError):
                fetcher.https_url(url)

    def test_control_characters_refused(self):
        with self.assertRaises(fetcher.DownloadError):
            fetcher.https_url('https://example.test/base\nsecret')

    def test_redirect_to_http_refused(self):
        with self.assertRaises(fetcher.DownloadError):
            fetcher.HTTPSRedirect().redirect_request(urllib.request.Request('https://example.test/a'), None, 302, '', {}, 'http://example.test/b')

    def test_normal_drive_confirmation_accepts_inert_css(self):
        url = fetcher.confirmation_url(confirmation(), 'FILEID')
        self.assertIn('confirm=t', url)

    def test_confirmation_wrong_file_refused(self):
        with self.assertRaises(fetcher.DownloadError):
            fetcher.confirmation_url(confirmation(), 'OTHER')

    def test_confirmation_foreign_destination_refused(self):
        body = confirmation().replace(b'drive.usercontent.google.com', b'example.test')
        with self.assertRaises(fetcher.DownloadError):
            fetcher.confirmation_url(body, 'FILEID')

    def test_actual_captcha_and_login_refused(self):
        for extra in ('<div class="g-recaptcha"></div>', '<script src="https://www.google.com/recaptcha/api.js"></script>', '<input type="password" name="pass">'):
            with self.assertRaises(fetcher.DownloadError):
                fetcher.confirmation_url(confirmation(extra), 'FILEID')

    def test_unknown_and_duplicate_confirmation_fields_refused(self):
        for extra in ('<input type="hidden" name="secret" value="x">', '<input type="hidden" name="id" value="FILEID">'):
            with self.assertRaises(fetcher.DownloadError):
                fetcher.confirmation_url(confirmation(extra), 'FILEID')

    def test_confirmation_size_limit(self):
        with self.assertRaises(fetcher.DownloadError):
            fetcher.confirmation_url(b'x' * (fetcher.MAX_HTML + 1), 'FILEID')

    def test_complete_pinned_download_publishes_once(self):
        data = ipa_fixture()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'new.ipa'
            result = fetcher.fetch(output, target_for(data), url='https://example.test/base.ipa', opener=Opener(Response(data)))
            self.assertEqual(output.read_bytes(), data)
            self.assertEqual(result['members_crc_checked'], 2)
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_one_drive_confirmation_then_download(self):
        data = ipa_fixture()
        opener = Opener(Response(confirmation(), 'text/html'), Response(data))
        with tempfile.TemporaryDirectory() as directory:
            fetcher.fetch(Path(directory) / 'base.ipa', target_for(data), url='https://drive.google.com/file/d/FILEID/view', opener=opener)
        self.assertEqual(len(opener.requests), 2)

    def test_repeated_confirmation_refused_without_output(self):
        data = ipa_fixture()
        opener = Opener(Response(confirmation(), 'text/html'), Response(confirmation(), 'text/html'))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'base.ipa'
            with self.assertRaises(fetcher.DownloadError):
                fetcher.fetch(output, target_for(data), url='https://drive.google.com/file/d/FILEID/view', opener=opener)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_hash_mismatch_cleans_partial(self):
        data = ipa_fixture()
        wrong = bytearray(data)
        wrong[40] ^= 1
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(fetcher.DownloadError):
                fetcher.fetch(Path(directory) / 'base.ipa', target_for(data), url='https://example.test/base', opener=Opener(Response(wrong)))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_stream_and_declared_size_caps(self):
        data = ipa_fixture()
        for response in (Response(data + b'x'), Response(data, declared=len(data) + 1), Response(data[:-1])):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(fetcher.DownloadError):
                    fetcher.fetch(Path(directory) / 'base.ipa', target_for(data), url='https://example.test/base', opener=Opener(response))
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_existing_destination_is_not_changed(self):
        data = ipa_fixture()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'base.ipa'
            output.write_bytes(b'keep')
            with self.assertRaises(fetcher.DownloadError):
                fetcher.fetch(output, target_for(data), url='https://example.test/base', opener=Opener(Response(data)))
            self.assertEqual(output.read_bytes(), b'keep')

    def test_nonzip_with_correct_hash_refused(self):
        data = b'not a zip archive'
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(zipfile.BadZipFile):
                fetcher.fetch(Path(directory) / 'base.ipa', target_for(data), url='https://example.test/base', opener=Opener(Response(data)))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_local_copy_preserves_source(self):
        data = ipa_fixture()
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'source.ipa', Path(directory) / 'output.ipa'
            source.write_bytes(data)
            fetcher.fetch(output, target_for(data), local=source)
            self.assertEqual(source.read_bytes(), data)
            self.assertEqual(output.read_bytes(), data)

    def test_invalid_pin_manifest_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'Target.json'
            path.write_text(json.dumps({'schema_version': 1, 'target': {'ipa_size': True, 'ipa_sha256': '0' * 64}}))
            with self.assertRaises(fetcher.DownloadError):
                fetcher.read_target(path)

    def test_partial_http_response_refused(self):
        data = ipa_fixture()
        response = Response(data)
        response.status = 206
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(fetcher.DownloadError):
                fetcher.fetch(Path(directory) / 'base.ipa', target_for(data), url='https://example.test/base', opener=Opener(response))


if __name__ == '__main__':
    unittest.main()
