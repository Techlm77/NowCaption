"""The application's own methods without GTK, for the tests that need only NumPy: METHODS names the
methods of NowCaption, CaptionWindow and the control/ collaborators that they run, compiled from
their source with AST (annotations postponed, as the modules' `from __future__ import annotations`
does) into classes without bases or __init__, in one namespace, APP, that holds every module name
those methods use. app_with_fakes() puts them together with fakes around them.

A test that strays beyond what is loaded fails loudly and says why: a listed method missing from
its source, or a module name the methods use that APP lacks, fails at load; a module that a test
must patch into APP first (Gtk, Gio, gtk4-layer-shell) raises an AssertionError naming it; and an
attribute a loaded object was never given (such as CaptionWindow.docking, which the __init__ that
is not loaded would make) raises an AttributeError that says so.
"""
import __future__

import ast
import builtins
import logging
import os
from pathlib import Path
from types import SimpleNamespace as NS

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine
from nowcaption.style import PAD_Y, STATUS_MS

ROOT = Path(__file__).resolve().parents[2]  # the repository

# The methods the tests run, of each class in its source file under src/nowcaption.
METHODS = {
    ('NowCaption', 'app.py'): ['earlier_sessions', 'history_days', 'on_shutdown', 'save_transcript',
                               'threads_finished'],
    ('CaptionWindow', 'ui/caption_window/window.py'): ['box_height', 'caption_height', 'clear_failure',
                                                       'disconnect_monitors', 'end_status', 'on_monitor_geometry',
                                                       'render', 'show_failure', 'show_footer', 'show_status',
                                                       'show_text', 'use_monitor', 'watch_monitor', 'width_for'],
    ('Listening', 'control/listening.py'): ['cancel_recognition_retry', 'capture_ended', 'capture_started',
                                            'clear_transcript', 'on_source', 'on_worker', 'retry_capture',
                                            'retry_recognition', 'start_capture', 'stop', 'threads_finished'],
    ('History', 'control/history.py'): ['earlier_sessions', 'full_transcript', 'history_days'],
    ('Saving', 'control/saving.py'): ['save_transcript', 'start_save', 'write_transcript'],
    ('Quitting', 'control/quitting.py'): ['begin_save', 'finish_save', 'on_window_close', 'request_quit'],
}


class Unloaded:
    """A module the loaded methods use that is not loaded: a test that needs it patches a fake into
    APP (patch.dict(APP, Gtk=...)); anything else that reaches it fails with this message."""

    def __init__(self, name, why):
        self.name, self.why = name, why

    def __getattr__(self, attr):
        raise AssertionError(f'{self.name}.{attr}: {self.why}')


def missing_attribute(cls, methods):
    """__getattr__ for a loaded class: its attributes are what the test gave the object."""
    def __getattr__(self, name):
        raise AttributeError(f'{cls}.{name}: support/app_loader.py loads only these methods of {cls} '
                             f'({", ".join(methods)}), not its __init__; the test gives the object '
                             'everything else it uses')
    return __getattr__


def global_names(function):
    """The module names a method uses: the names it reads that it does not bind itself, apart from
    the builtins and its annotations (postponed, never evaluated)."""
    reads, binds = set(), set()

    def visit(node):
        if isinstance(node, ast.arg):
            binds.add(node.arg)
            return  # its annotation is never evaluated
        if isinstance(node, ast.Name):
            (reads if isinstance(node.ctx, ast.Load) else binds).add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)) or (isinstance(node, ast.ExceptHandler) and node.name):
            binds.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            binds.update((alias.asname or alias.name).split('.')[0] for alias in node.names)
        for field, child in ast.iter_fields(node):
            if field in ('returns', 'annotation'):
                continue
            for item in child if isinstance(child, list) else [child]:
                if isinstance(item, ast.AST):
                    visit(item)
    visit(function)
    return reads - binds - set(dir(builtins))


def load_app_methods():
    context = {'os': os, 'engine': engine,
               'session_heading': lambda started: 'Thursday 23 October 2026, 14:05',
               'LOG': logging.getLogger('nowcaption'),
               'GLib': NS(timeout_add_seconds=lambda *a: 1, source_remove=lambda *a: None),
               'Gio': Unloaded('Gio', 'the tests that save patch a fake Gio into APP'),
               'Gtk': Unloaded('Gtk', 'the tests that save patch a fake Gtk (its FileDialog) into APP'),
               'layer_shell': Unloaded('layer_shell', 'gtk4-layer-shell is not loaded without GTK: test the '
                                       'docked window on the test display (tests/ui/test_docking.py)'),
               'PAD_Y': PAD_Y, 'STATUS_MS': STATUS_MS}
    for (name, module), methods in METHODS.items():
        path = ROOT / 'src/nowcaption' / module
        [node] = [n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef) and n.name == name]
        found = {m.name: m for m in node.body if isinstance(m, ast.FunctionDef)}
        lost = [m for m in methods if m not in found]
        assert not lost, f'support/app_loader.py: {module} has no {name}.{", ".join(lost)} any more'
        for method in methods:
            unknown = global_names(found[method]) - set(context) - {n for n, _ in METHODS}
            assert not unknown, (f'support/app_loader.py: {name}.{method} in {module} uses '
                                 f'{", ".join(sorted(unknown))}, which APP does not have')
        node.bases, node.keywords, node.decorator_list = [], [], []
        node.body = [found[m] for m in methods]
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(path), 'exec',
                     __future__.annotations.compiler_flag, dont_inherit=True), context)
        context[name].__getattr__ = missing_attribute(name, methods)
    return context


APP = load_app_methods()


def collaborator(name, app, **state):
    """One of the app's collaborators (control/), holding the app and this state."""
    part = APP[name]()
    part.app = app
    part.__dict__.update(state)
    return part


def app_with_fakes():
    a = APP['NowCaption']()
    settings = {'source': 'desktop', 'history_days': engine.HISTORY_DAYS}
    a.settings = NS(get=settings.get, set=settings.__setitem__)
    a.window = NS(text='', statuses=[])
    a.window.show_text = lambda text: setattr(a.window, 'text', text)
    a.window.show_status = lambda text, seconds=None, sticky=False: a.window.statuses.append(text)
    a.window.show_failure = lambda scope, text: a.window.statuses.append(text)
    a.window.clear_failure = lambda scope: None
    a.closing = False
    a.hidden = False
    a.shortcuts = None
    a.transcript = None
    a.started = 1792760700.0
    a.listening = collaborator('Listening', a, worker=None, ready=False, paused=False, source_gen=0, ui_gen=0,
                               capture_failures=0, recognition_failures=0, retry_id=None,
                               recognition_retry_id=None, recognition_retry_token=0)
    a.history = collaborator('History', a, history_path=engine.history_file(a.started))
    a.saving = collaborator('Saving', a, save_queues={})
    a.quitting = collaborator('Quitting', a, pending_saves=0, quit_requested=False, fading_out=False)
    a.listening.capture = NS(starts=[], stop=lambda: None,
                             start=lambda device, gen: a.listening.capture.starts.append((device, gen)))
    a.fullscreen_watch = NS(stop=lambda sync=False: None)  # the KWin script is not these tests' business
    a.panel_float = NS(stop=lambda: None, dock=lambda edge, output='': None)  # nor are Plasma's panels
    return a
