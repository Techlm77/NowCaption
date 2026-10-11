"""NowCaption: on-device live captions for anything your computer plays, or your microphone."""
import os
import sys
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
import cairo  # noqa: E402
gi.require_version('Pango', '1.0')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

try:
    gi.require_version('Gtk4LayerShell', '1.0')
    from gi.repository import Gtk4LayerShell as LayerShell
except (ValueError, ImportError):
    LayerShell = None

from . import __version__, audio, engine  # noqa: E402

APP_ID = 'io.github.Techlm77.NowCaption'
SIZES = [18, 22, 26, 30, 36, 42, 50, 60]
ROWS = 2              # caption rows shown, like broadcast subtitles
PAD_X, PAD_Y = 22, 12
BOTTOM_GAP = 80       # default distance from the bottom of the screen
DEFAULTS = {'source': 'desktop', 'size': 30, 'opacity': 0.78, 'x': -1, 'y': -1, 'monitor': ''}


class Settings:
    """Small key file in the user's config directory."""

    def __init__(self):
        self.path = Path(GLib.get_user_config_dir()) / 'nowcaption' / 'settings.ini'
        self.kf = GLib.KeyFile()
        try:
            self.kf.load_from_file(str(self.path), GLib.KeyFileFlags.NONE)
        except GLib.Error:
            pass

    def get(self, key):
        default = DEFAULTS[key]
        try:
            if isinstance(default, int):
                return self.kf.get_integer('main', key)
            if isinstance(default, float):
                return self.kf.get_double('main', key)
            return self.kf.get_string('main', key)
        except GLib.Error:
            return default

    def set(self, key, value):
        {int: self.kf.set_integer, float: self.kf.set_double}.get(type(DEFAULTS[key]), self.kf.set_string)('main', key, value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.kf.save_to_file(str(self.path))


def last_rows(layout, text, rows):
    """The last `rows` wrapped lines of text, as Pango lays them out."""
    layout.set_text(text, -1)
    data = text.encode()
    lines = [data[l.start_index:l.start_index + l.length].decode(errors='ignore').strip()
             for l in layout.get_lines_readonly()]
    return '\n'.join(lines[-rows:])


class CaptionWindow(Gtk.ApplicationWindow):
    """Fixed-size caption box: always ROWS rows of text, newest words at the end of the last row.

    On compositors with layer-shell (KDE Plasma, Hyprland, Sway, ...) the window is a transparent
    overlay covering one monitor. Only the caption box takes pointer input (everything else clicks
    through), and dragging moves the box inside the overlay, so nothing moves under the pointer.
    Elsewhere it is an undecorated normal window that the window manager moves.
    """

    def __init__(self, app):
        super().__init__(application=app, title='NowCaption')
        self.app, self.settings = app, app.settings
        self.set_decorated(False)
        self.set_resizable(False)
        self.layer = bool(LayerShell and LayerShell.is_supported())
        self.dragging = None

        self.monitor = self.pick_monitor(self.settings.get('monitor'))
        geo = self.monitor.get_geometry() if self.monitor else None
        self.box_width = min(1000, int(geo.width * 0.7)) if geo else 1000

        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.add_css_class('nowcaption')
        self.label = Gtk.Label(xalign=0, yalign=0, wrap=False, single_line_mode=False)
        self.label.add_css_class('caption-text')
        self.label.set_accessible_role(Gtk.AccessibleRole.STATUS)
        self.box = Gtk.Box()
        self.box.add_css_class('caption-box')
        self.box.append(self.label)
        self.fixed = Gtk.Fixed()  # lets the box move inside the window while it is dragged
        self.fixed.put(self.box, 0, 0)
        self.set_child(self.fixed)

        if self.layer:
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_namespace(self, 'nowcaption')
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
            LayerShell.set_exclusive_zone(self, -1)
            if self.monitor:
                LayerShell.set_monitor(self, self.monitor)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            self.connect('realize', lambda *a: self.get_surface().connect('layout', lambda *a: self.update_input()))
        self.restyle()

        drag = Gtk.GestureDrag()  # primary button
        drag.connect('drag-begin', self.on_drag_begin)
        drag.connect('drag-update', self.on_drag_update)
        drag.connect('drag-end', self.on_drag_end)
        self.add_controller(drag)
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect('pressed', self.on_menu)
        self.add_controller(click)
        self.set_cursor(Gdk.Cursor.new_from_name('grab', None))

        self.popover = Gtk.PopoverMenu.new_from_model(app.build_menu(self.layer and self.monitor_count() > 1))
        self.popover.set_parent(self.box)
        self.popover.set_has_arrow(False)
        self.popover.set_position(Gtk.PositionType.BOTTOM)
        self.popover.connect('closed', lambda *a: self.layer and LayerShell.set_keyboard_mode(
            self, LayerShell.KeyboardMode.NONE))
        self.show_text('Loading speech model…')

    # --- geometry -------------------------------------------------------------------------

    @staticmethod
    def monitor_count():
        return Gdk.Display.get_default().get_monitors().get_n_items()

    @staticmethod
    def pick_monitor(connector):
        monitors = Gdk.Display.get_default().get_monitors()
        items = [monitors.get_item(i) for i in range(monitors.get_n_items())]
        for m in items:
            if connector and m.get_connector() == connector:
                return m
        return items[0] if items else None

    def box_height(self):
        return ROWS * self.row_height + 2 * PAD_Y

    def clamp(self, x, y):
        """x, y on the monitor (None: centred near the bottom), keeping the whole box on screen."""
        geo = self.monitor.get_geometry()
        w, h = self.box_width, self.box_height()
        x = (geo.width - w) // 2 if x is None else x
        y = geo.height - h - BOTTOM_GAP if y is None else y
        return max(0, min(int(x), geo.width - w)), max(0, min(int(y), geo.height - h))

    def place(self, x=None, y=None):
        """Put the caption box at x, y on its monitor."""
        if not self.layer or not self.monitor:
            return
        self.x, self.y = self.clamp(x, y)
        self.fixed.move(self.box, self.x, self.y)
        self.update_input()

    def update_input(self):
        """Only the caption box takes pointer input; the rest of the overlay clicks through."""
        surface = self.get_surface()
        if self.layer and surface:
            surface.set_input_region(cairo.Region(cairo.RectangleInt(
                self.x, self.y, self.box_width, self.box_height())))

    def next_monitor(self):
        monitors = Gdk.Display.get_default().get_monitors()
        items = [monitors.get_item(i) for i in range(monitors.get_n_items())]
        if len(items) < 2 or not self.layer:
            return
        self.monitor = items[(items.index(self.monitor) + 1) % len(items)] if self.monitor in items else items[0]
        self.settings.set('monitor', self.monitor.get_connector() or '')
        LayerShell.set_monitor(self, self.monitor)
        self.reset_position()

    def reset_position(self):
        self.settings.set('x', -1)
        self.settings.set('y', -1)
        self.place()

    def restyle(self):
        px, alpha = self.settings.get('size'), self.settings.get('opacity')
        self.css.load_from_string(f"""
            window.nowcaption {{ background: transparent; }}
            window.nowcaption .caption-box {{ background: rgba(0, 0, 0, {alpha}); border-radius: 14px; }}
            window.nowcaption .caption-text {{ color: #ffffff; font-size: {px}px; font-weight: 500;
                                              padding: {PAD_Y}px {PAD_X}px; }}""")
        # measure one row in the caption font, then fix the box at exactly ROWS rows
        probe = self.label.create_pango_layout('Ag')
        self.row_height = probe.get_pixel_size()[1]
        self.layout = self.label.create_pango_layout('')
        self.layout.set_width((self.box_width - 2 * PAD_X) * Pango.SCALE)
        self.layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        self.label.set_size_request(self.box_width, self.box_height())
        x, y = self.settings.get('x'), self.settings.get('y')
        self.place(x if x >= 0 else None, y if y >= 0 else None)
        self.render()

    # --- text -----------------------------------------------------------------------------

    def show_text(self, text):
        self.text = text
        self.render()
        return False

    def render(self):
        # the text flows on; only the last ROWS rows show, so the newest words are always visible
        text = getattr(self, 'text', '')
        self.label.set_text(last_rows(self.layout, text, ROWS) if text else ' ')

    def save_png(self, path):
        """Render the window exactly as drawn, for store screenshots."""
        w, h = self.box.get_width(), self.box.get_height()
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(self.box).snapshot(snap, w, h)
        node = snap.to_node()
        if node:
            self.get_renderer().render_texture(node, None).save_to_png(path)
        return False

    # --- moving -----------------------------------------------------------------------------

    def on_drag_begin(self, gesture, x, y):
        self.set_cursor(Gdk.Cursor.new_from_name('grabbing', None))
        if self.layer:  # x, y are overlay (monitor) coordinates; remember where the box was grabbed
            self.dragging = (x - self.x, y - self.y)
            return
        surface = self.get_surface()  # the window manager moves normal windows
        if isinstance(surface, Gdk.Toplevel):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            surface.begin_move(gesture.get_device(), gesture.get_current_button(), x, y,
                               gesture.get_current_event_time())

    def on_drag_update(self, gesture, dx, dy):
        if not self.dragging:
            return
        ok, px, py = gesture.get_point(None)
        if ok:
            self.place(px - self.dragging[0], py - self.dragging[1])

    def on_drag_end(self, gesture, dx, dy):
        self.set_cursor(Gdk.Cursor.new_from_name('grab', None))
        if not self.dragging:
            return
        self.dragging = None
        self.settings.set('x', self.x)
        self.settings.set('y', self.y)
        if os.environ.get('NOWCAPTION_DEBUG_DRAG'):
            print('drag end', self.x, self.y, flush=True)

    def on_menu(self, gesture, n, x, y):
        if self.layer:  # let the menu take keyboard input while it is open
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.ON_DEMAND)
        rect = Gdk.Rectangle()  # the menu belongs to the box, so use box coordinates
        bx, by = (self.x, self.y) if self.layer else (0, 0)
        rect.x, rect.y, rect.width, rect.height = int(x - bx), int(y - by), 1, 1
        self.popover.set_pointing_to(rect)
        self.popover.popup()


