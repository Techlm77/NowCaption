"""NowCaption: on-device live captions for anything your computer plays, or your microphone."""
import os
import sys
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

try:
    gi.require_version('Gtk4LayerShell', '1.0')
    from gi.repository import Gtk4LayerShell as LayerShell
except (ValueError, ImportError):
    LayerShell = None

from . import __version__, audio, engine  # noqa: E402

APP_ID = 'io.github.Techlm77.NowCaption'
SIZES = [18, 22, 26, 30, 36, 42, 50, 60]
DEFAULTS = {'source': 'desktop', 'size': 30, 'position': 'bottom', 'margin': 60, 'opacity': 0.78}


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


class CaptionWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='NowCaption')
        self.app, self.settings = app, app.settings
        self.set_decorated(False)
        self.layer = bool(LayerShell and LayerShell.is_supported())
        display = Gdk.Display.get_default()
        monitors = display.get_monitors()
        width = 1100
        if monitors.get_n_items():
            width = min(1100, int(monitors.get_item(0).get_geometry().width * 0.7))
        self.set_default_size(width, -1)
        self.set_size_request(width, -1)
        if self.layer:  # a real overlay on KDE Plasma, Hyprland, Sway and other wlroots desktops
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_namespace(self, 'nowcaption')
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
            self.place()

        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.add_css_class('nowcaption')
        self.restyle()

        self.label = Gtk.Label(label='', wrap=True, xalign=0)
        self.width = width
        self.label.set_accessible_role(Gtk.AccessibleRole.STATUS)
        self.set_child(self.label)
        self.show_text([], 'Loading speech model…')

        drag = Gtk.GestureDrag()
        drag.connect('drag-begin', self.on_drag_begin)
        drag.connect('drag-update', self.on_drag_update)
        drag.connect('drag-end', lambda *a: self.settings.set('margin', self.settings.get('margin')))
        self.add_controller(drag)
        click = Gtk.GestureClick(button=3)
        click.connect('pressed', self.on_menu)
        self.add_controller(click)
        self.popover = Gtk.PopoverMenu.new_from_model(app.build_menu())
        self.popover.set_parent(self.label)
        self.popover.set_has_arrow(False)

    def place(self):
        top = self.settings.get('position') == 'top'
        edge = LayerShell.Edge.TOP if top else LayerShell.Edge.BOTTOM
        other = LayerShell.Edge.BOTTOM if top else LayerShell.Edge.TOP
        LayerShell.set_anchor(self, edge, True)
        LayerShell.set_anchor(self, other, False)
        LayerShell.set_margin(self, edge, self.settings.get('margin'))

    def restyle(self):
        px, alpha = self.settings.get('size'), self.settings.get('opacity')
        self.css.load_from_string(f"""
            window.nowcaption {{ background: rgba(0,0,0,{alpha}); border-radius: 12px; }}
            window.nowcaption label {{ color: #ffffff; font-size: {px}px; font-weight: 500;
                                       padding: 10px 20px; line-height: 1.25; }}""")

    def show_text(self, lines, partial):
        """Newest line always; the line before it only if both fit in about three rows."""
        limit = int(3 * (self.width - 40) / (0.52 * self.settings.get('size')))
        parts = list(lines) + ([partial] if partial else [])
        text = parts[-1] if parts else ''
        if len(text) > limit:  # one long line: keep its end, starting on a whole word
            text = text[-limit:]
            text = text[text.find(' ') + 1:]
        elif len(parts) > 1 and len(parts[-2]) + len(text) <= limit:
            text = parts[-2] + '\n' + text
        self.label.set_text(text or ' ')
        return False

    def on_drag_begin(self, gesture, x, y):
        self.drag_margin = self.settings.get('margin')
        if not self.layer:
            surface = self.get_surface()
            if isinstance(surface, Gdk.Toplevel):
                surface.begin_move(gesture.get_device(), gesture.get_current_button(), x, y,
                                   gesture.get_current_event_time())

    def on_drag_update(self, gesture, dx, dy):
        if self.layer:  # layer surfaces are positioned by margin from the anchored edge
            sign = 1 if self.settings.get('position') == 'top' else -1
            margin = max(0, int(self.drag_margin + sign * dy))
            self.settings.kf.set_integer('main', 'margin', margin)
            LayerShell.set_margin(self, LayerShell.Edge.TOP if sign > 0 else LayerShell.Edge.BOTTOM, margin)

    def on_menu(self, gesture, n, x, y):
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
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

    def build_menu(self):
        menu = Gio.Menu()
        src = Gio.Menu()
        src.append('Desktop audio', 'app.source::desktop')
        src.append('Microphone', 'app.source::mic')
        menu.append_section('Listen to', src)
        look = Gio.Menu()
        look.append('Larger text', 'app.larger')
        look.append('Smaller text', 'app.smaller')
        look.append('More see-through', 'app.lighter')
        look.append('More solid', 'app.darker')
        if LayerShell and LayerShell.is_supported():
            look.append('Show at top of screen', 'app.position::top')
            look.append('Show at bottom of screen', 'app.position::bottom')
        menu.append_section('Appearance', look)
        text = Gio.Menu()
        text.append('Copy transcript', 'app.copy')
        text.append('Save transcript…', 'app.save')
        text.append('Clear transcript', 'app.clear')
        menu.append_section(None, text)
        end = Gio.Menu()
        end.append('About NowCaption', 'app.about')
        end.append('Quit', 'app.quit')
        menu.append_section(None, end)
        return menu

    def add_actions(self):
        def act(name, fn, accels=()):
            a = Gio.SimpleAction.new(name, None)
            a.connect('activate', lambda *_: fn())
            self.add_action(a)
            if accels:
                self.set_accels_for_action('app.' + name, list(accels))
        for name, key, values in [('source', 'source', ('desktop', 'mic')), ('position', 'position', ('top', 'bottom'))]:
            a = Gio.SimpleAction.new_stateful(name, GLib.VariantType.new('s'), GLib.Variant('s', self.settings.get(key)))
            a.connect('activate', self.on_choice, key)
            self.add_action(a)
        act('larger', lambda: self.resize(+1), ['<Control>plus', '<Control>equal'])
        act('smaller', lambda: self.resize(-1), ['<Control>minus'])
        act('lighter', lambda: self.opacity(-0.15))
        act('darker', lambda: self.opacity(+0.15))
        act('copy', self.copy_transcript, ['<Control>c'])
        act('save', self.save_transcript, ['<Control>s'])
        act('clear', self.clear_transcript)
        act('about', self.about)
        act('quit', self.quit, ['<Control>q'])

    def on_choice(self, action, value, key):
        action.set_state(value)
        self.settings.set(key, value.get_string())
        if key == 'source':
            self.start_capture()
        elif self.window and self.window.layer:
            self.window.place()

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
        self.window.present()
        GLib.idle_add(self.load_models)

    def load_models(self):
        try:
            rec, punct = engine.load_recognizer(), engine.load_punctuation()
        except Exception as e:  # missing or broken model files
            self.window.show_text([], f'Could not load the speech model: {e}')
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
            self.window.show_text([], 'Listening to the microphone…' if mic else 'Listening to desktop audio…')

    def on_audio(self, pcm):
        self.captioner.feed(pcm)

    def on_captions(self, lines, partial):
        GLib.idle_add(self.window.show_text, lines, partial)

    def on_audio_error(self, message):
        GLib.idle_add(self.window.show_text, [], f'Audio capture stopped: {message}')

    def copy_transcript(self):
        if self.captioner:
            self.window.get_clipboard().set(self.captioner.transcript())

    def clear_transcript(self):
        if self.captioner:
            with self.captioner.lock:
                self.captioner.lines.clear()
            self.window.show_text([], '')

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
        dialog.save(self.window, None, done)

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
        about.present(self.window)


def main():
    return NowCaption().run(sys.argv)
