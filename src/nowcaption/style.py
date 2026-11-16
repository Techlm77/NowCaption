"""The captions' shared look and motion: text sizes, rows and padding, colours, animation
timings, how new words arrive, and the docked positions. Plain data with no GTK, since
settings.py reads it too; the widgets that use it are in ui/."""

SIZES = [18, 22, 26, 30, 36, 42, 50, 60]
ROWS = 2              # default caption rows, like broadcast subtitles; drag an edge for more
PAD_X, PAD_Y = 22, 12
BOTTOM_GAP = 80       # default distance from the bottom of the screen
EDGE = 10             # width of the band along the box's edges that resizes instead of moving
MIN_WIDTH = 240
CURSORS = {'n': 'n-resize', 's': 's-resize', 'e': 'e-resize', 'w': 'w-resize',
           'ne': 'ne-resize', 'nw': 'nw-resize', 'se': 'se-resize', 'sw': 'sw-resize'}
# Rows of − / + buttons in the menu. They are real buttons rather than menu items, because
# GTK closes a menu when one of its items is chosen, and these are pressed several times.
STEPPERS = {
    'text-size': ('Text size', ('Smaller text', 'app.smaller', 'list-remove-symbolic'),
                  ('Larger text', 'app.larger', 'list-add-symbolic')),
    'background': ('Background', ('More see-through', 'app.lighter', 'list-remove-symbolic'),
                   ('More solid', 'app.darker', 'list-add-symbolic')),
}
MENU_INDENT = 34  # button rows start where the menu's item labels do, after the tick column
# Caption text colours, as TV captions offer them. Their swatches in the menu are buttons too,
# so colours can be compared on the captions before the menu closes.
COLOURS = {'white': ('White', '#ffffff'), 'yellow': ('Yellow', '#ffff00'),
           'lime': ('Lime', '#00ff00'), 'cyan': ('Cyan', '#00ffff'),
           'orange': ('Orange', '#ff9900'), 'pink': ('Pink', '#ff7ad9'),
           'lavender': ('Lavender', '#c8a2ff'), 'brown': ('Brown', '#d2a26e'),
           'red': ('Red', '#ff5050')}
# Animations, in ms. All are instant when the desktop turns animations off. The caption box
# fades in when NowCaption starts and out when it quits; the rows glide up a row instead of
# jumping; new words fade in; text colour and background blend into a new choice; status
# messages fade, and a message that waits for speech ("Listening…") breathes until it comes.
FADE_IN_MS, FADE_OUT_MS = 200, 150
GLIDE_MS, WORD_MS, STATUS_MS, CHANGE_MS, BREATH_MS = 200, 120, 150, 150, 2000
UNDOCK_MS, DOCK_MS = 220, 260  # docked captions slide into their edge, and out of the new one
# How new words arrive, chosen under More options. The pixel effects cut the word into small
# pieces cut from the text itself, so the pieces put together are exactly the word.
WORD_EFFECTS = {'none': ('None', 'Each word appears at once'),
                'fade': ('Fade', 'Each word fades in'),
                'assemble': ('Pixel assemble', 'Left to right, the pixels fade in as they fly into place'),
                'dissolve': ('Pixel dissolve', 'The pixels switch on in random order'),
                'mosaic': ('Mosaic sharpen', 'The word starts as big blocks and sharpens'),
                'rain': ('Pixel rain', 'The pixels drop into place from above, left to right')}
WORD_SPEEDS = {'fast': ('Fast', 0.6), 'normal': ('Normal', 1.0), 'slow': ('Slow', 1.75)}
PIXEL_MS = 200  # a pixel effect at normal speed (the fade takes WORD_MS)
PIXEL_WORDS = 8  # more words than this arriving at once (a whole passage) fade in instead
MOSAIC = (0.07, 0.11, 0.16, 0.24, 0.36, 0.55, 1.0)  # the mosaic's steps, as a share of full detail
BREATH_STEP_MS = 40  # breathing is slow and may last long: 25 steps a second, not every frame
# Where the captions go: floating where they are dragged, or docked along an edge of the screen.
# Docked, they reserve that strip of the screen (the layer-shell exclusive zone), so maximised and
# snapped windows end where the captions begin; that needs layer shell (Plasma, Hyprland, Sway).
POSITIONS = {'floating': 'Floating', 'top': 'Docked at the top', 'bottom': 'Docked at the bottom',
             'left': 'Docked on the left', 'right': 'Docked on the right'}
INNER = {'top': 's', 'bottom': 'n', 'left': 'e', 'right': 'w'}  # the edge a docked strip resizes by
ALONG = {'top': ('left', 'right'), 'bottom': ('left', 'right'), 'left': ('top', 'bottom'),
         'right': ('top', 'bottom')}  # the edges a docked strip also reaches


def css_font(family):
    """A font family name as a CSS string (it may hold spaces, quotes or backslashes)."""
    family = ' '.join(family.split())
    return '"' + family.replace('\\', '\\\\').replace('"', '\\"') + '"'
