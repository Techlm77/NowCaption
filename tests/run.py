#!/usr/bin/env python3
"""Run the tests, each test module in a process of its own (as CI does), and sum up.

    python3 tests/run.py               the core tests: they need only Python 3.10+ and NumPy
    python3 tests/run.py --gui         the GUI tests, each under xvfb-run
    python3 tests/run.py --all         both
    python3 tests/run.py PATH ...      these test modules, or the modules in these folders

A test module is a GUI test when it imports gi, cairo or yaml, itself or through a module in
tests/support: it needs the system's Python, with PyGObject (GTK 4, libadwaita) and PyYAML, and
the display Xvfb gives it. GUI tests run with NOWCAPTION_REQUIRE_GUI_TESTS=1, so a missing GTK
fails them instead of skipping them. The scripts at the top of tests/ (test_live.py and so on)
need real models or audio and are run by hand.
"""
import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
GUI_PACKAGES = {'gi', 'cairo', 'yaml'}
TIMEOUT = 600  # seconds for one module


def imported(path):
    """The modules a file imports, anywhere in it."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update(f'{node.module}.{alias.name}' for alias in node.names)
    return names


def needs_gui(path, seen=None):
    """Whether a test module imports gi, cairo or yaml, itself or through tests/support."""
    seen = set() if seen is None else seen
    for name in imported(path):
        if name.split('.')[0] in GUI_PACKAGES:
            return True
        support = TESTS / (name.replace('.', '/') + '.py')
        if name.startswith('support.') and support.is_file() and support not in seen:
            seen.add(support)
            if needs_gui(support, seen):
                return True
    return False


def modules(paths):
    """The test modules: tests/<area>/test_*.py, or those in or at the given paths."""
    if not paths:
        paths = [p for p in sorted(TESTS.iterdir()) if p.is_dir() and p.name not in ('support', 'fixtures')]
    found = []
    for path in map(Path, paths):
        path = path.resolve()
        if path.is_dir():
            found += sorted(path.rglob('test_*.py'))
        elif path.is_file():
            found.append(path)
        else:
            sys.exit(f'run.py: no such file or folder: {path}')
    return found


def run(path, gui):
    """Run one module; returns (ok, counts, output)."""
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(filter(None, [str(ROOT / 'src'), str(TESTS), env.get('PYTHONPATH')]))
    command = [sys.executable, str(path)]
    if gui:
        env['NOWCAPTION_REQUIRE_GUI_TESTS'] = '1'
        # GTK prefers Wayland when WAYLAND_DISPLAY is set, which would put the test windows on the
        # desktop of whoever runs the tests instead of on xvfb-run's display.
        env.pop('WAYLAND_DISPLAY', None)
        env['GDK_BACKEND'] = 'x11'
        command = ['xvfb-run', '-a'] + command
    try:
        done = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=TIMEOUT)
        output, ok = done.stdout, done.returncode == 0
    except subprocess.TimeoutExpired as e:
        output, ok = (e.stdout or '') + f'\nrun.py: stopped after {TIMEOUT} s\n', False
    ran = re.findall(r'^Ran (\d+) tests? in', output, re.M)
    counts = {'tests': int(ran[-1]) if ran else 0, 'failures': 0, 'errors': 0, 'skipped': 0}
    status = re.findall(r'^(?:OK|FAILED)(?: \((.*)\))?$', output, re.M)
    for part in (status[-1] if status else '').split(', '):
        kind, _, number = part.partition('=')
        if kind in counts:
            counts[kind] = int(number)
    ok = ok and bool(ran) and bool(status)  # a module that crashed before running counts as failed
    return ok, counts, output


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--gui', action='store_true', help='run the GUI tests (under xvfb-run)')
    group.add_argument('--all', action='store_true', help='run the core and the GUI tests')
    parser.add_argument('--list', action='store_true', help='list the modules and their group, run nothing')
    parser.add_argument('-v', '--verbose', action='store_true', help="show every module's output")
    parser.add_argument('paths', nargs='*', help='test modules or folders (default: every area folder)')
    args = parser.parse_args()

    chosen = [(path, needs_gui(path)) for path in modules(args.paths)]
    if not args.paths:
        chosen = [(path, gui) for path, gui in chosen if args.all or gui == args.gui]
    if args.list:
        for path, gui in chosen:
            print(f"{'gui ' if gui else 'core'}  {path.relative_to(TESTS)}")
        return 0
    if any(gui for _, gui in chosen) and not shutil.which('xvfb-run'):
        sys.exit('run.py: the GUI tests need xvfb-run (the xvfb package)')

    totals, failed, start = {'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0}, [], time.monotonic()
    for path, gui in chosen:
        began = time.monotonic()
        ok, counts, output = run(path, gui)
        for key in totals:
            totals[key] += counts[key]
        name = path.relative_to(TESTS)
        extra = ', '.join(f'{counts[k]} {k}' for k in ('failures', 'errors', 'skipped') if counts[k])
        tests = f"{counts['tests']} test{'s' if counts['tests'] != 1 else ''}"
        print(f"{'ok  ' if ok else 'FAIL'}  {name}  {tests}{', ' + extra if extra else ''}"
              f'  ({time.monotonic() - began:.1f} s)', flush=True)
        if args.verbose or not ok:
            print(output.rstrip() + '\n', flush=True)
        if not ok:
            failed.append(str(name))
    print(f"\n{len(chosen)} modules, {totals['tests']} tests, {totals['failures']} failures, "
          f"{totals['errors']} errors, {totals['skipped']} skipped in {time.monotonic() - start:.0f} s")
    if failed:
        print('Failed: ' + ' '.join(failed))
    return 1 if failed or not chosen else 0


if __name__ == '__main__':
    sys.exit(main())
