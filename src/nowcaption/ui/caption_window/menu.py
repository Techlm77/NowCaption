"""The caption box's menu: the app's menu model in a popover, with rows of buttons that keep it
open (text size, background, text colour and position), pointed toward the room on the screen
and opened by a right-click or a long press. It reads the window's place and size, and changes
nothing but the menu."""
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from ...style import COLOURS, MENU_INDENT, POSITIONS, STEPPERS  # noqa: E402
from . import layer_shell  # noqa: E402


class CaptionMenu:
    """The menu that belongs to the caption box. The app builds its model (build_menu); this
    fills the model's custom items, shows the current values in them, and points and opens it."""

    def __init__(self, window):
        self.window = window
        self.popover = Gtk.PopoverMenu()
        self.update_menu(window.layer and window.monitor_count() > 1)
        self.popover.set_parent(window.box)
        self.popover.set_has_arrow(False)
        self.popover.set_position(Gtk.PositionType.BOTTOM)
        self.popover.connect('closed', lambda *a: window.layer and layer_shell.LayerShell.set_keyboard_mode(
            window, layer_shell.LayerShell.KeyboardMode.NONE))

    def update_menu(self, several_screens):
        """Set the menu, with the text size, background and text colour rows as buttons that
        keep it open. The size and background rows show their value between their buttons; the
        colour row shows the chosen colour's name, with a line of colour dots under it."""
        self.popover.set_menu_model(self.window.app.build_menu(several_screens, self.window.layer))
        self.values = {}
        for name, (title, *buttons) in STEPPERS.items():
            row = Gtk.Box(spacing=2, margin_start=MENU_INDENT, margin_end=6)
            row.append(Gtk.Label(label=title, xalign=0, hexpand=True))
            for tooltip, action, icon in buttons:
                button = Gtk.Button(icon_name=icon, action_name=action, tooltip_text=tooltip)
                button.add_css_class('flat')
                button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
                row.append(button)
                if not self.values.get(name):
                    self.values[name] = Gtk.Label(width_chars=5, css_classes=['dim-label', 'numeric'])
                    row.append(self.values[name])
            self.popover.add_child(row, name)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=MENU_INDENT,
                      margin_end=6, margin_top=6, margin_bottom=4)
        row = Gtk.Box()
        row.append(Gtk.Label(label='Text colour', xalign=0, hexpand=True))
        self.values['text-colour'] = Gtk.Label(css_classes=['dim-label'], margin_end=6)
        row.append(self.values['text-colour'])
        box.append(row)
        dots = Gtk.Box(spacing=1)
        for name, (label, _) in COLOURS.items():  # ringed, like a radio button, when chosen
            swatch = Gtk.Box(css_classes=['colour-swatch', f'colour-{name}'],
                             halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            button = Gtk.ToggleButton(child=swatch, action_name='app.colour',
                                      action_target=GLib.Variant('s', name), tooltip_text=label,
                                      css_classes=['flat', 'colour-dot'])
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            dots.append(button)
        box.append(dots)
        self.popover.add_child(box, 'text-colour')
        if self.window.layer:  # elsewhere the window manager places the window
            self.popover.add_child(self.position_row(), 'position')
        self.show_values()

    def refresh_menu(self):
        """Build the menu again, for an item whose label changed."""
        self.update_menu(self.window.layer and self.window.monitor_count() > 1)

    def position_row(self):
        """The Position row: the chosen position's name, and a little screen for each position
        showing where the captions go; like the colour dots, they keep the menu open."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=MENU_INDENT,
                      margin_end=6, margin_top=6, margin_bottom=4)
        row = Gtk.Box()
        row.append(Gtk.Label(label='Position', xalign=0, hexpand=True))
        self.values['position'] = Gtk.Label(css_classes=['dim-label'], margin_end=6)
        row.append(self.values['position'])
        box.append(row)
        icons = Gtk.Box(spacing=2)
        for name, label in POSITIONS.items():
            screen = Gtk.Box(css_classes=['screen'], halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            strip = Gtk.Box(css_classes=['strip', 'across' if name in ('top', 'bottom') else
                                         'down' if name in ('left', 'right') else 'floating'],
                            hexpand=True, vexpand=name in ('left', 'right'),
                            halign={'left': Gtk.Align.START, 'right': Gtk.Align.END,
                                    'floating': Gtk.Align.CENTER}.get(name, Gtk.Align.FILL),
                            valign={'top': Gtk.Align.START, 'left': Gtk.Align.FILL,
                                    'right': Gtk.Align.FILL}.get(name, Gtk.Align.END))
            screen.append(strip)
            button = Gtk.ToggleButton(child=screen, action_name='app.position', tooltip_text=label,
                                      action_target=GLib.Variant('s', name), css_classes=['flat', 'position'])
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            icons.append(button)
        box.append(icons)
        return box

    def show_values(self):
        values, settings = self.values, self.window.settings
        values['text-size'].set_label(f"{settings.get('size')} px")
        values['background'].set_label(f"{round(settings.get('opacity') * 100)}%")
        values['text-colour'].set_label(COLOURS[settings.get('colour')][0])
        if 'position' in values:
            values['position'].set_label(POSITIONS[settings.get('position')])

    def point_menu(self, x, y):
        """Point the menu at x, y in the window, opening toward the room on the screen: away
        from a docked strip's edge, beside a docked column, and upwards from captions low on
        the screen. KWin squeezes a layer-shell menu that does not fit, or lets it run off the
        screen, rather than flipping it."""
        win = self.window
        rect = Gdk.Rectangle()  # the menu belongs to the box, so use box coordinates
        w, h = win.box_width, win.box_height()
        side = Gtk.PositionType.BOTTOM
        if win.edge == 'bottom':
            side, (rect.x, rect.y, rect.width, rect.height) = Gtk.PositionType.TOP, (int(x), 0, 1, 1)
        elif win.edge == 'top':
            rect.x, rect.y, rect.width, rect.height = int(x), h - 1, 1, 1
        elif win.edge in ('left', 'right'):
            side = Gtk.PositionType.RIGHT if win.edge == 'left' else Gtk.PositionType.LEFT
            rect.x, rect.y, rect.width, rect.height = w - 1 if win.edge == 'left' else 0, 0, 1, h
        else:
            bx, by = (win.x, win.y) if win.layer else (0, 0)
            geo = win.monitor.get_geometry() if win.layer and win.monitor else None
            if geo and y > geo.height / 2:  # the overlay covers the screen, so y is on it
                side = Gtk.PositionType.TOP
            rect.x, rect.y, rect.width, rect.height = int(x - bx), int(y - by), 1, 1
        self.popover.set_position(side)
        self.popover.set_pointing_to(rect)

    def on_menu(self, gesture, n, x, y):
        win = self.window
        if win.layer:  # let the menu take keyboard input while it is open
            shell = layer_shell.LayerShell
            shell.set_keyboard_mode(win, shell.KeyboardMode.ON_DEMAND)
        self.point_menu(x, y)
        if win.layer:  # let the compositor apply the keyboard mode first, or KWin closes the menu at once
            GLib.timeout_add(60, lambda: self.popover.popup() and False)
        else:
            self.popover.popup()
