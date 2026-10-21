from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from presenter_tts import narrate
from presenter_tts.audio import AudioError, Format


def sha(data):
    return hashlib.sha256(data).hexdigest()


class StubBackend:
    """0.1 s of PCM per speech part, recording what was spoken."""
    spoken = []

    def __init__(self, config):
        pass

    @contextmanager
    def synthesize(self, text):
        StubBackend.spoken.append(text)
        yield Format(16000), iter([b'\x01\x00' * 1600])


def make_deck(root, texts):
    """A two-slide deck in the shape html-deck + presentation-ja leave behind."""
    deck = Path(root)
    (deck / 'slides').mkdir(parents=True)
    slides = []
    for position, (sid, text) in enumerate(texts.items(), 1):
        html = deck / 'slides' / f'0{position}-{sid}.html'
        html.write_text(f'<article class="slide" data-slide-id="{sid}"><h1>{sid}</h1></article>',
                        encoding='utf-8')
        slides.append({'id': sid, 'position': position, 'file': f'slides/{html.name}',
                       'claim': f'claim {sid}', 'html_sha256': sha(html.read_bytes())})
    (deck / 'presentation-map.json').write_text(json.dumps({'slides': slides}), encoding='utf-8')
    script = {'segments': [{'id': f'seg-{s["id"]}', 'slide_id': s['id'], 'slide_sha256': s['html_sha256'],
                            'text': texts[s['id']], 'pause_after_seconds': 0.5} for s in slides]}
    narration = deck / 'narration'
    (narration / 'checked').mkdir(parents=True)
    (narration / 'script.json').write_text(json.dumps(script, ensure_ascii=False), encoding='utf-8')
    receipt(deck)
    return deck


def receipt(deck):
    """What presentation-ja's check records: the hashes of the script and the map it checked."""
    narration = deck / 'narration'
    (narration / 'checked' / 'receipt.json').write_text(json.dumps({
        'script_sha256': sha((narration / 'script.json').read_bytes()),
        'deck_map_sha256': sha((deck / 'presentation-map.json').read_bytes())}), encoding='utf-8')


class NarrateTests(unittest.TestCase):
    def setUp(self):
        StubBackend.spoken = []
        patcher = patch('presenter_tts.narrate.Backend', StubBackend)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.deck = make_deck(tempfile.mkdtemp(), {'intro': 'こんにちは。今日は検索の話です。',
                                                    'limits': '限界もあります。'})

    def run_narrate(self, **kwargs):
        return narrate.narrate({}, self.deck, log=lambda line: None, **kwargs)

    def test_outputs_audio_timeline_and_player(self):
        result = self.run_narrate()
        out = self.deck / 'narration'
        self.assertEqual(StubBackend.spoken, ['こんにちは。', '今日は検索の話です。', '限界もあります。'])
        # intro: 2 parts x 0.1 s + 0.5 s pause; limits: 0.1 s + 0.5 s pause.
        self.assertEqual([(s['id'], s['start'], s['end']) for s in result['slides']],
                         [('intro', 0, 0.7), ('limits', 0.7, 1.3)])
        with wave.open(str(out / 'narration.wav')) as clip:
            self.assertAlmostEqual(clip.getnframes() / clip.getframerate(), 1.3)
        self.assertEqual(json.loads((out / 'timeline.json').read_text(encoding='utf-8')), result)
        page = (out / 'player.html').read_text(encoding='utf-8')
        self.assertIn('"slide": "../slides/01-intro.html"', page)
        self.assertNotIn('/*TIMELINE*/', page)

    def test_unchanged_slides_are_reused(self):
        self.run_narrate()
        StubBackend.spoken = []
        self.run_narrate()
        self.assertEqual(StubBackend.spoken, [])
        self.run_narrate(force=True)
        self.assertEqual(len(StubBackend.spoken), 3)

    def test_edited_script_needs_presentation_ja_check_again(self):
        script = self.deck / 'narration' / 'script.json'
        data = json.loads(script.read_text(encoding='utf-8'))
        data['segments'][1]['text'] = '別の文です。'
        script.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        with self.assertRaisesRegex(AudioError, 'presentation-ja'):
            self.run_narrate()
        receipt(self.deck)  # After presentation-ja checks it again, only that slide is redone.
        self.run_narrate()
        StubBackend.spoken = []
        data['segments'][1]['text'] = 'さらに別の文です。'
        script.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        receipt(self.deck)
        self.run_narrate()
        self.assertEqual(StubBackend.spoken, ['さらに別の文です。'])

    def test_changed_slide_is_refused(self):
        (self.deck / 'slides' / '02-limits.html').write_text('<article>changed</article>', encoding='utf-8')
        with self.assertRaisesRegex(AudioError, '02-limits.html changed'):
            self.run_narrate()


if __name__ == '__main__':
    unittest.main()
