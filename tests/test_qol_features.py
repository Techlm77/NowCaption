#!/usr/bin/env python3
"""Issue #5 features: the word list (fixes for words the model mishears), how long transcripts are
kept, pausing, hiding the captions, the caption font, starting at login and the launch options.

The word list, history and autostart tests need only NumPy. The application tests need PyGObject
with GTK 4 and libadwaita and a display (run them under xvfb-run); they never call the desktop's
portals.

    PYTHONPATH=src .venv/bin/python tests/test_qol_features.py
"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

os.environ['XDG_CACHE_HOME'] = tempfile.mkdtemp()  # never the user's folders
os.environ['XDG_CONFIG_HOME'] = tempfile.mkdtemp()
os.environ['XDG_DATA_HOME'] = tempfile.mkdtemp()

from nowcaption import engine, words  # noqa: E402
from test_reliability import FakePunct, SILENCE, SPEECH  # noqa: E402
from test_fixes_115 import StrippedRecognizer  # noqa: E402

try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from nowcaption import app as nc
    from nowcaption import portal
    from nowcaption.ui import caption_window, options_window
except (ImportError, ValueError) as e:
    if os.environ.get('NOWCAPTION_REQUIRE_GUI_TESTS') == '1':
        raise
    nc = caption_window = options_window = portal = None
    SKIP = f'PyGObject with GTK 4 and libadwaita is not available: {e}'

NVIDIA = [('in video', 'Nvidia'), ('and video', 'Nvidia')]


class WordFixTests(unittest.TestCase):
    def test_a_misheard_phrase_is_replaced_only_as_whole_words(self):
        fixes = words.WordFixes(NVIDIA)
        self.assertEqual(fixes.heard('i bought an in video card'), 'i bought an nvidia card')
        self.assertEqual(fixes.heard('and video is faster'), 'nvidia is faster')
        self.assertEqual(fixes.heard('clips in videos'), 'clips in videos')  # "videos" is another word
        self.assertEqual(fixes.heard('within video'), 'within video')

    def test_spelling_keeps_punctuation_and_possessives(self):
        fixes = words.WordFixes([('', 'NVIDIA'), ('', 'KDE Plasma')])
        out = fixes.spelled('I like nvidia. "Nvidia\'s" drivers, kde plasma!'.split())
        self.assertEqual(out, ['I', 'like', 'NVIDIA.', '"NVIDIA\'s"', 'drivers,', 'KDE', 'Plasma!'])

    def test_a_fix_never_repeats_words_that_are_already_right(self):
        fixes = words.WordFixes([('visual studio', 'Visual Studio Code')])
        self.assertEqual(fixes.heard('open visual studio code'), 'open visual studio code')
        self.assertEqual(fixes.heard('open visual studio now'), 'open visual studio code now')

    def test_empty_fixes_are_ignored(self):
        self.assertFalse(words.WordFixes([('something', '  ')]))
        self.assertFalse(words.WordFixes())

    def test_the_list_is_saved_and_read_back(self):
        path = Path(tempfile.mkdtemp()) / 'nowcaption' / 'words.json'
        words.save_words(NVIDIA + [('', 'KDE')], path)
        self.assertEqual(words.load_words(path), NVIDIA + [('', 'KDE')])
        path.write_text('{not json')
        self.assertEqual(words.load_words(path), [])  # a damaged file never stops NowCaption
        self.assertEqual(words.load_words(path.with_name('missing.json')), [])


class CaptionerWordTests(unittest.TestCase):
    def say(self, script, fixes=()):
        rec = StrippedRecognizer(script)
        self.updates = []
        cap = engine.Captioner(rec, FakePunct(), on_update=self.updates.append, fixes=words.WordFixes(fixes))
        for _ in script:
            cap.feed(SPEECH)
        for _ in range(12):
            cap.feed(SILENCE)
        return cap

    def test_captions_and_transcript_show_the_fix(self):
        cap = self.say(['i', ' bought', ' an', ' in', ' video', ' card'], NVIDIA)
        self.assertIn('Nvidia card', self.updates[-1])
        self.assertTrue(cap.transcript().endswith('I bought an Nvidia card.'))

    def test_words_still_being_spoken_are_fixed_too(self):
        rec = StrippedRecognizer(['the', ' in', ' video'])
        updates = []
        cap = engine.Captioner(rec, FakePunct(), on_update=updates.append, fixes=words.WordFixes(NVIDIA))
        for _ in range(3):
            cap.feed(SPEECH)
        self.assertEqual(updates[-1], 'The Nvidia.')
        self.assertTrue(cap.transcript().endswith('The Nvidia.'))

    def test_a_new_list_applies_to_the_live_passage(self):
        rec = StrippedRecognizer(['my', ' nvidia'])
        updates = []
        cap = engine.Captioner(rec, FakePunct(), on_update=updates.append)
        for _ in range(2):
            cap.feed(SPEECH)
        self.assertEqual(updates[-1], 'My nvidia.')
        cap.set_fixes(words.WordFixes([('', 'NVIDIA')]))
        self.assertEqual(updates[-1], 'My NVIDIA.')

    def test_without_a_list_nothing_changes(self):
        cap = self.say(['i', ' bought', ' an', ' in', ' video', ' card'])
        self.assertTrue(cap.transcript().endswith('I bought an in video card.'))


class HistoryDaysTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        now = time.time()
        self.old = engine.history_file(now - 5 * 86400, self.dir)
        self.new = engine.history_file(now - 3600, self.dir)
        for path, age in ((self.old, 5 * 86400), (self.new, 3600)):
            path.write_text('[10:00:00] Hello.\n')
            os.utime(path, (now - age, now - age))

    def test_a_shorter_time_deletes_older_sessions(self):
        engine.prune_history(self.dir, days=1)
        self.assertFalse(self.old.exists())
        self.assertTrue(self.new.exists())

    def test_until_deleted_keeps_everything(self):
        engine.prune_history(self.dir, days=0)
        self.assertTrue(self.old.exists() and self.new.exists())
        self.assertEqual(engine.history_cutoff(0), float('-inf'))

    def test_the_span_reads_naturally(self):
        self.assertEqual([engine.history_span(d) for d in engine.HISTORY_CHOICES],
                         ['1 day', '7 days', '30 days', '90 days', None])


@unittest.skipIf(portal is None, globals().get('SKIP', ''))
class AutostartTests(unittest.TestCase):
    def test_exec_quotes_paths_with_spaces(self):
        self.assertEqual(portal.desktop_exec(['/home/me/My Apps/NowCaption.AppImage']),
                         '"/home/me/My Apps/NowCaption.AppImage"')
        self.assertEqual(portal.desktop_exec(['nowcaption']), 'nowcaption')
        self.assertEqual(portal.desktop_exec(['a$b']), '"a\\$b"')

    def test_outside_the_flatpak_a_file_is_written_and_removed(self):
        with patch.dict(os.environ, XDG_CONFIG_HOME=tempfile.mkdtemp(), APPIMAGE='/opt/Now Caption.AppImage'), \
                patch.object(portal, 'in_flatpak', lambda: False):
            auto = portal.Autostart(None, 'io.github.Techlm77.NowCaption')
            results = []
            auto.set(True, lambda on, message: results.append((on, message)))
            text = auto.path().read_text()
            self.assertIn('Exec="/opt/Now Caption.AppImage"\n', text)
            auto.set(False, lambda on, message: results.append((on, message)))
            self.assertFalse(auto.path().exists())
            self.assertEqual(results, [(True, ''), (False, '')])


@unittest.skipIf(nc is None, globals().get('SKIP', ''))
class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_window import shared_app
        cls.app = shared_app()

    def setUp(self):
        self.window = caption_window.CaptionWindow(self.app)
        self.app.window = self.window
        self.calls = []
        self.app.capture = NS(start=lambda device, gen: self.calls.append(('start', gen)),
                              stop=lambda: self.calls.append(('stop',)))
        self.app.worker = NS(set_source=lambda gen: self.calls.append(('source', gen)),
                             set_words=lambda fixes: self.calls.append(('words', fixes.fixes)))
        self.app.ready = True

    def tearDown(self):
        self.app.set_paused(False)
        self.app.worker = None
        self.app.ready = False
        if self.app.hidden:
            self.app.toggle_captions()
        self.window.disconnect_monitors()
        self.window.destroy()
        self.app.window = None
        self.app.settings.set('history_days', engine.HISTORY_DAYS)
        self.app.lookup_action('history-days').change_state(nc.GLib.Variant('s', str(engine.HISTORY_DAYS)))
        self.app.settings.set('font', '')
        self.app.lookup_action('font').change_state(nc.GLib.Variant('s', ''))
        if self.app.options:
            self.app.options.close()

    def test_pause_stops_capture_and_resume_listens_again(self):
        gen = self.app.source_gen
        self.app.activate_action('pause', None)
        self.assertTrue(self.app.paused)
        self.assertEqual(self.calls, [('source', gen + 1), ('stop',)])
        self.assertIn('paused', self.window.status)
        self.app.start_capture()  # a source change or Clear while paused does not listen
        self.assertEqual(len(self.calls), 2)
        self.app.activate_action('pause', None)
        self.assertFalse(self.app.paused)
        self.assertEqual(self.calls[2:], [('source', gen + 2), ('start', gen + 2)])
        self.assertNotIn('paused', self.window.status)

    def test_hiding_keeps_the_window_and_starting_again_shows_it(self):
        self.window.present()
        self.app.activate_action('toggle', None)
        self.assertTrue(self.app.hidden)
        self.assertFalse(self.window.get_visible())
        self.app.on_activate(self.app)  # NowCaption started again
        self.assertFalse(self.app.hidden)
        self.assertTrue(self.window.get_visible())

    def test_toggle_and_pause_only_act_on_a_running_nowcaption(self):
        done = []
        options = nc.GLib.VariantDict.new(None)
        options.insert_value('toggle', nc.GLib.Variant('b', True))
        line = NS(get_options_dict=lambda: options)
        self.app.window = None
        with patch.object(self.app, 'activate', lambda: done.append('activate')), \
                patch.object(self.app, 'activate_action', lambda name, value: done.append(name)):
            self.app.on_command_line(self.app, line)
        self.assertEqual(done, ['activate'])  # starting it shows the captions; it does not hide them

    def test_history_days_apply_with_apply_and_change_the_menu(self):
        self.app.open_options()
        options = self.app.options
        options.chosen.lookup_action('history-days').activate(nc.GLib.Variant('s', '30'))
        self.assertEqual(self.app.history_days(), 7)  # not before Apply
        options.apply()
        self.assertEqual(self.app.history_days(), 30)
        menu = self.app.build_menu()
        text = []
        for i in range(menu.get_n_items()):
            section = menu.get_item_link(i, 'section')
            for j in range(section.get_n_items()):
                label = section.get_item_attribute_value(j, 'label', None)
                if label:
                    text.append(label.get_string())
        self.assertIn('Keep 30 days of history', text)
        self.assertIn('Pause captions', text)

    def test_the_font_shows_in_the_sample_first_then_the_captions(self):
        self.app.open_options()
        options = self.app.options
        options.chosen.lookup_action('font').activate(nc.GLib.Variant('s', 'DejaVu Serif'))
        self.assertIn('"DejaVu Serif"', options.sample_css.to_string())
        self.assertEqual(self.app.settings.get('font'), '')
        options.apply()
        self.assertEqual(self.app.settings.get('font'), 'DejaVu Serif')
        self.assertEqual(self.window.look[1], 'DejaVu Serif')
        self.assertIn('"DejaVu Serif"', self.window.css.to_string())

    def test_more_options_names_the_command_this_install_has(self):
        OW = options_window.OptionsWindow
        with patch.object(portal, 'in_flatpak', lambda: True):
            self.assertEqual(OW.launch_command(), 'flatpak run io.github.Techlm77.NowCaption')
        with patch.object(portal, 'in_flatpak', lambda: False), patch.dict(os.environ, APPIMAGE='/opt/NowCaption.AppImage'):
            self.assertEqual(OW.launch_command(), '/opt/NowCaption.AppImage')
        with patch.object(portal, 'in_flatpak', lambda: False), patch.dict(os.environ, clear=False) as env:
            env.pop('APPIMAGE', None)
            self.assertEqual(OW.launch_command(), 'nowcaption')

    def test_word_fixes_added_in_more_options_reach_the_worker_and_the_file(self):
        self.app.open_options()
        options = self.app.options
        options.heard_entry.set_text('in  video')
        options.write_entry.set_text('Nvidia')
        options.add_fix()
        self.assertEqual(self.app.word_list, [('in video', 'Nvidia')])
        self.assertEqual(words.load_words(), [('in video', 'Nvidia')])
        self.assertEqual(self.calls[-1], ('words', [('in video', 'Nvidia')]))
        self.assertEqual(len(options.fix_rows), 1)
        options.remove_fix(('in video', 'Nvidia'))
        self.assertEqual(words.load_words(), [])
        self.assertEqual(len(options.fix_rows), 0)


if __name__ == '__main__':
    unittest.main()
