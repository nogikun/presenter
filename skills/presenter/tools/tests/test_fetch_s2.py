import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('fetch_s2', Path(__file__).parents[1] / 'scripts/fetch_s2.py')
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)


class DownloadTests(unittest.TestCase):
    def test_revision_and_hash_are_verified(self):
        model, tokenizer = b'GGUF test model', b'{}'
        info = {'sha': 'a' * 40, 'siblings': [
            {'rfilename': 's2-pro-q4_k_m.gguf', 'size': len(model),
             'lfs': {'sha256': hashlib.sha256(model).hexdigest()}},
            {'rfilename': 'tokenizer.json', 'size': len(tokenizer)}]}
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            with patch('sys.argv', ['fetch', '--directory', directory]), \
                 patch.object(fetch.urllib.request, 'urlopen', side_effect=[
                     io.BytesIO(json.dumps(info).encode()), io.BytesIO(model), io.BytesIO(tokenizer)]) as request:
                self.assertEqual(fetch.main(), 0)
            self.assertIn('/resolve/' + 'a' * 40 + '/', request.call_args_list[1].args[0])
            path = Path(directory)
            self.assertEqual((path / 's2-pro-q4_k_m.gguf').read_bytes(), model)
            provenance = json.loads((path / 'provenance.json').read_text())
            self.assertEqual(provenance['revision'], 'a' * 40)

    def test_corrupt_download_does_not_replace_a_model(self):
        info = {'sha': 'a' * 40, 'siblings': [
            {'rfilename': 's2-pro-q4_k_m.gguf', 'lfs': {'sha256': '0' * 64}}]}
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            path = Path(directory) / 's2-pro-q4_k_m.gguf'
            path.write_bytes(b'old model')
            with patch('sys.argv', ['fetch', '--directory', directory]), \
                 patch.object(fetch.urllib.request, 'urlopen', side_effect=[
                     io.BytesIO(json.dumps(info).encode()), io.BytesIO(b'corrupt')]):
                self.assertEqual(fetch.main(), 2)
            self.assertEqual(path.read_bytes(), b'old model')
