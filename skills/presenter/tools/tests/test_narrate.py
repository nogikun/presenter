from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urljoin
import wave

from presenter_tts import narrate, video
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


def receipt(deck, checked=None):
    """What presentation-ja's check records: the hashes of the script and the map it checked."""
    narration = deck / 'narration'
    checked = checked or narration / 'checked'
    checked.mkdir(parents=True, exist_ok=True)
    (checked / 'receipt.json').write_text(json.dumps({
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

    def run_narrate(self, config=None, **kwargs):
        return narrate.narrate(config or {}, self.deck, log=lambda line: None, **kwargs)

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
        # The transcript seeks by where each sentence actually landed, pauses included.
        self.assertEqual([[(c['text'], c['start'], c['end']) for c in s['cues']] for s in result['slides']],
                         [[('こんにちは。', 0, 0.1), ('今日は検索の話です。', 0.1, 0.2)],
                          [('限界もあります。', 0.7, 0.8)]])
        self.assertEqual(result['title'], self.deck.name)
        (self.deck / 'index.html').write_text('<title>検索 &amp; 発表</title>', encoding='utf-8')
        self.assertEqual(self.run_narrate()['title'], '検索 & 発表')

    def test_missing_sentence_timings_resynthesize_the_slide(self):
        self.run_narrate()
        (self.deck / 'narration/audio/limits.json').unlink()
        StubBackend.spoken = []
        self.run_narrate()
        self.assertEqual(StubBackend.spoken, ['限界もあります。'])

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

    def test_dependency_changes_and_removal_are_refused(self):
        manifest = self.deck / 'presentation-map.json'
        data = json.loads(manifest.read_text(encoding='utf-8'))
        names = ('deck.md', 'theme.css', 'index.html', 'assets/diagram.svg')
        for name in names:
            path = self.deck / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'original')
        data['dependencies'] = {name: sha(b'original') for name in names}
        manifest.write_text(json.dumps(data), encoding='utf-8')
        receipt(self.deck)
        self.run_narrate()
        StubBackend.spoken = []
        for name in names:
            path = self.deck / name
            with self.subTest(name=name, change='edited'):
                path.write_bytes(b'changed')
                with self.assertRaisesRegex(AudioError, 'changed since'):
                    self.run_narrate()
            with self.subTest(name=name, change='removed'):
                path.unlink()
                with self.assertRaisesRegex(AudioError, 'changed since'):
                    self.run_narrate()
            path.write_bytes(b'original')
        self.assertEqual(StubBackend.spoken, [])

    def test_unmapped_slide_is_refused_before_synthesis(self):
        (self.deck / 'slides/03-extra.html').write_text('<article>new</article>', encoding='utf-8')
        with self.assertRaisesRegex(AudioError, 'Slides not in the narration map'):
            self.run_narrate()
        self.assertEqual(StubBackend.spoken, [])

    def test_alternate_check_folder_uses_its_receipt(self):
        script = self.deck / 'narration/script.json'
        data = json.loads(script.read_text(encoding='utf-8'))
        data['segments'][0]['text'] = 'Revised introduction.'
        script.write_text(json.dumps(data), encoding='utf-8')
        checked = self.deck / 'narration/checked-v2'
        receipt(self.deck, checked)
        with self.assertRaisesRegex(AudioError, 'changed after'):
            self.run_narrate()
        self.run_narrate(checked=checked)
        self.assertIn('Revised introduction.', StubBackend.spoken)
        with self.assertRaisesRegex(AudioError, 'Missing'):
            self.run_narrate(checked=self.deck / 'missing-check')

    def test_sound_settings_invalidate_cached_audio(self):
        self.run_narrate()
        for config in ({'backend': 'voicevox'}, {'speaker': 8},
                       {'url': 'http://localhost:3031'}, {'generation': {'temperature': .6}}):
            with self.subTest(config=config):
                self.run_narrate()  # Restore the baseline.
                StubBackend.spoken = []
                self.run_narrate(config=config)
                self.assertEqual(len(StubBackend.spoken), 3)
                StubBackend.spoken = []
                self.run_narrate(config=config)
                self.assertEqual(StubBackend.spoken, [])

    def test_player_paths_resolve_from_a_custom_narration_folder(self):
        original = self.deck / 'narration'
        out = self.deck / 'exports #1/deep/narration'
        out.parent.mkdir(parents=True)
        original.rename(out)
        result = self.run_narrate(narration=out)
        for slide, source in zip(result['slides'], sorted((self.deck / 'slides').glob('*.html'))):
            self.assertEqual(urljoin((out / 'player.html').as_uri(), slide['slide']), source.as_uri())
            self.assertTrue((out / unquote(slide['audio'])).is_file())

    def test_cross_drive_output_fails_before_synthesis(self):
        with patch('presenter_tts.narrate.os.path.relpath', side_effect=ValueError('different mounts')):
            with self.assertRaisesRegex(AudioError, 'same drive'):
                self.run_narrate()
        self.assertEqual(StubBackend.spoken, [])
        self.assertFalse((self.deck / 'narration/audio').exists())


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'needs FFmpeg')
class VideoTests(unittest.TestCase):
    def test_exports_mp4_matching_the_narration_and_links_it(self):
        with patch('presenter_tts.narrate.Backend', StubBackend):
            deck = make_deck(tempfile.mkdtemp(), {'intro': 'こんにちは。', 'limits': '限界もあります。'})
            narrate.narrate({}, deck, log=lambda line: None)
        rendered = []

        def screenshot(exe, page, png, profile, width, height):  # Stand-in for headless Chrome.
            rendered.append(page)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color=c=white:s={width}x{height}',
                            '-frames:v', '1', str(png)], check=True)

        with patch('presenter_tts.video.browser', return_value='chrome'),                 patch('presenter_tts.video.screenshot', screenshot):
            result = video.export(deck / 'narration', width=640, height=360, log=lambda line: None)
        self.assertEqual(rendered, sorted((deck / 'slides').resolve().glob('*.html')))
        self.assertAlmostEqual(result['seconds'], 1.2, delta=0.1)
        self.assertEqual(json.loads((deck / 'narration/timeline.json').read_text(encoding='utf-8'))['video'],
                         'presentation.mp4')
        self.assertIn('"video": "presentation.mp4"', (deck / 'narration/player.html').read_text(encoding='utf-8'))
        self.assertEqual([p.name for p in (deck / 'narration').iterdir() if p.name.startswith('.')], [])


if __name__ == '__main__':
    unittest.main()
