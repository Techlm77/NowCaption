"""The transcript window: this session's captions and the kept history, searchable, with the
session headings and times they are shown with."""
from __future__ import annotations

import bisect
import logging
import re
import time
from collections.abc import Callable

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango

from .. import engine

LOG = logging.getLogger('nowcaption')


def history_title(days: int) -> str:
    """How the transcript window says what it shows, for sessions kept this long."""
    return 'Last 24 hours' if days == 1 else f'Last {days} days' if days else 'Every kept session'


def session_heading(started: float) -> str:
    """A session's heading in the transcript, such as "Thursday 23 October 2026, 14:05"."""
    return time.strftime('%A %-d %B %Y, %H:%M', time.localtime(started))


def rgba(spec: str) -> Gdk.RGBA:
    """A colour given in CSS, such as 'rgba(255, 140, 0, 0.75)'."""
    colour = Gdk.RGBA()
    colour.parse(spec)
    return colour


TIME = re.compile(r'(\[\d\d:\d\d:\d\d\]) ')


class TranscriptWindow(Adw.Window):
    """Every kept session, oldest first, then this one as it grows: scroll, select and copy any
    of it, find words (Ctrl+F), save it, or delete the history. Earlier sessions come from the
    history folder; this one is fetched again from the recognition worker shortly after each
    caption, and only its changed lines are replaced, so a selection or the place being read
    stays put. The view follows new lines only while it is at the end."""

    REFRESH_MS = 500

    def __init__(self, app) -> None:
        """app: the NowCaption application: its settings, the kept sessions, the recognition worker,
        and copying, saving and deleting the transcript."""
        super().__init__(application=app, default_width=680, default_height=720)
        self.app, self.closed = app, False
        self.block, self.current = [], None  # this session's lines as shown (heading first), and its start
        self.matches, self.match, self.refresh_id = [], -1, None
        self.searched = ''  # the search text the matches are of
        self.handlers = []  # (object, handler id) for handlers that refer back to this window, until it closes

        self.buffer = Gtk.TextBuffer()
        self.time_tag = self.buffer.create_tag('time')
        self.heading_tag = self.buffer.create_tag('heading', weight=Pango.Weight.BOLD,
                                                  pixels_above_lines=18, pixels_below_lines=6)
        self.match_tag = self.buffer.create_tag('match', background_rgba=rgba('rgba(246, 211, 45, 0.45)'))
        self.current_tag = self.buffer.create_tag('current-match', background_rgba=rgba('rgba(255, 140, 0, 0.75)'))
        self.end = self.buffer.create_mark('end', self.buffer.get_end_iter(), False)
        # The caret shows, so the keyboard (Shift+arrows) can select, and screen readers can follow it
        self.view = Gtk.TextView(buffer=self.buffer, editable=False,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=18, right_margin=18,
                                 top_margin=6, bottom_margin=18)
        self.scroller = Gtk.ScrolledWindow(child=self.view, vexpand=True)
        self.stack = Gtk.Stack()
        self.stack.add_named(Adw.StatusPage(icon_name='format-justify-left-symbolic', title='No captions yet',
                                            description='Each line appears here once it is finished.'), 'empty')
        self.stack.add_named(self.scroller, 'text')
        self.toasts = Adw.ToastOverlay(child=self.stack)

        self.entry = Gtk.SearchEntry(placeholder_text='Find in transcript', hexpand=True)
        self.count = Gtk.Label(css_classes=['dim-label', 'numeric'])
        bar = Gtk.Box(spacing=6)
        bar.append(self.entry)
        bar.append(self.count)
        for icon, tooltip, step in (('go-up-symbolic', 'Previous match', -1), ('go-down-symbolic', 'Next match', 1)):
            button = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
            button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
            self.connect_to(button, 'clicked', lambda b, step=step: self.step(step))
            bar.append(button)
        self.search = Gtk.SearchBar(child=Adw.Clamp(child=bar, maximum_size=560), show_close_button=True)
        self.search.connect_entry(self.entry)
        self.search.set_key_capture_widget(self)  # typing anywhere starts a search
        self.connect_to(self.entry, 'search-changed', lambda e: self.find())
        self.connect_to(self.entry, 'activate', lambda e: self.step(1))
        self.connect_to(self.entry, 'next-match', lambda e: self.step(1))
        self.connect_to(self.entry, 'previous-match', lambda e: self.step(-1))

        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title='Transcript')
        header.set_title_widget(self.title)
        find = Gtk.ToggleButton(icon_name='edit-find-symbolic', tooltip_text='Find (Ctrl+F)')
        find.update_property([Gtk.AccessibleProperty.LABEL], ['Find'])
        find.bind_property('active', self.search, 'search-mode-enabled',
                           GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        header.pack_start(find)
        for icon, tooltip, action in (('user-trash-symbolic', 'Delete history…', self.confirm_delete),
                                      ('document-save-symbolic', 'Save…', app.save_transcript),
                                      ('edit-copy-symbolic', 'Copy all', self.copy_all)):
            button = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
            button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
            self.connect_to(button, 'clicked', lambda b, action=action: action())
            header.pack_end(button)
        page = Adw.ToolbarView(content=self.toasts)
        page.add_top_bar(header)
        page.add_top_bar(self.search)
        self.set_content(page)

        keys = Gtk.ShortcutController()  # each action is given the window (w), so GTK holds nothing that holds it
        for trigger, action in (('<Control>f', lambda w: w.search.set_search_mode(True)),
                                ('<Control>w', lambda w: w.close())):
            keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(trigger),
                                           action=Gtk.CallbackAction.new(lambda w, a, call=action: call(w) or True)))
        self.add_controller(keys)
        self.connect_to(Adw.StyleManager.get_default(), 'notify::dark', lambda *a: GLib.idle_add(self.restyle))
        self.connect('close-request', self.on_close)
        self.restyle()
        self.reload()

    def connect_to(self, obj: GObject.Object, signal: str, handler: Callable[..., object]) -> None:
        """Connect handler to obj's signal until this window closes."""
        self.handlers.append((obj, obj.connect(signal, handler)))

    def on_close(self, window):
        """Let go of what GTK holds that refers back to this window: the handlers and the refresh
        timer. Python's garbage collector cannot see those cycles, so otherwise the closed window and
        the whole history in it would never be freed, and it would still follow the theme."""
        self.closed = True
        for obj, handler in self.handlers:
            obj.disconnect(handler)
        self.handlers = []
        if self.refresh_id is not None:
            GLib.source_remove(self.refresh_id)
            self.refresh_id = None
        self.matches, self.match = [], -1
        GLib.idle_add(self.buffer.set_text, '')  # after the window has gone, or its view starts scrolling as it closes
        return False

    def restyle(self) -> bool:
        """Dim the times from the text colour, which follows the light or dark style. False, so
        it runs once as an idle callback."""
        colour = self.view.get_color()  # the text colour, dimmed for the times
        colour.alpha *= 0.55
        self.time_tag.set_property('foreground-rgba', colour)
        return False

    def show_title(self) -> None:
        """Say under the title what the window shows: how long sessions are kept, or this one."""
        keep = self.app.settings.get('history')
        self.title.set_subtitle(history_title(self.app.history_days()) if keep else 'This session only')

    def reload(self) -> None:
        """Read the kept sessions again and fetch this one."""
        self.app.prune_history()
        if self.current:
            self.buffer.delete_mark(self.current)
        self.buffer.set_text('')
        for started, text in self.app.earlier_sessions():
            self.add_lines([session_heading(started)] + text.splitlines())
        self.current = self.buffer.create_mark(None, self.buffer.get_end_iter(), True)  # stays before this session
        self.block = []
        self.find(keep=True)  # the old matches went with the text
        self.show_title()
        self.show_page()
        self.refresh()
        GLib.idle_add(self.scroll_to_end)

    def add_lines(self, lines: list[str], heading: bool = True) -> None:
        """Append lines; the first is a session's heading unless heading is False."""
        for i, line in enumerate(lines):
            if i == 0 and heading:
                self.buffer.insert_with_tags(self.buffer.get_end_iter(), line + '\n', self.heading_tag)
                continue
            stamp = TIME.match(line)
            if stamp:
                self.buffer.insert_with_tags(self.buffer.get_end_iter(), stamp.group(1), self.time_tag)
                line = ' ' + line[stamp.end():]
            self.buffer.insert(self.buffer.get_end_iter(), line + '\n')

    def changed(self) -> None:
        """A caption changed: fetch this session again soon (at most twice a second)."""
        if self.refresh_id is None:
            self.refresh_id = GLib.timeout_add(self.REFRESH_MS, self.refresh)

    def refresh(self) -> bool:
        """Ask the recognition worker for this session's lines again (apply gets them). False, so
        it runs once as a timer callback."""
        self.refresh_id = None
        if self.app.worker and not self.app.closing and not self.closed:
            try:
                self.app.worker.snapshot(self.apply)
            except RuntimeError as e:
                LOG.warning('Could not refresh the transcript: %s', e)
        return False

    def apply(self, text: str) -> None:
        """Show this session's transcript, replacing only the lines that changed."""
        if self.closed:
            return
        lines = text.splitlines()
        block = [session_heading(self.app.started)] + lines if lines else []
        same = 0
        while same < min(len(block), len(self.block)) and block[same] == self.block[same]:
            same += 1
        if same == len(block) == len(self.block):
            return
        follow = self.at_end()
        start = self.buffer.get_iter_at_mark(self.current).get_offset()
        start += sum(len(line) + 1 for line in self.block[:same])
        self.buffer.delete(self.buffer.get_iter_at_offset(start), self.buffer.get_end_iter())
        self.add_lines(block[same:], heading=same == 0)
        self.block = block
        self.show_page()
        if self.entry.get_text():
            self.find(keep=True, since=start)
        if follow:
            GLib.idle_add(self.scroll_to_end)

    def at_end(self) -> bool:
        """Whether the view is scrolled to the end, give or take 24 px."""
        adjustment = self.scroller.get_vadjustment()
        return adjustment.get_value() + adjustment.get_page_size() >= adjustment.get_upper() - 24

    def scroll_to_end(self) -> bool:
        """Scroll to the last line. False, so it runs once as an idle callback."""
        self.view.scroll_to_mark(self.end, 0, False, 0, 1)
        return False

    def show_page(self) -> None:
        """Show the text, or while there is none, 'No captions yet'."""
        self.stack.set_visible_child_name('text' if self.buffer.get_char_count() else 'empty')

    def find(self, keep: bool = False, since: int = 0) -> None:
        """Highlight every match of the search text and go to the newest (or keep the place).
        Matches before offset since are kept: apply() passes where its new lines begin, so live
        captions search only those again, however long the history is."""
        text = self.entry.get_text()
        if '\n' in text or text != self.searched:  # other text, or pasted lines whose match could begin before since
            since = 0
        self.searched = text
        # Matches in the lines apply() replaced had their start marks left at since (they keep to the left)
        kept = bisect.bisect_left(self.matches, since, key=lambda m: self.buffer.get_iter_at_mark(m[0]).get_offset())
        where, end = self.buffer.get_iter_at_offset(since), self.buffer.get_end_iter()
        self.buffer.remove_tag(self.match_tag, where, end)
        self.buffer.remove_tag(self.current_tag, *self.buffer.get_bounds())
        for a, b in self.matches[kept:]:
            self.buffer.delete_mark(a)
            self.buffer.delete_mark(b)
        del self.matches[kept:]
        while text:
            found = where.forward_search(text, Gtk.TextSearchFlags.CASE_INSENSITIVE, None)
            if found and len(found) == 3:  # (found, start, end) in older PyGObject
                found = found[1:] if found[0] else None
            if not found:
                break
            a, b = found
            self.buffer.apply_tag(self.match_tag, a, b)
            self.matches.append((self.buffer.create_mark(None, a, True), self.buffer.create_mark(None, b, False)))
            where = b
        if keep and 0 <= self.match < len(self.matches):
            self.show_match(scroll=False)
        else:
            self.match = len(self.matches) - 1
            self.show_match(scroll=True)

    def step(self, direction: int) -> None:
        """Go to the next match (1) or the previous one (-1), going round at either end."""
        if self.matches:
            self.buffer.remove_tag(self.current_tag, *self.buffer.get_bounds())
            self.match = (self.match + direction) % len(self.matches)
            self.show_match(scroll=True)

    def show_match(self, scroll: bool) -> None:
        """Highlight the current match and say which it is; with scroll, also go to it."""
        if not self.entry.get_text():
            self.count.set_label('')
        elif not self.matches:
            self.count.set_label('No matches')
        else:
            a, b = self.matches[self.match]
            self.buffer.apply_tag(self.current_tag, self.buffer.get_iter_at_mark(a), self.buffer.get_iter_at_mark(b))
            self.count.set_label(f'{self.match + 1} of {len(self.matches)}')
            if scroll:
                self.buffer.place_cursor(self.buffer.get_iter_at_mark(a))  # the arrow keys go on from the match
                self.view.scroll_to_mark(a, 0.1, True, 0, 0.3)

    def copy_all(self) -> None:
        """Copy every kept session and this one, each under its heading, and say so."""
        self.app.copy_transcript()
        self.toasts.add_toast(Adw.Toast(title='Transcript copied', timeout=2))

    def confirm_delete(self) -> None:
        """Ask before deleting every kept transcript, this session's included."""
        span = engine.history_span(self.app.history_days())
        kept = (f'everything NowCaption has kept from the last {span}' if span
                else 'every transcript NowCaption has kept')
        dialog = Adw.AlertDialog(heading='Delete all transcripts?',
                                 body=f'This deletes {kept}, including this session, and clears the captions '
                                      'on screen.')
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('delete', 'Delete')
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', lambda d, response: response == 'delete' and self.app.delete_history())
        dialog.present(self)
