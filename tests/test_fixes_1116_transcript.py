#!/usr/bin/env python3
"""1.1.16 transcript window: while Find has text, live captions search only their new lines
again, not the whole history; a closed window is freed; and the text has a caret, so it can be
selected with the keyboard.

Constructs TranscriptWindow on a display like test_window.py, whose application it shares (a
second one could not register). No models or audio pipeline are started.
"""
import gc
import re
import time
import unittest
import weakref
from types import SimpleNamespace as NS
from unittest.mock import patch

import test_window
from test_window import shared_app

nc = test_window.nc


def settle():
    context = nc.GLib.MainContext.default()
    for _ in range(50):
        while context.pending():
            context.iteration(False)


@unittest.skipIf(nc is None, getattr(test_window, 'SKIP', ''))
class TranscriptFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        self.app.settings.set('history', True)
        for _, path in nc.engine.history_files():
            path.unlink()
        folder = nc.engine.history_dir()
        folder.mkdir(parents=True, exist_ok=True)
        now = time.time()
        self.older_start, self.newer_start = now - 2 * 86400, now - 3600
        self.older = nc.engine.history_file(self.older_start, folder)
        self.newer = nc.engine.history_file(self.newer_start, folder)
        self.older.write_text('[09:00:01] Good morning everyone.\n[09:00:05] Bright and early today.\n')
        self.newer.write_text('[13:00:00] Welcome back.\n')
        self.window = nc.TranscriptWindow(self.app)
        self.addCleanup(self.window.destroy)

    def tearDown(self):
        for _, path in nc.engine.history_files():  # the tests after these expect no earlier sessions
            path.unlink()

    def text(self):
        return self.window.buffer.get_text(*self.window.buffer.get_bounds(), False)

    def offset(self, mark):
        return self.window.buffer.get_iter_at_mark(mark).get_offset()

    def caret(self):
        return self.offset(self.window.buffer.get_insert())

    def toggles(self, tag):
        """Where tag starts and ends, in order."""
        offsets, where = [], self.window.buffer.get_start_iter()
        while where.forward_to_tag_toggle(tag):
            offsets.append(where.get_offset())
        return offsets

    def assert_as_a_full_search(self, word):
        """The matches, their highlights, the current one and the count are what searching the
        whole transcript again would give."""
        w = self.window
        found = [(m.start(), m.end()) for m in re.finditer(re.escape(word), self.text(), re.IGNORECASE)]
        self.assertEqual([(self.offset(a), self.offset(b)) for a, b in w.matches], found)
        self.assertEqual(self.toggles(w.match_tag), [offset for match in found for offset in match])
        self.assertEqual(self.toggles(w.current_tag), list(found[w.match]))
        self.assertEqual(w.count.get_label(), f'{w.match + 1} of {len(found)}')

    def test_new_lines_are_searched_without_searching_the_history_again(self):
        w = self.window
        w.apply('[14:05:20] A bright idea.')
        w.entry.set_text('bright')
        w.find()
        earlier = w.matches[0]  # in an earlier session
        searched, search = [], nc.Gtk.TextIter.forward_search

        def spy(where, *args):
            searched.append(where.get_offset())
            return search(where, *args)
        new_line = len(self.text())
        with patch.object(nc.Gtk.TextIter, 'forward_search', spy):
            w.apply('[14:05:20] A bright idea.\n[14:05:31] Bright lights, bright')
        self.assertIs(w.matches[0], earlier, 'the earlier matches are kept, not found again')
        self.assertEqual(min(searched), new_line, 'only the new line is searched')
        self.assert_as_a_full_search('bright')

    def test_the_current_match_stays_on_its_words_when_its_line_is_replaced(self):
        w = self.window
        w.apply('[14:05:20] Bright and')
        w.entry.set_text('bright')
        w.find()  # the newest: in this session's line, still being spoken
        self.assertEqual(w.count.get_label(), '2 of 2')
        current = self.offset(w.matches[w.match][0])
        w.apply('[14:05:20] Bright and sunny, very bright')
        self.assertEqual(w.count.get_label(), '2 of 3')
        self.assertEqual(self.offset(w.matches[w.match][0]), current)
        self.assert_as_a_full_search('bright')
        w.step(1)  # Enter
        self.assertEqual(w.count.get_label(), '3 of 3')
        self.assert_as_a_full_search('bright')
        w.step(1)
        w.step(-1)  # Shift+Enter
        w.step(-1)
        self.assertEqual(w.count.get_label(), '2 of 3')
        self.assert_as_a_full_search('bright')
        w.apply('')  # Clear: the current match went with this session
        self.assertEqual(w.count.get_label(), '1 of 1')
        self.assert_as_a_full_search('bright')

    def test_a_current_match_in_the_history_stays_highlighted_as_new_lines_arrive(self):
        w = self.window
        w.apply('[14:05:20] Bright')
        w.entry.set_text('bright')
        w.find()
        w.step(1)  # round to the first, in an earlier session
        caret = self.caret()
        w.apply('[14:05:20] Bright, bright')
        self.assertEqual(w.count.get_label(), '1 of 3')
        self.assertEqual(self.caret(), caret, 'new lines leave the caret alone')
        self.assert_as_a_full_search('bright')

    def test_a_caption_before_new_search_text_is_searched_finds_only_the_new_text(self):
        w = self.window
        w.apply('[14:05:20] A bright idea.')
        w.entry.set_text('bright')
        w.find()
        w.entry.set_text('idea')  # GTK waits a moment before it says the search changed
        w.apply('[14:05:20] A bright idea.\n[14:05:31] Another idea')
        self.assert_as_a_full_search('idea')

    def test_a_search_across_lines_still_finds_a_match_that_begins_in_a_kept_line(self):
        w = self.window
        w.apply('[14:05:20] Hello there.')
        w.entry.set_text('there.\n[14:05:26] fine')
        w.find()
        self.assertEqual(w.count.get_label(), 'No matches')
        w.apply('[14:05:20] Hello there.\n[14:05:26] Fine')
        self.assertEqual(w.count.get_label(), '1 of 1')

    def test_deleting_the_history_drops_its_matches(self):
        w = self.window
        self.app.worker = NS(clear=lambda gen: None, set_history=lambda path: None,
                             snapshot=lambda fn, on_error=None: None)
        self.app.window = NS(show_text=lambda text: None)
        self.app.transcript = w
        self.app.ready = self.app.closing = self.app.quit_requested = False
        for name in ('worker', 'window', 'transcript'):
            self.addCleanup(setattr, self.app, name, None)
        w.apply('[14:05:20] Bright')
        w.entry.set_text('bright')
        w.find()
        self.app.delete_history()
        self.assertEqual(w.count.get_label(), 'No matches')
        self.assertEqual(w.matches, [])
        w.apply('[14:06:00] Bright again')
        self.assert_as_a_full_search('bright')
        self.assertEqual(w.count.get_label(), '1 of 1')

    def test_a_closed_window_is_freed(self):
        self.app.open_transcript()
        self.addCleanup(lambda: self.app.transcript and self.app.transcript.close())
        w = self.app.transcript
        w.apply('[14:05:20] Bright and early.')
        w.entry.set_text('bright')
        w.find()
        w.changed()  # a refresh is due
        buffer, window = w.buffer, weakref.ref(w)
        w.close()
        settle()
        self.assertIsNone(self.app.transcript)
        self.assertIsNone(w.refresh_id)
        self.assertEqual(buffer.get_char_count(), 0, 'its text is let go of at once')
        del w
        gc.collect()
        self.assertIsNone(window(), 'nothing GTK holds keeps the closed window, or the theme handler, alive')

    def test_the_text_has_a_caret_so_the_keyboard_can_select_it(self):
        w = self.window
        self.assertTrue(w.view.get_cursor_visible())
        self.assertFalse(w.view.get_editable())
        w.present()
        settle()
        w.view.grab_focus()
        w.buffer.place_cursor(w.buffer.get_start_iter())
        w.view.emit('move-cursor', nc.Gtk.MovementStep.DISPLAY_LINES, 1, True)  # Shift+Down
        selected = w.buffer.get_selection_bounds()
        self.assertTrue(selected, 'Shift+Down selects instead of scrolling')
        self.assertEqual(w.buffer.get_text(*selected, False), nc.session_heading(int(self.older_start)) + '\n')

    def test_find_puts_the_caret_on_the_match_it_goes_to(self):
        w = self.window
        w.apply('[14:05:20] A bright idea.')
        w.entry.set_text('bright')
        w.find()
        self.assertEqual(self.caret(), self.offset(w.matches[1][0]))
        w.step(1)
        self.assertEqual(self.caret(), self.offset(w.matches[0][0]))


if __name__ == '__main__':
    unittest.main()
