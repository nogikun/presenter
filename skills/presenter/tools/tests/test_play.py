import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from presenter_tts import narrate, play
from test_narrate import StubBackend, make_deck


class VoiceOutputTests(unittest.TestCase):
    def setUp(self):
        patcher = patch('presenter_tts.narrate.Backend', StubBackend)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.deck = make_deck(tempfile.mkdtemp(), {'intro': 'こんにちは。', 'limits': '限界もあります。'})
        self.out = self.deck / 'narration'

    def test_other_voices_get_their_own_folder_and_the_player_lists_them(self):
        narrate.narrate({}, self.deck, log=lambda line: None)
        voice = Path(tempfile.mkdtemp()) / 'alice.s2voice'
        voice.write_bytes(b'profile')
        narrate.narrate({'voice': str(voice)}, self.deck, log=lambda line: None, out=self.out / 'voices/alice')
        main = json.loads((self.out / 'timeline.json').read_text(encoding='utf-8'))
        alice = json.loads((self.out / 'voices/alice/timeline.json').read_text(encoding='utf-8'))
        self.assertEqual((main['voice'], alice['voice']), ('default', 'alice'))
        self.assertTrue((self.out / 'voices/alice/narration.wav').is_file())
        # Slide links resolve from the voice folder, so the player can load its timeline as is.
        self.assertEqual(alice['slides'][0]['slide'], '../../../slides/01-intro.html')
        self.assertEqual([v['name'] for v in narrate.voice_outputs(self.out)], ['default', 'alice'])
        page = (self.out / 'player.html').read_text(encoding='utf-8')
        self.assertIn('"timeline": "voices/alice/timeline.json"', page)
        self.assertIn('"voice": "alice"', page)  # Its timeline is embedded for file:// players.


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.deck = make_deck(tempfile.mkdtemp(), {'intro': 'こんにちは。'})
        self.out = self.deck / 'narration'
        (self.out / 'timeline.json').write_text(json.dumps({'voice': 'default', 'slides': []}), encoding='utf-8')
        (self.out / 'narration.wav').write_bytes(bytes(range(256)) * 4)
        self.requested = []

        def synthesize(name, log):
            self.requested.append(name)
            log('[1/1] intro: synthesizing')
            folder = self.out / 'voices' / name
            folder.mkdir(parents=True)
            (folder / 'timeline.json').write_text(json.dumps({'voice': name, 'slides': []}), encoding='utf-8')

        profiles = patch('presenter_tts.play.voices.profiles', return_value=[{'name': 'alice'}])
        profiles.start()
        self.addCleanup(profiles.stop)
        self.httpd, self.url = play.server(self.deck, self.out, synthesize)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.port = self.httpd.server_address[1]

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, data

    def test_lists_voices_and_synthesizes_a_missing_one(self):
        self.assertTrue(self.url.endswith('/narration/player.html'))
        _, data = self.request('GET', '/api/voices')
        self.assertEqual([(v['name'], v['ready']) for v in json.loads(data)['voices']],
                         [('default', True), ('alice', False)])
        response, _ = self.request('POST', '/api/narrate', json.dumps({'voice': 'alice'}),
                                   {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{self.port}'})
        self.assertEqual(response.status, 202)
        for _ in range(50):
            job = json.loads(self.request('GET', '/api/job')[1])
            if job['state'] != 'running':
                break
            time.sleep(.05)
        self.assertEqual((job['state'], job['voice'], job['total']), ('done', 'alice', 1))
        voices = json.loads(self.request('GET', '/api/voices')[1])['voices']
        self.assertEqual(voices[1], {'name': 'alice', 'ready': True, 'timeline': 'voices/alice/timeline.json'})
        self.assertEqual(self.requested, ['alice'])

    def test_refuses_other_sites_and_unknown_voices(self):
        response, _ = self.request('POST', '/api/narrate', json.dumps({'voice': 'alice'}),
                                   {'Origin': 'https://example.com'})
        self.assertEqual(response.status, 403)
        response, _ = self.request('GET', '/api/voices', headers={'Host': 'attacker.test'})
        self.assertEqual(response.status, 403)
        response, _ = self.request('POST', '/api/narrate', json.dumps({'voice': '../etc'}))
        self.assertEqual(response.status, 404)
        self.assertEqual(self.requested, [])

    def test_audio_supports_byte_ranges(self):
        response, data = self.request('GET', '/narration/narration.wav', headers={'Range': 'bytes=10-19'})
        self.assertEqual((response.status, response.getheader('Content-Range'), data),
                         (206, 'bytes 10-19/1024', bytes(range(10, 20))))
        response, data = self.request('GET', '/narration/narration.wav', headers={'Range': 'bytes=-4'})
        self.assertEqual((response.status, data), (206, bytes(range(252, 256))))
        response, _ = self.request('GET', '/')
        self.assertEqual((response.status, response.getheader('Location')), (302, '/narration/player.html'))


if __name__ == '__main__':
    unittest.main()
