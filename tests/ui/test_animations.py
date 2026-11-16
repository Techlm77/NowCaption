#!/usr/bin/env python3
"""Animations, with the desktop's animations on: the box fading in and out, the rows gliding up,
and new words arriving with the chosen effect (fade, the pixel effects, none). On the test
display; no models or audio pipeline are started.

AnimationTests come from the first window tests, EffectFixTests from 1.1.16.
"""
import time
import unittest
from unittest.mock import patch

import numpy as np

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import style
from support.windows import SKIP, nc, shared_app

if nc:  # GTK and a display are there
    import cairo
    from gi.repository import Gdk, GObject, Graphene, Gsk, Pango

    from nowcaption.ui import caption_text, caption_window


@unittest.skipIf(nc is None, globals().get('SKIP', ''))
class AnimationTests(unittest.TestCase):
    """With the desktop's animations on, on a window that is shown. Frames come too slowly on
    a headless display to wait for them, so the caption rows are shown as of a chosen time,
    and fades are checked started, then skipped to their end."""

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        self.gtk = nc.Gtk.Settings.get_default()
        self.gtk.set_property('gtk-enable-animations', True)
        self.addCleanup(self.gtk.set_property, 'gtk-enable-animations', False)
        self.app.settings.set('size', 30)
        self.window = caption_window.CaptionWindow(self.app)
        self.app.window = self.window
        self.window.present()
        self.settle()
        self.finish(self.window.box)

    def tearDown(self):
        self.window.disconnect_monitors()
        self.window.destroy()
        self.app.window = None

    def settle(self):
        context = nc.GLib.MainContext.default()
        for _ in range(50):
            while context.pending():
                context.iteration(False)

    def finish(self, widget):
        """Skip a running fade to its end."""
        animation = self.window.fades.get(widget)
        if animation:
            animation.skip()

    def at(self, ms):
        """Show the caption rows as they are ms after now."""
        self.window.caption.update(nc.GLib.get_monotonic_time() + ms * 1000)

    def passage(self, rows):
        """Text that fills `rows` caption rows, and a word that starts the next row."""
        words = []
        wrap = self.window.caption.wrapper.greedy
        while len(wrap(words + [f'word{len(words)}'], 0, len(words) + 1)) <= rows:
            words.append(f'word{len(words)}')
        return ' '.join(words), f'word{len(words)}'

    def test_the_caption_box_fades_in_when_it_first_appears(self):
        window = caption_window.CaptionWindow(self.app)
        self.addCleanup(window.destroy)
        self.addCleanup(window.disconnect_monitors)
        window.present()
        self.settle()
        self.assertIn(window.box, window.fades)
        self.assertLess(window.box.get_opacity(), 1.0)
        window.fades[window.box].skip()
        self.assertEqual(window.box.get_opacity(), 1.0)

    def test_a_new_row_glides_up_and_its_first_word_fades_in(self):
        w = self.window
        text, word = self.passage(3)
        w.show_text(text)
        self.at(500)
        rows = w.label.get_text().splitlines()
        self.assertEqual(len(rows), w.rows)
        w.show_text(f'{text} {word}')
        # the rows stay where they were, with the new row waiting below them
        self.assertEqual(w.label.get_text().splitlines(), rows + [word])
        self.assertEqual(w.caption.offset, 0)
        spans = [(a.start_index, a.end_index) for a in w.label.get_attributes().get_attributes()]
        shown = w.label.get_text().encode()
        self.assertEqual([shown[s:e] for s, e in spans], [word.encode()])
        self.at(style.GLIDE_MS / 2)
        self.assertGreater(w.caption.offset, 0)
        self.assertLess(w.caption.offset, w.row_height)
        self.at(style.GLIDE_MS + 50)
        self.assertEqual(w.label.get_text().splitlines(), rows[1:] + [word])
        self.assertEqual(w.caption.offset, 0)
        self.assertIsNone(w.label.get_attributes())

    def test_a_glide_ends_exactly_where_the_rows_come_to_rest(self):
        w = self.window
        text, word = self.passage(3)
        w.show_text(text)
        self.at(500)
        w.show_text(f'{text} {word}')
        self.at(style.GLIDE_MS - 1)  # the glide's last moment, a row up
        layout = w.label.get_layout()
        first, second = (layout.index_to_pos(line.start_index).y / Pango.SCALE
                         for line in layout.get_lines_readonly()[:2])
        # moved up by exactly one line of the label, so dropping that line at the end moves nothing
        self.assertAlmostEqual(w.caption.offset, second - first, delta=0.01)

    def test_without_animations_the_rows_jump_and_words_appear_at_once(self):
        self.gtk.set_property('gtk-enable-animations', False)
        w = self.window
        text, word = self.passage(3)
        w.show_text(text)
        rows = w.label.get_text().splitlines()
        w.show_text(f'{text} {word}')
        self.assertEqual(w.label.get_text().splitlines(), rows[1:] + [word])
        self.assertEqual(w.caption.offset, 0)
        self.assertIsNone(w.label.get_attributes())

    def test_corrected_punctuation_does_not_fade_words_again(self):
        w = self.window
        w.show_text('so we took the long road back')
        self.at(500)
        w.show_text('So we took the long road back,')
        self.assertIsNone(w.label.get_attributes())

    def test_the_status_row_fades_out_before_the_box_shrinks(self):
        w = self.window
        w.show_text('Words that stay')
        w.show_status('Transcript copied.')
        self.assertLess(w.status_label.get_opacity(), 1.0)
        self.finish(w.status_label)
        self.assertEqual(w.status_label.get_opacity(), 1.0)
        w.end_status()
        self.assertTrue(w.status_label.get_visible())
        self.finish(w.status_label)
        self.assertFalse(w.status_label.get_visible())
        self.assertEqual(w.box_height(), w.caption_height())

    def test_a_message_returning_while_the_row_fades_out_stays(self):
        w = self.window
        w.show_text('Words that stay')
        w.show_status('Transcript copied.')
        self.finish(w.status_label)
        w.end_status()
        w.show_status('Transcript copied.')
        self.finish(w.status_label)
        self.assertTrue(w.status_label.get_visible())
        self.assertEqual(w.status_label.get_opacity(), 1.0)

    def test_listening_breathes_until_captions_start(self):
        w = self.window
        w.show_status('Listening to desktop audio…')
        self.at(style.BREATH_MS / 2)
        self.assertLess(w.label.get_opacity(), 0.6)
        w.show_text('first words')
        self.assertEqual(w.label.get_opacity(), 1.0)
        w.show_text('')
        w.show_failure('capture', 'Device unavailable. Restarting in 2 s…')
        self.at(style.BREATH_MS / 2)
        self.assertEqual(w.label.get_opacity(), 1.0, 'a problem breathed like a wait')

    def test_a_new_text_colour_blends_in(self):
        end = time.monotonic() + 0.5  # CSS transitions start once the label has been drawn
        while time.monotonic() < end:
            nc.GLib.MainContext.default().iteration(False)
            time.sleep(0.005)
        self.addCleanup(self.app.activate_action, 'colour', nc.GLib.Variant('s', 'white'))
        self.app.activate_action('colour', nc.GLib.Variant('s', 'yellow'))
        self.assertGreater(self.window.label.get_color().blue, 0.0, 'the colour changed at once')

    def test_quitting_fades_the_box_out_first(self):
        self.addCleanup(setattr, self.app.quitting, 'fading_out', False)
        with patch.object(nc.Adw.Application, 'quit') as quit:
            self.app.quitting.quit()
            self.assertEqual(quit.call_count, 0)
            self.finish(self.window.box)
            self.assertEqual(self.window.box.get_opacity(), 0.0)
            self.assertEqual(quit.call_count, 1)
            self.app.quitting.quit()  # the safety timer, or a second Quit, while the box is gone
            self.assertEqual(quit.call_count, 2)

    def effect(self, effect, speed='normal'):
        for name, value, default in (('word-effect', effect, 'fade'), ('word-speed', speed, 'normal')):
            self.app.lookup_action(name).activate(nc.GLib.Variant('s', value))
            self.addCleanup(self.app.lookup_action(name).activate, nc.GLib.Variant('s', default))

    def alphas(self):
        """The label's arriving words, with their alpha (1 is hidden, 65535 opaque)."""
        shown, attrs = self.window.label.get_text().encode(), self.window.label.get_attributes()
        return [(shown[a.start_index:a.end_index].decode(), a.as_int().value)
                for a in attrs.get_attributes()] if attrs else []

    def test_a_pixel_effect_draws_the_new_word_in_pieces_until_they_land(self):
        self.effect('assemble')
        w = self.window
        w.show_text('so we took the long road')
        self.at(1000)
        w.show_text('so we took the long road back')
        self.at(style.PIXEL_MS / 2)
        self.assertEqual(self.alphas(), [('back', 1)])  # hidden in the label, drawn as pieces
        [(pieces, p)] = w.caption.drawing
        self.assertGreater(len(pieces.x), 20)
        self.assertAlmostEqual(p, 0.5, delta=0.1)
        self.at(style.PIXEL_MS + 50)
        self.assertIsNone(w.label.get_attributes())
        self.assertEqual(w.caption.drawing, [])

    def test_a_whole_passage_at_once_fades_in_instead_of_flying_in_pieces(self):
        self.effect('rain')
        w = self.window
        w.show_text(' '.join(f'w{i}' for i in range(style.PIXEL_WORDS + 1)))
        self.at(style.PIXEL_MS / 2)
        self.assertEqual(w.caption.drawing, [])
        alphas = self.alphas()
        self.assertEqual(len(alphas), style.PIXEL_WORDS + 1)
        self.assertTrue(all(1 < alpha < 65535 for _, alpha in alphas))

    def test_mosaic_blends_into_the_label_at_the_end(self):
        self.effect('mosaic')
        w = self.window
        w.show_text('so we took the long road')
        self.at(1000)
        w.show_text('so we took the long road back')
        self.at(style.PIXEL_MS * 0.4)
        self.assertEqual(self.alphas(), [('back', 1)])
        self.at(style.PIXEL_MS * 0.9)
        [(word, alpha)] = self.alphas()
        self.assertTrue(1 < alpha < 65535)
        self.assertEqual(len(w.caption.drawing), 1)

    def test_none_shows_new_words_at_once(self):
        self.effect('none')
        w = self.window
        w.show_text('so we took the long road')
        self.at(1000)
        w.show_text('so we took the long road back')
        self.assertIsNone(w.label.get_attributes())
        self.assertEqual((w.caption.appeared, w.caption.drawing), ({}, []))
        self.assertIn('back', w.label.get_text())

    def test_the_speed_scales_how_long_new_words_take(self):
        self.effect('dissolve', 'slow')
        self.assertEqual(self.window.caption.word_us(), style.PIXEL_MS * 1.75 * 1000)
        self.effect('fade', 'fast')
        self.assertEqual(self.window.caption.word_us(), style.WORD_MS * 0.6 * 1000)
        self.effect('fade')
        self.assertEqual(self.window.caption.word_us(), style.WORD_MS * 1000)  # as before effects

    def test_landed_pieces_are_exactly_the_word(self):
        label = self.window.label
        layout = label.create_pango_layout('so we took')
        pieces = caption_text.Pieces(layout, label.get_color(), (25.3, 0, 40, 30), 1, 36)

        def image(effect, p):
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 140)
            cr = cairo.Context(surface)
            if effect:
                pieces.draw(cr, effect, p, 50, 50)
            else:
                cr.set_source_surface(pieces.surface, 50 + pieces.left, 50 + pieces.top)
                cr.paint()
            surface.flush()
            return bytes(surface.get_data())

        word = image(None, 1)
        self.assertNotEqual(word, bytes(len(word)))
        for effect in ('assemble', 'dissolve', 'rain'):
            self.assertEqual(image(effect, 0.999), word, effect)
            self.assertNotEqual(image(effect, 0.3), word, effect)

    def test_pixel_effects_make_a_word_readable_in_under_half_their_time(self):
        label = self.window.label
        layout = label.create_pango_layout('headlights')
        w, h = layout.get_pixel_size()
        pieces = caption_text.Pieces(layout, label.get_color(), (0.3, 0, w, h), 1, 40)
        for effect in ('assemble', 'dissolve', 'rain'):
            shown, ox, oy, alpha = pieces.state(effect, 0.5)  # 90% in place, at least half opaque
            self.assertGreaterEqual((shown & (np.hypot(ox, oy) <= 1) & (alpha >= 0.5)).mean(), 0.9, effect)
            shown, ox, oy, alpha = pieces.state(effect, 0.75)  # and all of it well before the label
            self.assertTrue(shown.all() and not ox.any() and not oy.any() and (alpha == 1).all(), effect)
        self.assertGreaterEqual(style.MOSAIC[int(0.33 / caption_text.Pieces.SHARP * len(style.MOSAIC))],
                                0.36)  # mosaic: a third


