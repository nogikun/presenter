import array
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from typer.testing import CliRunner

from presenter_tts import voices
from presenter_tts.cli import app


def write_wav(path, samples, rate=44100):
    with wave.open(str(path), 'wb') as clip:
        clip.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        clip.writeframes(array.array('h', samples).tobytes())


class VoiceCommandTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.voices = self.folder / 'voices'
        self.fillers = self.folder / 'fillers'
        self.voices.mkdir()
        for target, value in (('VOICES', self.voices), ('FILLER_CACHE', self.fillers)):
            patcher = patch.object(voices, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.config = self.folder / 'config.toml'
        self.config.write_text('', encoding='utf-8')
        self.runner = CliRunner()

    def invoke(self, *args, input=None):
        return self.runner.invoke(app, ['--config', str(self.config), *args], input=input)

    def test_add_encodes_boosts_and_records_the_voice(self):
        source = self.folder / 'rec.m4a'
        source.write_bytes(b'stub')
        (self.fillers / 'myvoice').mkdir(parents=True)

        def fake_run(command, **kwargs):
            if 'ffmpeg' in Path(command[0]).name.lower() or command[0] == 'ffmpeg':
                write_wav(command[-1], [1000, -2000] * 22050)  # 1 s at a -24 dB peak
            else:
                Path(command[command.index('--voice-dir') + 1], 'myvoice.s2voice').write_bytes(b'v')
            return type('Done', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        with patch('presenter_tts.voices.shutil.which', return_value='ffmpeg'), \
                patch('presenter_tts.voices.engine', return_value=('s2', 'm', 't', {})), \
                patch('presenter_tts.voices.subprocess.run', side_effect=fake_run):
            result = self.invoke('voice', 'add', 'myvoice', str(source), '--text', 'こんにちは。',
                                 '--no-preview')
        self.assertEqual(result.exit_code, 0, result.output)
        meta = json.loads((self.voices / 'myvoice.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['transcript'], 'こんにちは。')
        self.assertAlmostEqual(meta['gain_db'], 21.3 - 2.0 - 0.0, delta=1.5)  # capped at +20 dB
        self.assertFalse((self.fillers / 'myvoice').exists())  # stale fillers are dropped
        self.assertIn('5-30 s', result.output)

    def test_failed_force_reclone_keeps_the_old_voice(self):
        source = self.folder / 'rec.wav'
        write_wav(source, [1000] * 100)
        for suffix in ('.s2voice', '.wav', '.json'):
            (self.voices / f'myvoice{suffix}').write_bytes(b'old')

        def fake_run(command, **kwargs):
            if command[0] == 'ffmpeg':
                write_wav(command[-1], [1000, -2000] * 22050)
                return type('Done', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
            return type('Done', (), {'returncode': 1, 'stdout': '', 'stderr': 'CUDA out of memory'})()
        with patch('presenter_tts.voices.shutil.which', return_value='ffmpeg'), \
                patch('presenter_tts.voices.engine', return_value=('s2', 'm', 't', {})), \
                patch('presenter_tts.voices.subprocess.run', side_effect=fake_run):
            result = self.invoke('voice', 'add', 'myvoice', str(source), '-t', 'x', '--force',
                                 '--no-preview')
        self.assertEqual(result.exit_code, 2)
        self.assertIn('out of memory', result.output)
        for suffix in ('.s2voice', '.wav', '.json'):
            self.assertEqual((self.voices / f'myvoice{suffix}').read_bytes(), b'old')
        self.assertEqual(sorted(p.name for p in self.voices.iterdir()),
                         ['myvoice.json', 'myvoice.s2voice', 'myvoice.wav'])  # No staging left

    def test_cuda_root_with_bom_still_reaches_path(self):
        runtime = self.folder / 'runtime'
        for path in ('s2-build/Release/s2.exe', 'models/s2-pro-q4_k_m.gguf', 'models/tokenizer.json'):
            (runtime / path).parent.mkdir(parents=True, exist_ok=True)
            (runtime / path).write_bytes(b'x')
        (runtime / 's2-cuda-root.txt').write_bytes('\ufeffC:\\CUDA\\v12.9\r\n'.encode('utf-8'))
        with patch.object(voices, 'RUNTIME', runtime):
            env = voices.engine()[3]
        self.assertTrue(env['PATH'].startswith('C:\\CUDA\\v12.9\\bin'))

    def test_add_refuses_bad_input(self):
        source = self.folder / 'rec.wav'
        write_wav(source, [0] * 10)
        (self.voices / 'myvoice.s2voice').write_bytes(b'v')
        cases = [(['myvoice', str(source)], 'exactly one of --text'),
                 (['bad name', str(source), '-t', 'x'], 'Voice names'),
                 (['default', str(source), '-t', 'x'], 'Voice names'),
                 (['myvoice', str(source), '-t', 'x'], 'already exists')]
        for args, message in cases:
            result = self.invoke('voice', 'add', *args, '--no-preview')
            self.assertEqual(result.exit_code, 2, args)
            self.assertIn(message, result.output)

    def test_list_marks_the_configured_voice(self):
        (self.voices / 'myvoice.s2voice').write_bytes(b'v')
        (self.voices / 'myvoice.json').write_text('{"transcript": "こんにちは。", "seconds": 0.85}',
                                               encoding='utf-8')
        (self.voices / 'other.s2voice').write_bytes(b'v')
        self.config.write_text('voice = "myvoice"\n', encoding='utf-8')  # a name, resolved in voices/
        result = self.invoke('voice', 'list')
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('* myvoice  0.85 s  こんにちは。', result.output)
        self.assertIn('  other', result.output)
        self.assertIn('  default', result.output)

    def test_remove_deletes_profile_and_fillers(self):
        for suffix in ('.s2voice', '.wav', '.json'):
            (self.voices / f'myvoice{suffix}').write_bytes(b'x')
        (self.fillers / 'myvoice').mkdir(parents=True)
        self.assertEqual(self.invoke('voice', 'remove', 'myvoice', input='n\n').exit_code, 1)
        self.assertTrue((self.voices / 'myvoice.s2voice').exists())
        result = self.invoke('voice', 'remove', 'myvoice', '--yes')
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(list(self.voices.iterdir()), [])
        self.assertFalse((self.fillers / 'myvoice').exists())

    def test_say_starts_the_server_only_when_it_is_down(self):
        with patch('presenter_tts.cli.say_loop'), \
                patch('presenter_tts.server.reachable', side_effect=[False, False]), \
                patch('presenter_tts.server.start', return_value=True) as start:
            self.assertEqual(self.invoke('say', 'hi').exit_code, 0)
        start.assert_called_once()
        self.config.write_text('[server]\nauto_start = false\n', encoding='utf-8')
        with patch('presenter_tts.cli.say_loop'), \
                patch('presenter_tts.server.reachable', return_value=False), \
                patch('presenter_tts.server.start') as start:
            self.assertEqual(self.invoke('say', 'hi').exit_code, 0)
        start.assert_not_called()

    def test_server_start_reports_an_early_exit(self):
        from presenter_tts import server
        log = self.folder / 's2-server.log'
        log.write_text('Error: out of memory\n', encoding='utf-8')
        dead = type('Proc', (), {'pid': 1, 'poll': lambda self: 1})()
        with patch.object(server, 'LOG_FILE', log), \
                patch.object(server, 'PID_FILE', self.folder / 'pid'), \
                patch('presenter_tts.server.engine', return_value=('s2', 'm', 't', {})), \
                patch('presenter_tts.server.reachable', return_value=False), \
                patch('presenter_tts.server.subprocess.Popen', return_value=dead):
            result = self.invoke('server', 'start')
        self.assertEqual(result.exit_code, 2)
        self.assertIn('out of memory', result.output)
        self.assertFalse((self.folder / 'pid').exists())

    def test_server_stop_without_a_started_server(self):
        from presenter_tts import server
        with patch.object(server, 'PID_FILE', self.folder / 'pid'):
            result = self.invoke('server', 'stop')
        self.assertEqual(result.exit_code, 0)
        self.assertIn('No server started by this tool', result.output)

    def test_doctor_reports_a_missing_engine(self):
        with patch.object(voices, 'RUNTIME', self.folder / 'empty-cache'):
            result = self.invoke('doctor')
        self.assertEqual(result.exit_code, 0, result.output)
        report = json.loads(result.output)
        self.assertFalse(report['engine']['ready'])
        self.assertIn('setup-s2.ps1', report['engine']['missing'])
        self.assertIn('windows', report['prerequisites'])

    def test_normalize_raises_peak_to_minus_3_db(self):
        path = self.folder / 'quiet.wav'
        write_wav(path, [0, 3000, -6000, 1500])
        gain = voices.normalize(path)
        with wave.open(str(path)) as clip:
            peak = max(map(abs, array.array('h', clip.readframes(clip.getnframes()))))
        self.assertAlmostEqual(peak, 23197, delta=2)
        self.assertAlmostEqual(gain, 11.7, delta=.1)


if __name__ == '__main__':
    unittest.main()
