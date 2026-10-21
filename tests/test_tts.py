import array
from contextlib import contextmanager
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
from pathlib import Path
import shutil
import socket
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

from presenter_tts.audio import AudioError, Backend, Format, read_pcm, render, transform
from presenter_tts.ssml import Mark, Pause, Speech, SSMLError, Style, compile_ssml


def document(body):
    return ('<speak xmlns="http://www.w3.org/2001/10/synthesis" version="1.1" '
            'xml:lang="ja-JP">' + body + '</speak>')


class SSMLTests(unittest.TestCase):
    def test_mixed_text_and_alias_are_preserved(self):
        events, _ = compile_ssml(document('Before <sub alias="エーダブリューエス">AWS</sub> after。'))
        self.assertEqual(events[0].text, 'Before エーダブリューエス after。')

    def test_nested_prosody_uses_default_rate_and_restores_parent(self):
        events, _ = compile_ssml(document('<prosody rate="50%">A<prosody rate="90%" '
            'pitch="+2st" volume="+3dB">B</prosody>C</prosody>D'))
        speech = [e for e in events if isinstance(e, Speech)]
        self.assertEqual([e.style.rate for e in speech], [.5, .9, .5, 1.])
        self.assertEqual(speech[1].style.pitch_st, 2)
        self.assertEqual(speech[1].style.volume_db, 3)

    def test_default_volume_can_reset_silent(self):
        events, _ = compile_ssml(document('<prosody volume="silent">A<prosody volume="+3dB">B'
            '</prosody><prosody volume="default">C</prosody></prosody>'))
        self.assertEqual([e.style.silent for e in events], [True, True, False])

    def test_timing_and_sentence_boundaries(self):
        events, _ = compile_ssml(document('<mark name="start"/><s>一。二。</s>'
            '<break time="700ms"/><mark name="next"/>三。'))
        self.assertEqual([e.text for e in events if isinstance(e, Speech)], ['一。', '二。', '三。'])
        self.assertIn(Pause(.7), events)
        self.assertEqual(events[0], Mark('start'))

    def test_emphasis_is_explicitly_approximate(self):
        events, warnings = compile_ssml(document('<emphasis level="strong">重要です。</emphasis>'))
        self.assertTrue(warnings)
        self.assertEqual(events[0].style.volume_db, 4)

    def test_invalid_or_unsupported_input_is_rejected(self):
        invalid = ['<speak>hello</speak>', document('<phoneme ph="x">a</phoneme>'),
            document('<prosody contour="(0%,high)">a</prosody>'),
            document('<prosody/>a'), document('<s><p>a</p></s>'),
            document('<p><p>a</p></p>'), document('<emphasis><s>a</s></emphasis>'),
            document('<break time="NaNs"/>a'), document('<break time="11s"/>a'),
            document('<prosody rate="900%">a</prosody>'),
            document('<prosody rate="nan%">a</prosody>'),
            document('<mark name="x"/><mark name="x"/>a'),
            document('<break>text</break>a'), document('<sub alias=""/>a'),
            document('<x:prosody xmlns:x="urn:other">a</x:prosody>'),
            document('[emphasis]hello'), document('&lt;|speaker:0|&gt;hello'),
            '<!DOCTYPE speak [<!ENTITY x "hello">]>' + document('&x;'),
            document('<mark name="only"/>')]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(SSMLError):
                compile_ssml(value)

    def test_size_and_depth_limits(self):
        for value in (document('x' * 128001),
                      document('<prosody rate="100%">' * 26 + 'x' + '</prosody>' * 26)):
            with self.assertRaises(SSMLError):
                compile_ssml(value)


class StubBackend:
    @contextmanager
    def synthesize(self, text):
        yield Format(16000), iter([b'\x01', b'\x00' * 3199])