@unittest.skipIf(nc is None, SKIP)
class EffectFixTests(unittest.TestCase):
    """With the desktop's animations on, on a window that is shown, the caption rows shown as
    of a chosen time, as in AnimationTests."""

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        self.gtk = nc.Gtk.Settings.get_default()
        self.gtk.set_property('gtk-enable-animations', True)
        self.addCleanup(self.gtk.set_property, 'gtk-enable-animations', False)
        self.app.settings.set('size', 30)
        self.window = caption_window.CaptionWindow(self.app)
        self.app.window = self.window
        self.window.present()
        self.settle()
        self.finish(self.window.box)

    def tearDown(self):
        self.window.disconnect_monitors()
        self.window.destroy()
        self.app.window = None

    def settle(self):
        context = nc.GLib.MainContext.default()
        for _ in range(50):
            while context.pending():
                context.iteration(False)

    def finish(self, widget):
        """Skip a running fade to its end."""
        animation = self.window.fades.get(widget)
        if animation:
            animation.skip()

    def at(self, ms):
        """Show the caption rows as they are ms after now."""
        self.window.caption.update(nc.GLib.get_monotonic_time() + ms * 1000)

    def passage(self, rows):
        """Text that fills `rows` caption rows, and a word that starts the next row."""
        words = []
        wrap = self.window.caption.wrapper.greedy
        while len(wrap(words + [f'word{len(words)}'], 0, len(words) + 1)) <= rows:
            words.append(f'word{len(words)}')
        return ' '.join(words), f'word{len(words)}'

    def effect(self, effect, speed='normal'):
        for name, value, default in (('word-effect', effect, 'fade'), ('word-speed', speed, 'normal')):
            self.app.lookup_action(name).activate(nc.GLib.Variant('s', value))
            self.addCleanup(self.app.lookup_action(name).activate, nc.GLib.Variant('s', default))

    def flying(self, word):
        """The Pieces of a word in flight, and how far through it is."""
        [(pieces, p)] = [(pieces, p) for pieces, p in self.window.caption.drawing if pieces.key[0] == word]
        return pieces, p

    def shown(self, pieces, effect, p):
        """The cells of the word whose pieces show."""
        return set(pieces.spot[pieces.place(effect, p)[0]].tolist())

    def render(self):
        """The caption box as GTK draws it, as bytes."""
        w = self.window
        width = w.box.measure(nc.Gtk.Orientation.HORIZONTAL, -1)[1]
        height = w.box.measure(nc.Gtk.Orientation.VERTICAL, width)[1]
        w.box.allocate(width, height, -1, None)
        snap = nc.Gtk.Snapshot()
        w.drawer.snapshot_child(w.box, snap)
        renderer = Gsk.CairoRenderer()
        renderer.realize_for_display(Gdk.Display.get_default())
        try:
            texture = renderer.render_texture(snap.to_node(), Graphene.Rect().init(0, 0, width, height))
        finally:
            renderer.unrealize()
        return Gdk.TextureDownloader.new(texture).download_bytes()[0].get_data()

    # Pieces jumped mid-flight: a word cut again started new random paths

    def test_pieces_fly_on_when_the_rows_finish_gliding(self):
        self.effect('dissolve', 'slow')
        w = self.window
        text, word = self.passage(3)
        w.show_text(text)
        self.at(1000)
        w.show_text(f'{text} {word}')  # a new row: the rows glide up
        w.show_text(f'{text} {word} next')  # and a word arrives while they glide
        w.caption.glide_start -= 150 * 1000  # the glide began 150 ms ago
        self.at(30)
        before, p = self.flying('next')
        self.at(90)  # the glide is over: the label dropped its top row, so the word moved up in it
        after, q = self.flying('next')
        self.assertIsNot(after, before)
        self.assertEqual(w.caption.offset, 0)
        self.assertLess(p, q)
        self.assertLess(q, 0.5)  # dissolve still switching pieces on
        common, mine, theirs = np.intersect1d(after.spot, before.spot, return_indices=True)
        self.assertGreater(len(common), 0.9 * len(before.spot))
        for name in ('dx', 'dy', 'delay', 'order', 'drop'):
            self.assertTrue((getattr(after, name)[mine] == getattr(before, name)[theirs]).all(), name)
        on = self.shown(before, 'dissolve', p)
        self.assertTrue(on)
        self.assertLessEqual(on & set(common.tolist()), self.shown(after, 'dissolve', q),
                             'pieces already shown switched off again')

    def test_pieces_fly_on_while_the_text_colour_changes(self):
        self.effect('assemble', 'slow')
        w = self.window
        w.show_text('so we took the long road')
        self.at(1000)
        w.show_text('so we took the long road back')
        self.at(60)
        before, p = self.flying('back')
        blending = Gdk.RGBA()
        blending.parse('#ffff80')  # part of the way to yellow
        with patch.object(w.label, 'get_color', return_value=blending):
            self.at(80)
            after, q = self.flying('back')
        self.assertIsNot(after, before)
        self.assertEqual(after.spot.tolist(), before.spot.tolist())
        for name in ('dx', 'dy', 'delay', 'order', 'drop'):
            self.assertTrue((getattr(after, name) == getattr(before, name)).all(), name)
        for old, new in zip(before.state('assemble', q), after.state('assemble', q)):
            self.assertTrue((old == new).all())  # each piece goes on from where it was

    # None applied while words arrive

    def test_choosing_none_while_words_arrive_shows_them_at_once(self):
        self.effect('dissolve', 'slow')
        w = self.window
        w.show_text('so we took the long road')
        self.at(1000)
        w.show_text('so we took the long road back')
        self.at(style.PIXEL_MS / 2)
        self.assertTrue(w.caption.drawing)
        self.effect('none')
        self.assertEqual((w.caption.appeared, w.caption.drawing), ({}, []))
        self.assertIsNone(w.label.get_attributes())
        self.at(-50)  # a frame whose time is behind the clock that timed the word
        self.assertIsNone(w.label.get_attributes())

    def test_a_word_timed_after_the_frame_does_not_stop_the_animations(self):
        self.effect('none')
        w = self.window
        w.show_text('so we took the long road back')
        w.caption.appeared = {6: nc.GLib.get_monotonic_time() + 50 * 1000}
        self.at(0)  # no ZeroDivisionError

    # The desktop turning animations off

    def test_listening_stops_breathing_when_animations_are_turned_off(self):
        w = self.window
        w.show_status('Listening to desktop audio…')
        self.at(style.BREATH_MS / 2)
        self.assertLess(w.label.get_opacity(), 0.6)
        self.assertTrue(w.caption.breath_timer)
        self.gtk.set_property('gtk-enable-animations', False)
        self.assertEqual(w.label.get_opacity(), 1.0)
        self.assertEqual((w.caption.breath_start, w.caption.breath_timer, w.caption.tick), (None, 0, 0))
        self.at(style.BREATH_MS / 2)
        self.assertEqual(w.label.get_opacity(), 1.0)

    def test_words_in_flight_land_when_animations_are_turned_off(self):
        self.effect('rain')
        w = self.window
        text, word = self.passage(3)
        w.show_text(text)
        self.at(1000)
        w.show_text(f'{text} {word}')
        self.at(style.PIXEL_MS / 3)
        self.assertTrue(w.caption.drawing)
        self.assertGreater(w.caption.offset, 0)
        self.gtk.set_property('gtk-enable-animations', False)
        self.assertEqual((w.caption.appeared, w.caption.drawing, w.caption.glide), ({}, [], 0.0))
        self.assertIsNone(w.label.get_attributes())
        self.assertEqual(w.caption.offset, 0)
        self.assertEqual(w.label.get_text().splitlines()[-1], word)

    def test_the_more_options_sample_lets_go_of_the_settings_once_closed(self):
        self.app.open_options()
        options = self.app.options
        self.addCleanup(lambda: self.app.options and self.app.options.close())
        self.settle()
        caption = options.preview.caption
        self.assertTrue(caption.get_mapped())
        settings, handler = caption.watching
        self.assertTrue(GObject.signal_handler_is_connected(settings, handler))
        options.close()
        self.settle()
        self.assertFalse(GObject.signal_handler_is_connected(settings, handler))
        self.assertIsNone(caption.watching)

    # Less work each frame

    def test_the_label_is_not_given_the_same_attributes_every_frame(self):
        self.effect('dissolve', 'slow')
        w = self.window
        w.show_text('so we took the long road')
        self.at(1000)
        w.show_text('so we took the long road back')
        self.at(20)
        with patch.object(w.label, 'set_attributes', wraps=w.label.set_attributes) as set_attributes:
            self.at(60)
            self.at(120)
            self.assertEqual(set_attributes.call_count, 0)
            self.at(style.PIXEL_MS * 1.75 + 50)
            self.assertEqual(set_attributes.call_count, 1)  # landed: the label shows the word again
        self.assertIsNone(w.label.get_attributes())

    def test_pieces_are_drawn_in_an_image_just_big_enough_for_them(self):
        label = self.window.label
        layout = label.create_pango_layout('headlights')
        width, height = layout.get_pixel_size()
        pieces = caption_text.Pieces(layout, label.get_color(), (0.3, 0, width, height), 2, 40)
        x, y, w, h = pieces.bounds('dissolve', 0.3, 0, 0)  # dissolve: within the word
        self.assertGreaterEqual(x, pieces.left)
        self.assertGreaterEqual(y, pieces.top)
        self.assertLessEqual(x + w, pieces.left + pieces.width + pieces.size)
        self.assertLessEqual(y + h, pieces.top + pieces.height + pieces.size)
        self.assertEqual(pieces.bounds('mosaic', 0.3, 0, 0), (pieces.left, pieces.top, pieces.width, pieces.height))
        for effect in ('assemble', 'dissolve', 'rain'):
            for p in (0.02, 0.1, 0.25, 0.5, 0.8):
                box = pieces.bounds(effect, p, 100, 100)
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 800, 600)
                surface.set_device_scale(2, 2)
                pieces.draw(cairo.Context(surface), effect, p, 100, 100)
                surface.flush()
                ink = np.frombuffer(surface.get_data(), np.uint8).reshape(600, -1, 4)[..., 3]
                rows, cols = np.nonzero(ink)
                if box is None:
                    self.assertEqual(len(rows), 0, (effect, p))
                    continue
                bx, by, bw, bh = (v * 2 for v in box)  # screen pixels
                self.assertTrue(len(rows), (effect, p))
                self.assertGreaterEqual(cols.min(), bx - 0.01, (effect, p))
                self.assertGreaterEqual(rows.min(), by - 0.01, (effect, p))
                self.assertLessEqual(cols.max() + 1, bx + bw + 0.01, (effect, p))
                self.assertLessEqual(rows.max() + 1, by + bh + 0.01, (effect, p))

    def test_landed_pieces_on_screen_are_exactly_the_label(self):
        w = self.window
        for effect in ('assemble', 'dissolve', 'rain'):
            self.effect(effect)
            w.show_text('so we took the long road')
            self.at(1000)
            w.show_text('so we took the long road back')
            self.at(style.PIXEL_MS * 0.8)  # every piece landed, the label still hiding the word
            self.assertTrue(w.caption.drawing, effect)
            landed = self.render()
            self.at(style.PIXEL_MS + 50)
            self.assertEqual(w.caption.drawing, [], effect)
            self.assertEqual(landed, self.render(), effect)
            w.show_text('')


if __name__ == '__main__':
    unittest.main(verbosity=2)
