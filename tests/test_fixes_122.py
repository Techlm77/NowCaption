#!/usr/bin/env python3
"""Speech model v4 replaces v3. Every file that names the model folder, its release or its checksum
must agree with the folder the app loads, and nothing that builds or runs the app may still point at
an older model, so no checkout, Flatpak or AppImage can end up with v3 by mistake.

No models, GTK or audio needed; the checks against a downloaded model run only where ./models has it.
"""
import hashlib
import re
import subprocess
import unittest
from pathlib import Path

from nowcaption import engine

ROOT = Path(__file__).resolve().parents[1]
V4 = 'nowcaption-fastconformer-en-80ms-cc-by-v4'
MANIFEST = ROOT / 'io.github.Techlm77.NowCaption.yml'
FETCH = ROOT / 'tools/fetch-models.sh'
RELEASE = 'https://github.com/Techlm77/NowCaption/releases/download/'


def manifest_model():
    """(release tag, archive name, sha256) of the speech model archive in the Flatpak manifest."""
    m = re.search(r'url: ' + re.escape(RELEASE) + r'(models-v\d+)/(\S+)\n\s+sha256: ([0-9a-f]{64})', MANIFEST.read_text())
    return m.groups()


class ModelV4Tests(unittest.TestCase):
    def test_the_app_loads_v4(self):
        self.assertEqual(engine.ASR_DIR, V4)

    def test_the_flatpak_downloads_and_installs_the_folder_the_app_loads(self):
        tag, archive, _ = manifest_model()
        self.assertEqual((tag, archive), ('models-v4', V4 + '.tar.bz2'))
        self.assertIn(f'-t /app/share/nowcaption/models/{V4} asr/tokens.txt', MANIFEST.read_text())

    def test_fetch_models_gets_the_same_archive_as_the_flatpak(self):
        tag, _, sha = manifest_model()
        text = FETCH.read_text()
        self.assertIn(f'asr={V4}\n', text)
        self.assertIn(f'fetch "{RELEASE}{tag}/$asr.tar.bz2" \\\n      {sha} "$asr"', text)

    def test_appimage_and_run_dev_use_the_same_folder(self):
        for name in ('tools/build-appimage.sh', 'tools/run-dev.sh'):
            text = (ROOT / name).read_text()
            self.assertIn(f'models/{V4}/', text, name)
            self.assertEqual(set(re.findall(r'cc-by-v\d+', text)), {'cc-by-v4'}, name)

    def test_nothing_that_builds_or_runs_the_app_names_an_older_model(self):
        files = subprocess.run(['git', 'ls-files', 'src', 'tools', 'flatpak', '.github', MANIFEST.name],
                               cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        self.assertTrue(files)
        for name in files:
            path = ROOT / name
            if path.suffix in ('.png', '.svg', '.ico') or not path.is_file():
                continue
            text = path.read_text(errors='replace')
            self.assertEqual(set(re.findall(r'fastconformer-en-80ms-cc-by-v\d+', text)) - {V4[len('nowcaption-'):]}, set(), name)
            self.assertEqual(set(re.findall(r'releases/download/(models-v[\d.]+)/', text)) - {'models-v4'}, set(), name)

    def test_licenses_name_v4_its_release_card_and_teacher(self):
        text = (ROOT / 'MODEL-LICENSES.md').read_text()
        for needle in (V4, 'releases/tag/models-v4', 'MODEL-CARD-v4.md', 'parakeet-tdt-0.6b-v2'):
            self.assertIn(needle, text)
        self.assertTrue((ROOT / 'MODEL-CARD-v4.md').is_file())

    def test_model_card_lists_the_files_the_app_loads(self):
        card = (ROOT / 'MODEL-CARD-v4.md').read_text()
        listed = dict((name, digest) for digest, name in re.findall(r'^ +([0-9a-f]{64})  (\S+)$', card, re.M))
        self.assertEqual(set(listed), set(engine.ASR_FILES))

    def test_downloaded_model_matches_its_card(self):
        folder = ROOT / 'models' / V4
        if not engine.complete(folder, engine.ASR_FILES + ('README.md',)):
            self.skipTest('no complete ./models copy (tools/fetch-models.sh)')
        card = (ROOT / 'MODEL-CARD-v4.md').read_text()
        self.assertEqual((folder / 'README.md').read_text(), card, 'MODEL-CARD-v4.md is not the archive README.md')
        listed = dict((name, digest) for digest, name in re.findall(r'^ +([0-9a-f]{64})  (\S+)$', card, re.M))
        for name in engine.ASR_FILES:
            self.assertEqual(hashlib.sha256((folder / name).read_bytes()).hexdigest(), listed[name], name)

    def test_cached_archive_matches_the_pinned_checksum(self):
        _, archive, sha = manifest_model()
        for path in (ROOT / 'dist' / archive, ROOT / '.cache' / archive):
            if path.is_file():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), sha, path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