class RenderTests(unittest.TestCase):
    def test_pause_samples_marks_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'speech.wav'
            events = [Mark('start'), Pause(.05), Speech('one', Style()), Pause(.7),
                      Mark('next'), Speech('two', Style())]
            report = render(events, StubBackend(), path)
            with wave.open(str(path)) as wav:
                self.assertEqual(wav.getnframes(), 15200)
                data = wav.readframes(wav.getnframes())
            self.assertEqual(data[4800:27200], b'\0' * 22400)
            self.assertEqual(report['marks'][1]['frame'], 13600)
            self.assertEqual(json.loads(path.with_suffix('.json').read_text())['sample_rate'], 16000)
            self.assertGreater(report['rtf_speech'], 0)

    def test_filler_plays_only_when_speech_is_late(self):
        class SlowBackend:
            def __init__(self, delay):
                self.delay = delay

            @contextmanager
            def synthesize(self, text):
                def chunks():
                    time.sleep(self.delay)
                    yield b'\x02\x00' * 1600
                yield Format(16000), chunks()
        clip = (Format(16000), b'\x01\x00' * 800)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'speech.wav'
            late = render([Speech('one', Style())], SlowBackend(.3), path, filler=clip, filler_after=.05)
            with wave.open(str(path)) as wav:
                data = wav.readframes(wav.getnframes())
            prompt = render([Speech('one', Style())], SlowBackend(0), path, force=True,
                            filler=clip, filler_after=5)
        self.assertEqual(data, b'\x01\x00' * 800 + b'\x02\x00' * 1600)
        self.assertAlmostEqual(late['filler']['seconds'], .05)
        self.assertAlmostEqual(late['speech_seconds'], .1)
        self.assertNotIn('filler', prompt)

    def test_filler_is_trimmed_and_faded(self):
        from presenter_tts.fillers import trim_and_fade
        pcm = array.array('h', [0] * 10 + [1000] * 100 + [0] * 10).tobytes()
        out = array.array('h', trim_and_fade(pcm, Format(8000), .375))  # 3-sample fades
        self.assertEqual(len(out), 100)
        self.assertEqual(list(out[:4]), [250, 500, 750, 1000])
        self.assertEqual(list(out[-4:]), [1000, 750, 500, 250])

    def test_error_does_not_replace_existing_output(self):
        class BrokenBackend:
            @contextmanager
            def synthesize(self, text):
                def chunks():
                    yield b'\0\0'
                    raise AudioError('backend disconnected')
                yield Format(16000), chunks()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'speech.wav'
            path.write_bytes(b'previous')
            with self.assertRaises(AudioError):
                render([Speech('hello', Style())], BrokenBackend(), path, force=True)
            self.assertEqual(path.read_bytes(), b'previous')
            self.assertEqual(list(Path(directory).glob('.presenter-*')), [])

    def test_missing_filter_dependency_fails_before_synthesis(self):
        with tempfile.TemporaryDirectory() as directory, patch('presenter_tts.audio.shutil.which', return_value=None):
            with self.assertRaisesRegex(AudioError, 'FFmpeg'):
                render([Speech('hello', Style(rate=.9))], StubBackend(), Path(directory) / 'x.wav')

    def test_empty_or_odd_audio_is_not_published(self):
        class EmptyBackend:
            @contextmanager
            def synthesize(self, text):
                yield Format(16000), iter([b'\0'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'x.wav'
            with self.assertRaisesRegex(AudioError, 'incomplete PCM'):
                render([Speech('hello', Style())], EmptyBackend(), path)
            self.assertFalse(path.exists())

    def test_remote_endpoints_are_rejected(self):
        with self.assertRaises(AudioError):
            Backend({'url': 'https://example.com'})

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is not installed')
    def test_real_ffmpeg_changes_duration_and_pitch(self):
        rate = 16000
        pcm = b''.join(struct.pack('<h', round(3000 * math.sin(2 * math.pi * 440 * n / rate)))
                       for n in range(rate))
        out = b''.join(transform(iter([pcm]), Format(rate), Style(rate=.8, pitch_st=3)))
        samples = struct.unpack('<' + 'h' * (len(out) // 2), out)
        self.assertAlmostEqual(len(samples) / rate, 1.25, delta=.08)
        crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
        self.assertAlmostEqual(crossings / (len(samples) / rate), 440 * 2 ** (3 / 12), delta=15)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is not installed')
    def test_ffmpeg_emits_audio_before_input_finishes(self):
        release_input = threading.Event()
        received_output = threading.Event()
        errors = []

        def source():
            # Half a second of PCM, then wait as a real TTS stream might.
            yield b'\1\0' * 8000
            if not release_input.wait(8):
                raise RuntimeError('test input was not released')
            yield b'\1\0' * 8000

        def consume():
            try:
                for chunk in transform(source(), Format(16000), Style(rate=.9)):
                    if chunk:
                        received_output.set()
            except Exception as exc:
                errors.append(exc)

        consumer = threading.Thread(target=consume, daemon=True)
        consumer.start()
        try:
            self.assertTrue(received_output.wait(5), 'FFmpeg buffered until end of input')
        finally:
            release_input.set()
            consumer.join(timeout=10)
        self.assertFalse(consumer.is_alive())
        self.assertEqual(errors, [])

    def test_truncated_response_is_rejected(self):
        response = io.BytesIO(b'12')
        response.headers = {'Content-Length': '4'}
        with self.assertRaises(AudioError):
            list(read_pcm(response))


class WireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                cls.requests.append((self.path, self.headers['Content-Type'], body))
                if self.path.startswith('/audio_query'):
                    data, content_type = b'{}', 'application/json'
                elif self.path.startswith('/synthesis'):
                    buf = io.BytesIO()
                    with wave.open(buf, 'wb') as wav:
                        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                        wav.writeframes(b'\1\0' * 800)
                    data, content_type = buf.getvalue(), 'audio/wav'
                else:
                    data, content_type = b'\1\0' * 1600, 'application/octet-stream'
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('X-Audio-Encoding', 'pcm_s16le')
                self.send_header('X-Audio-Sample-Rate', '16000')
                self.send_header('X-Audio-Channels', '1')
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:' + str(cls.server.server_port)
        try:
            with socket.create_connection(('127.0.0.1', cls.server.server_port), timeout=1):
                pass
        except PermissionError as exc:
            cls.server.shutdown()
            cls.server.server_close()
            cls.thread.join()
            raise unittest.SkipTest(f'Execution environment blocks loopback connections: {exc}')

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_real_http_s2_multipart_pcm_to_wav(self):
        events, _ = compile_ssml(document('こんにちは。<break time="100ms"/><mark name="slide"/>次です。'))
        with tempfile.TemporaryDirectory() as directory:
            report = render(events, Backend({'url': self.url, 'voice': 'presenter'}), Path(directory) / 'out.wav')
        self.assertAlmostEqual(report['audio_seconds'], .3)
        path, content_type, body = self.requests[-1]
        self.assertEqual(path, '/generate')
        self.assertIn('multipart/form-data', content_type)
        self.assertIn(b'"stream": true', body)
        self.assertIn(b'"chunked": true', body)
        self.assertIn(b'pcm_s16le', body)
        self.assertIn(b'presenter', body)
        self.assertNotIn(b'<speak', body)

    def test_real_http_voicevox_queries(self):
        with Backend({'backend': 'voicevox', 'url': self.url, 'speaker': 3}).synthesize('hello') as (fmt, chunks):
            self.assertEqual(fmt.rate, 16000)
            self.assertEqual(len(b''.join(chunks)), 1600)
        self.assertEqual(json.loads(self.requests[-1][2])['prePhonemeLength'], 0)


class HTTPContractTests(unittest.TestCase):
    def test_s2_request_contract_without_network(self):
        response = io.BytesIO(b'\1\0' * 160)
        response.headers = {'X-Audio-Encoding': 'pcm_s16le', 'X-Audio-Sample-Rate': '16000',
                            'X-Audio-Channels': '1', 'Content-Length': '320'}
        with patch('presenter_tts.audio.urllib.request.build_opener') as build:
            build.return_value.open.return_value = response
            with Backend({'voice': 'presenter'}).synthesize('hello') as (fmt, chunks):
                self.assertEqual(fmt, Format(16000))
                self.assertEqual(len(b''.join(chunks)), 320)
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, 'http://127.0.0.1:3030/generate')
            self.assertIn(b'"stream": true', request.data)
            self.assertIn(b'"chunked": true', request.data)
            self.assertIn(b'pcm_s16le', request.data)
            self.assertIn(b'presenter', request.data)

    def test_bad_server_format_is_rejected(self):
        response = io.BytesIO(b'RIFF')
        response.headers = {'Content-Type': 'audio/wav'}
        with patch('presenter_tts.audio.urllib.request.build_opener') as build:
            build.return_value.open.return_value = response
            with self.assertRaisesRegex(AudioError, 'X-Audio-Encoding'):
                with Backend({}).synthesize('hello'):
                    pass

    def test_busy_server_is_retried(self):
        import urllib.error
        busy = urllib.error.HTTPError('u', 503, 'busy', {}, io.BytesIO(b'busy'))
        with patch('presenter_tts.audio.urllib.request.build_opener') as build, \
                patch('presenter_tts.audio.time.sleep') as sleep:
            build.return_value.open.side_effect = [busy, 'ok']
            self.assertEqual(Backend({}).post('/generate', b''), 'ok')
        sleep.assert_called_once()

    def test_start_buffer_scales_with_text_length(self):
        backend = Backend({'generation': {'stream_start_buffer_ms_per_char': 50}})
        with patch.object(backend, 'post', side_effect=AudioError('stop')) as post:
            with self.assertRaises(AudioError):
                with backend.synthesize('あいうえお。'):
                    pass
        self.assertIn(b'"stream_start_buffer_ms": 300', post.call_args.args[1])
        self.assertNotIn(b'per_char', post.call_args.args[1])

    def test_voice_switching(self):
        from presenter_tts.voices import set_voice
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'myvoice.s2voice').write_bytes(b'x')
            config = {'voice': 'old.s2voice'}
            with patch('presenter_tts.voices.VOICES', Path(folder)):
                set_voice(config, 'myvoice')
                self.assertTrue(config['voice'].endswith('/myvoice.s2voice'))
                with self.assertRaisesRegex(AudioError, 'available: default, myvoice'):
                    set_voice(config, 'nobody')
                set_voice(config, 'default')
        self.assertNotIn('voice', config)

    def test_filler_pool_drops_runaways(self):
        import presenter_tts.fillers as main
        with tempfile.TemporaryDirectory() as folder:
            takes = Path(folder, 'fillers', 'default')
            takes.mkdir(parents=True)
            for name, seconds in (('a', .2), ('b', .2), ('long', 3)):
                with wave.open(str(takes / f'{name}.wav'), 'wb') as clip:
                    clip.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                    clip.writeframes(b'\xe8\x03' * int(8000 * seconds))
            settings = {'filler_max_seconds': 1, 'filler_pitch_semitones': 0,
                        'filler_rate_min': 1, 'filler_rate_max': 1}
            with patch('presenter_tts.voices.FILLER_CACHE', Path(folder, 'fillers')), \
                    patch.object(main, 'FILLERS', ('a', 'b', 'long')):
                pool = main.filler_pool(Backend({}), settings)
        self.assertEqual([p.name for p, _ in pool], ['a.wav', 'b.wav'])

    def test_pick_filler_fits_the_wait(self):
        from presenter_tts.fillers import pick_filler
        pool = [(name, (Format(8000), b'\0\0' * int(8000 * s)))
                for name, s in (('s', .3), ('m', .6), ('l', .9), ('xl', 1.2), ('xs', .1))]
        picks = {pick_filler(pool, 1.0, last='l')[0] for _ in range(50)}
        self.assertEqual(picks, {'m', 's', 'xs'})  # three longest that fit 1.0 s, never the last
        self.assertIsNone(pick_filler(pool, .05))

    def test_say_skips_filler_by_probability_and_expected_arrival(self):
        from presenter_tts.say import say
        clip = ('take', (Format(8000), b'\0\0' * 4000))  # 0.5 s
        config = {'say': {'filler': True, 'filler_probability': .5, 'filler_after_ms': 1000,
                          'arrival_base_ms': 0, 'arrival_ms_per_char': 100}}
        with patch('presenter_tts.say.render', return_value={}) as fake, \
                patch('presenter_tts.fillers.filler_pool', return_value=[clip]), \
                patch('presenter_tts.fillers.random.random', return_value=.1):
            say(Backend(config), 'あ' * 15, Path('x.wav'))  # body expected at 1.5 s: filler fits
            say(Backend(config), 'あ' * 14, Path('x.wav'))  # 1.4 s: filler would delay the body
            with patch('presenter_tts.fillers.random.random', return_value=.9):
                say(Backend(config), 'あ' * 15, Path('x.wav'))  # fits, but skipped by chance
        self.assertEqual([c.kwargs['filler'] for c in fake.call_args_list], [clip[1], None, None])

    def test_first_clause_can_be_split(self):
        from presenter_tts.say import segments
        line = '今日は、いい天気です。明日も、晴れ。'
        self.assertEqual(segments(line, True), ['今日は、', 'いい天気です。', '明日も、晴れ。'])
        self.assertEqual(segments(line, False), ['今日は、いい天気です。', '明日も、晴れ。'])
        self.assertEqual(segments('はい、', True), ['はい、'])

    def test_long_sentences_are_cut_at_commas_outside_brackets(self):
        from presenter_tts.say import clauses
        sentence = 'ああ、いい、「う、え」、おおおおお、かか。'
        self.assertEqual(clauses(sentence, 8), ['ああ、いい、', '「う、え」、', 'おおおおお、', 'かか。'])
        self.assertEqual(clauses(sentence, 0), [sentence])
        self.assertEqual(''.join(clauses(sentence, 3)), sentence)
        self.assertEqual(clauses('代表作は「あいう」「えお」「かき」など。', 12),
                         ['代表作は「あいう」', '「えお」「かき」など。'])
        self.assertEqual(clauses('雑誌「言葉」を創刊した人物です。', 5), ['雑誌「言葉」を創刊した人物です。'])

    def test_say_skips_punctuation_only_input(self):
        from presenter_tts.say import say
        backend = Backend({'say': {'filler': True}})
        with patch('presenter_tts.say.render') as fake, \
                patch('presenter_tts.fillers.filler_pool', return_value=[]), \
                patch('builtins.input', side_effect=['！？', EOFError]):
            self.assertEqual(say(backend, '！？', Path('x.wav')), 0)  # One-shot: returns
            self.assertEqual(say(backend, '', Path('x.wav')), 0)      # Interactive: keeps going
        fake.assert_not_called()

    def test_say_speaks_each_typed_line(self):
        from presenter_tts.say import say
        with patch('presenter_tts.say.render') as fake, \
                patch('presenter_tts.fillers.filler_pool', return_value=[('take', (Format(8000), b''))]), \
                patch('builtins.input', side_effect=['こんにちは。元気？ はい', '', EOFError]):
            self.assertEqual(say(Backend({'say': {'filler': True, 'filler_after_ms': 400}}),
                                 '', Path('x.wav')), 0)
        fake.assert_called_once()
        self.assertTrue(fake.call_args.kwargs['play'])
        self.assertEqual(fake.call_args.kwargs['filler'], (Format(8000), b''))
        self.assertEqual(fake.call_args.kwargs['filler_after'], .4)
        self.assertEqual([e.text for e in fake.call_args.args[0]], ['こんにちは。', '元気？', 'はい'])


if __name__ == '__main__':
    unittest.main()