class NowCaption(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.settings = Settings()
        self.window = self.captioner = None
        self.capture = audio.Capture(self.on_audio, self.on_audio_error)
        self.connect('activate', self.on_activate)
        self.connect('shutdown', lambda *a: self.capture.stop())
        self.add_actions()

    def build_menu(self, several_screens=False):
        """Compact menu: sound source, one-row size/background controls, transcript, app."""
        def section(*items, label=None, hint=None):
            m = Gio.Menu()
            for text, action, icon in items:
                item = Gio.MenuItem.new(text, action)
                if icon:
                    item.set_attribute_value('verb-icon', GLib.Variant('s', icon))
                m.append_item(item)
            sec = Gio.MenuItem.new_section(label, m)
            if hint:
                sec.set_attribute_value('display-hint', GLib.Variant('s', hint))
            return sec

        menu = Gio.Menu()
        menu.append_item(section(('Desktop audio', 'app.source::desktop', None),
                                 ('Microphone', 'app.source::mic', None)))
        menu.append_item(section(('Smaller text', 'app.smaller', 'zoom-out-symbolic'),
                                 ('Larger text', 'app.larger', 'zoom-in-symbolic'),
                                 label='Text size', hint='inline-buttons'))
        menu.append_item(section(('More see-through', 'app.lighter', 'value-decrease-symbolic'),
                                 ('More solid', 'app.darker', 'value-increase-symbolic'),
                                 label='Background', hint='inline-buttons'))
        place = [('Reset position', 'app.reset-position', None)]
        if several_screens:
            place.append(('Move to next screen', 'app.next-screen', None))
        menu.append_item(section(*place))
        menu.append_item(section(('Copy transcript', 'app.copy', None),
                                 ('Save transcript…', 'app.save', None),
                                 ('Clear transcript', 'app.clear', None)))
        menu.append_item(section(('About NowCaption', 'app.about', None),
                                 ('Quit', 'app.quit', None)))
        return menu

    def add_actions(self):
        def act(name, fn, accels=()):
            a = Gio.SimpleAction.new(name, None)
            a.connect('activate', lambda *_: fn())
            self.add_action(a)
            if accels:
                self.accels['app.' + name] = list(accels)
        self.accels = {}  # registered once we know the window can take keyboard focus
        source = Gio.SimpleAction.new_stateful('source', GLib.VariantType.new('s'),
                                               GLib.Variant('s', self.settings.get('source')))
        source.connect('activate', self.on_source)
        self.add_action(source)
        act('larger', lambda: self.resize(+1), ['<Control>plus', '<Control>equal'])
        act('smaller', lambda: self.resize(-1), ['<Control>minus'])
        act('lighter', lambda: self.opacity(-0.15))
        act('darker', lambda: self.opacity(+0.15))
        act('reset-position', lambda: self.window.reset_position())
        act('next-screen', lambda: self.window.next_monitor())
        act('copy', self.copy_transcript)
        act('save', self.save_transcript, ['<Control>s'])
        act('clear', self.clear_transcript)
        act('about', self.about)
        act('quit', self.quit, ['<Control>q'])

    def on_source(self, action, value):
        action.set_state(value)
        self.settings.set('source', value.get_string())
        self.start_capture()

    def resize(self, step):
        size = self.settings.get('size')
        i = min(range(len(SIZES)), key=lambda k: abs(SIZES[k] - size))
        self.settings.set('size', SIZES[max(0, min(len(SIZES) - 1, i + step))])
        self.window.restyle()

    def opacity(self, step):
        self.settings.set('opacity', round(max(0.2, min(1.0, self.settings.get('opacity') + step)), 2))
        self.window.restyle()

    def on_activate(self, app):
        if self.window:
            return self.window.present()
        self.window = CaptionWindow(self)
        if not self.window.layer:  # an overlay never has keyboard focus, so shortcuts would never fire
            for action, keys in self.accels.items():
                self.set_accels_for_action(action, keys)
        self.window.present()
        GLib.idle_add(self.load_models)
        shot = os.environ.get('NOWCAPTION_SCREENSHOT')  # "file.png" or "file.png:seconds"
        if shot:
            path, _, after = shot.partition(':')
            GLib.timeout_add_seconds(int(after or 20), self.window.save_png, path)

    def load_models(self):
        try:
            rec, punct = engine.load_recognizer(), engine.load_punctuation()
        except Exception as e:  # missing or broken model files
            self.window.show_text(f'Could not load the speech model: {e}')
            return False
        self.captioner = engine.Captioner(rec, punct, self.on_captions)
        self.start_capture()
        return False

    def start_capture(self):
        if not self.captioner:
            return
        mic = self.settings.get('source') == 'mic'
        self.captioner.flush()
        # NOWCAPTION_SOURCE names any PulseAudio/PipeWire source, overriding the menu choice
        self.capture.start(os.environ.get('NOWCAPTION_SOURCE') or (audio.MICROPHONE if mic else audio.DESKTOP))
        if not self.captioner.lines:
            self.window.show_text('Listening to the microphone…' if mic else 'Listening to desktop audio…')

    def on_audio(self, pcm):
        self.captioner.feed(pcm)

    def on_captions(self, text):
        GLib.idle_add(self.window.show_text, text)

    def on_audio_error(self, message):
        GLib.idle_add(self.window.show_text, f'Audio capture stopped: {message}')

    def copy_transcript(self):
        if self.captioner:
            self.window.get_clipboard().set(self.captioner.transcript())

    def clear_transcript(self):
        if self.captioner:
            with self.captioner.lock:
                self.captioner.lines.clear()
            self.window.show_text('')

    def save_transcript(self):
        if not self.captioner:
            return
        dialog = Gtk.FileDialog(title='Save transcript', initial_name=GLib.DateTime.new_now_local().format(
            'Captions %Y-%m-%d %H.%M.txt'))
        def done(d, result):
            try:
                f = d.save_finish(result)
            except GLib.Error:
                return
            f.replace_contents((self.captioner.transcript() + '\n').encode(), None, False,
                               Gio.FileCreateFlags.REPLACE_DESTINATION, None)
        dialog.save(None, None, done)  # its own window: the caption overlay is too small to host it

    def about(self):
        about = Adw.AboutDialog(
            application_name='NowCaption', application_icon=APP_ID, version=__version__,
            developer_name='Techlm77', license_type=Gtk.License.MIT_X11,
            website='https://github.com/Techlm77/NowCaption',
            issue_url='https://github.com/Techlm77/NowCaption/issues',
            comments='Live captions for anything your computer plays, or your microphone. '
                     'Speech is recognised on this computer; audio never leaves it.')
        about.add_legal_section('Speech model', '© NVIDIA (stt_en_fastconformer_hybrid_large_streaming_multi)',
                                Gtk.License.CUSTOM, 'Creative Commons Attribution 4.0 International (CC-BY-4.0). '
                                'Exported to ONNX and quantized to int8 for NowCaption. '
                                'https://huggingface.co/nvidia/stt_en_fastconformer_hybrid_large_streaming_multi')
        about.add_legal_section('Punctuation model', 'Edge-Punct-Casing by frankyoujian', Gtk.License.APACHE_2_0, None)
        about.add_legal_section('sherpa-onnx', '© k2-fsa', Gtk.License.APACHE_2_0, None)
        about.present(None)  # its own window, not squeezed into the caption box


def main():
    return NowCaption().run(sys.argv)
