#!/usr/bin/env python3
"""New-word effects in 1.1.16: pieces fly on when a word in flight is cut again (the rows
finish gliding, or the text colour is changing), None applied while words arrive, the desktop
turning animations off while "Listening…" breathes, and smaller images for the pieces.

GTK window tests on the application test_window.py registers, as its AnimationTests do.
"""
import unittest
from unittest.mock import patch

import test_window
from test_window import nc, shared_app


@unittest.skipIf(nc is None, getattr(test_window, 'SKIP', ''))
class EffectFixTests(unittest.TestCase):
    """With the desktop's animations on, on a window that is shown, the caption rows shown as
    of a chosen time, as in test_window.AnimationTests."""

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        self.gtk = nc.Gtk.Settings.get_default()
        self.gtk.set_property('gtk-enable-animations', True)
        self.addCleanup(self.gtk.set_property, 'gtk-enable-animations', False)
        self.app.settings.set('size', 30)
        self.window = nc.CaptionWindow(self.app)
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
        renderer = nc.Gsk.CairoRenderer()
        renderer.realize_for_display(nc.Gdk.Display.get_default())
        try:
            texture = renderer.render_texture(snap.to_node(), nc.Graphene.Rect().init(0, 0, width, height))
        finally:
            renderer.unrealize()
        return nc.Gdk.TextureDownloader.new(texture).download_bytes()[0].get_data()

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
        common, mine, theirs = nc.np.intersect1d(after.spot, before.spot, return_indices=True)
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
        blending = nc.Gdk.RGBA()
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
        self.at(nc.PIXEL_MS / 2)
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
        self.at(nc.BREATH_MS / 2)
        self.assertLess(w.label.get_opacity(), 0.6)
        self.assertTrue(w.caption.breath_timer)
        self.gtk.set_property('gtk-enable-animations', False)
        self.assertEqual(w.label.get_opacity(), 1.0)
        self.assertEqual((w.caption.breath_start, w.caption.breath_timer, w.caption.tick), (None, 0, 0))
        self.at(nc.BREATH_MS / 2)
        self.assertEqual(w.label.get_opacity(), 1.0)

    def test_words_in_flight_land_when_animations_are_turned_off(self):
        self.effect('rain')
        w = self.window
        text, word = self.passage(3)
        w.show_text(text)
        self.at(1000)
        w.show_text(f'{text} {word}')
        self.at(nc.PIXEL_MS / 3)
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
        self.assertTrue(nc.GObject.signal_handler_is_connected(settings, handler))
        options.close()
        self.settle()
        self.assertFalse(nc.GObject.signal_handler_is_connected(settings, handler))
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
            self.at(nc.PIXEL_MS * 1.75 + 50)
            self.assertEqual(set_attributes.call_count, 1)  # landed: the label shows the word again
        self.assertIsNone(w.label.get_attributes())

    def test_pieces_are_drawn_in_an_image_just_big_enough_for_them(self):
        label = self.window.label
        layout = label.create_pango_layout('headlights')
        width, height = layout.get_pixel_size()
        pieces = nc.Pieces(layout, label.get_color(), (0.3, 0, width, height), 2, 40)
        x, y, w, h = pieces.bounds('dissolve', 0.3, 0, 0)  # dissolve: within the word
        self.assertGreaterEqual(x, pieces.left)
        self.assertGreaterEqual(y, pieces.top)
        self.assertLessEqual(x + w, pieces.left + pieces.width + pieces.size)
        self.assertLessEqual(y + h, pieces.top + pieces.height + pieces.size)
        self.assertEqual(pieces.bounds('mosaic', 0.3, 0, 0), (pieces.left, pieces.top, pieces.width, pieces.height))
        for effect in ('assemble', 'dissolve', 'rain'):
            for p in (0.02, 0.1, 0.25, 0.5, 0.8):
                box = pieces.bounds(effect, p, 100, 100)
                surface = nc.cairo.ImageSurface(nc.cairo.FORMAT_ARGB32, 800, 600)
                surface.set_device_scale(2, 2)
                pieces.draw(nc.cairo.Context(surface), effect, p, 100, 100)
                surface.flush()
                ink = nc.np.frombuffer(surface.get_data(), nc.np.uint8).reshape(600, -1, 4)[..., 3]
                rows, cols = nc.np.nonzero(ink)
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
            self.at(nc.PIXEL_MS * 0.8)  # every piece landed, the label still hiding the word
            self.assertTrue(w.caption.drawing, effect)
            landed = self.render()
            self.at(nc.PIXEL_MS + 50)
            self.assertEqual(w.caption.drawing, [], effect)
            self.assertEqual(landed, self.render(), effect)
            w.show_text('')


if __name__ == '__main__':
    unittest.main()
