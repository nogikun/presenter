import unittest

from presenter_tts import runtime


class RuntimeTests(unittest.TestCase):
    def test_platform_profiles_keep_build_outputs_and_defaults_together(self):
        windows = runtime.current('win32')
        macos = runtime.current('darwin')
        self.assertEqual(windows['executable'].as_posix(), 's2-build/Release/s2.exe')
        self.assertEqual(windows['accelerator'], 'cuda')
        self.assertEqual(macos['executable'].as_posix(), 's2-build/s2')
        self.assertEqual(macos['accelerator'], 'metal')

    def test_auto_accelerator_selects_cuda_or_metal(self):
        self.assertEqual(runtime.accelerator_args({}, 'win32'),
                         ['--cuda', '0', '--gpu-layers', '36'])
        self.assertEqual(runtime.accelerator_args({}, 'darwin'),
                         ['--metal', '--gpu-layers', '36'])
        self.assertEqual(runtime.codec_arg({}, 'win32'), '--codec-follow-backend')
        self.assertEqual(runtime.codec_arg({}, 'darwin'), '--codec-cpu')

    def test_accelerator_can_be_overridden(self):
        self.assertEqual(runtime.accelerator_args({
            'accelerator': 'cuda', 'gpu_device': 2, 'gpu_layers': 18}, 'darwin'),
            ['--cuda', '2', '--gpu-layers', '18'])
        self.assertEqual(runtime.accelerator_args({'accelerator': 'cpu'}, 'darwin'),
                         ['--gpu-layers', '0'])
        with self.assertRaisesRegex(ValueError, 'auto, cuda, metal, or cpu'):
            runtime.accelerator_args({'accelerator': 'mps'}, 'darwin')
        with self.assertRaisesRegex(ValueError, 'auto, cuda, metal, or cpu'):
            runtime.accelerator_args({'accelerator': 1}, 'darwin')

    def test_auto_threads_uses_performance_cores_on_macos_only(self):
        from unittest.mock import patch
        done = type('Done', (), {'stdout': '4\n'})()
        with patch('presenter_tts.runtime.subprocess.run', return_value=done) as run:
            self.assertEqual(runtime.auto_threads('darwin'), 4)
            self.assertEqual(runtime.auto_threads('win32'), 0)
        run.assert_called_once()
        with patch('presenter_tts.runtime.subprocess.run', side_effect=OSError):
            self.assertEqual(runtime.auto_threads('darwin'), 0)


if __name__ == '__main__':
    unittest.main()
