from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from presenter_tts import api
from presenter_tts.audio import Format


class StubBackend:
    texts = []

    def __init__(self, config):
        self.config = config

    @contextmanager
    def synthesize(self, text):
        StubBackend.texts.append((text, self.config.get('voice')))
        yield Format(44100), iter([b'\x10\x00' * 4410])  # 0.1 s per part


class ParseTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(api.parse(b'{"input": "hi", "voice": "myvoice"}'), ('hi', 'myvoice', 'mp3', 1.0))
        for body, param in ((b'[]', None), (b'{}', 'input'), (b'{"input": " "}', 'input'),
                            (b'{"input": "hi", "response_format": "ogg"}', 'response_format'),
                            (b'{"input": "hi", "speed": 5}', 'speed'),
                            (b'{"input": "hi", "speed": true}', 'speed'),
                            (('{"input": "%s"}' % ('a' * 4097)).encode(), 'input')):
            with self.assertRaises(api.RequestError) as caught:
                api.parse(body)
            self.assertEqual(caught.exception.param, param, body[:40])

    def test_key_is_required_off_loopback_and_kept(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(api, 'KEY_FILE', Path(folder, 'api-key.txt')):
            self.assertIsNone(api.api_key('127.0.0.1'))
            self.assertEqual(api.api_key('0.0.0.0', 'given'), 'given')
            first = api.api_key('0.0.0.0')
            self.assertGreaterEqual(len(first), 24)
            self.assertEqual(api.api_key('0.0.0.0'), first)


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is required')
class SpeechEndpointTests(unittest.TestCase):
    def setUp(self):
        StubBackend.texts = []
        self.voices = Path(tempfile.mkdtemp())
        (self.voices / 'myvoice.s2voice').write_bytes(b'v')
        for target, value in (('presenter_tts.api.Backend', StubBackend),
                              ('presenter_tts.voices.VOICES', self.voices)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        config = {'voice': 'C:/x/narrator.s2voice'}
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), api.handler(config, threading.Lock(), 40, 'k3y'))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}/v1'

    def call(self, path, payload=None, key='k3y'):
        request = urllib.request.Request(self.url + path, data=payload and json.dumps(payload).encode(),
                                         headers={'Authorization': f'Bearer {key}',
                                                  'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, response.headers['Content-Type'], response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers['Content-Type'], error.read()

    def test_wrong_key_is_rejected(self):
        status, _, body = self.call('/audio/speech', {'input': 'hi'}, key='nope')
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)['error']['code'], 'invalid_api_key')

    def test_wav_and_pcm_and_voices(self):
        status, kind, body = self.call('/audio/speech', {'input': 'こんにちは。元気？', 'voice': 'myvoice',
                                                         'response_format': 'wav'})
        self.assertEqual((status, kind), (200, 'audio/wav'))
        self.assertTrue(body.startswith(b'RIFF'))
        self.assertEqual([t for t, _ in StubBackend.texts], ['こんにちは。', '元気？'])
        self.assertTrue(StubBackend.texts[0][1].endswith('/myvoice.s2voice'))
        StubBackend.texts = []
        status, kind, body = self.call('/audio/speech', {'input': 'hi', 'voice': 'alloy',
                                                         'response_format': 'pcm'})
        self.assertEqual((status, kind), (200, 'audio/pcm'))
        self.assertAlmostEqual(len(body), 2 * 2400, delta=200)  # 0.1 s resampled to 24 kHz
        self.assertEqual(StubBackend.texts[0][1], 'C:/x/narrator.s2voice')  # OpenAI name: default

    def test_errors_and_models(self):
        status, _, body = self.call('/audio/speech', {'input': ''})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)['error']['param'], 'input')
        status, _, body = self.call('/models')
        self.assertEqual(status, 200)
        self.assertIn('tts-1', [m['id'] for m in json.loads(body)['data']])


if __name__ == '__main__':
    unittest.main()
