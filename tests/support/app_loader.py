"""The application's own methods without GTK: the methods the tests use of NowCaption,
CaptionWindow and the control/ collaborators are compiled from their source with AST, without
their classes' bases, into one namespace (APP) that holds the module names they use. The tests
that need only NumPy run them with fakes around them (app_with_fakes()).
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from nowcaption import engine

ROOT = Path(__file__).resolve().parents[2]  # the repository


def load_app_methods():
    wanted = {'on_source', 'start_capture', 'on_worker', 'clear_transcript', 'on_shutdown',
              'capture_ended', 'capture_started', 'retry_capture', 'width_for', 'watch_monitor', 'use_monitor',
              'on_monitor_geometry', 'disconnect_monitors', 'on_monitors_changed',
              'cancel_recognition_retry', 'retry_recognition', 'request_quit', 'on_window_close', 'threads_finished',
              'save_transcript', 'write_transcript', 'start_save', 'begin_save', 'finish_save',
              'full_transcript', 'earlier_sessions', 'history_days', 'stop',
              'show_text', 'show_status', 'show_failure', 'clear_failure', 'end_status',
              'render', 'show_footer', 'caption_height', 'box_height'}
    context = {'os': __import__('os'), 'engine': engine,
               'session_heading': lambda started: 'Thursday 23 October 2026, 14:05',
               'LOG': __import__('logging').getLogger('nowcaption'),
               'GLib': NS(timeout_add_seconds=lambda *a: 1, source_remove=lambda *a: None),
               'LayerShell': NS(set_monitor=lambda *a: None),
               'ROWS': 2, 'PAD_Y': 12, 'STATUS_MS': 150}
    for name, module in (('NowCaption', 'app.py'), ('CaptionWindow', 'ui/caption_window/window.py'),
                         ('Listening', 'control/listening.py'), ('History', 'control/history.py'),
                         ('Saving', 'control/saving.py'), ('Quitting', 'control/quitting.py')):
        path = ROOT / 'src/nowcaption' / module
        [node] = [n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef) and n.name == name]
        node.bases, node.keywords, node.decorator_list = [], [], []
        node.body = [m for m in node.body if isinstance(m, ast.FunctionDef) and m.name in wanted]
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(path), 'exec'),
             context)
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
