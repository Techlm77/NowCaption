# Contributing to NowCaption

NowCaption is a GTK 4 / libadwaita app in Python. To run it from a checkout, see
"Run from source" in [README.md](README.md).

## Package layout

```
src/nowcaption/
  __init__.py  __main__.py  launch.py  app.py      # app.py = the Adw.Application, wiring only
  settings.py  style.py  words.py                  # plain data/config, no GTK imports at module level
  control/     # what the app does, one collaborator per job: Listening, History, Saving, Quitting,
               # Appearance, the actions and menu (actions.py) and launch options (commands.py)
  engine/      # speech pipeline + transcript storage. No GTK/Adw (GStreamer only in capture.py)
  ui/          # every GTK widget and window
  desktop/     # desktop integration: XDG portals, autostart, KDE Plasma/KWin
```

Dependencies go one way, with no cycles:

- `app` uses `control`, `ui`, `desktop`, `engine`, `settings` and `words`: it makes the
  collaborators and the windows and wires them together;
- `control` uses `engine`, `words` and `style`. Each collaborator holds the app and reaches the
  windows, the settings and the other collaborators through it;
- `ui` uses `engine`, `desktop`, `settings` and `style`, never `control`: the windows ask the app,
  whose short forwarders (`history_days`, `save_transcript`…) pass the call on;
- `desktop` uses nothing else in NowCaption;
- `engine` uses `words` only;
- `settings` uses `style` and `engine` (the history constants);
- only `launch` imports `app`, once gtk4-layer-shell is loaded.

Imports under `if TYPE_CHECKING:` never run, so they may name a type against these directions:
`control` names `NowCaption` that way, and `desktop` names `Settings`. The windows leave the app
unannotated (their tests pass fakes).

A package's `__init__.py` states its public API (explicit imports and `__all__`), so other
code can write `engine.RecognitionWorker`. It stays short, with no logic in it.

## Conventions

- One clear responsibility per module and per class. Each module starts with a short
  docstring saying what it does and what it must not depend on.
- No GTK or Adw in `engine/`: it must import with NumPy alone, so its tests run anywhere.
- Comments explain *why*, not what the next line does. Plain English, British spelling.
- Public functions and methods have type hints (Python 3.10 syntax, `X | None`) and a
  docstring. Modules with annotations start with `from __future__ import annotations`, so
  annotations cost nothing at run time. `Callable` and `Iterable` come from `collections.abc`
  (the standard library has loaded it already); anything else needed only for annotations is
  imported under `if TYPE_CHECKING:`.
- Python 3.10 is the oldest supported version; CI also runs 3.12 and 3.14.
- Tests are never weakened: don't loosen an assertion or delete a test to make a change pass.
  When code moves, point the test at its new place (a monkeypatch must target the module where
  the name is looked up).

## Running the tests

`tests/run.py` runs the tests the way CI does, each test module in a process of its own:

```sh
python3 tests/run.py                  # the core tests: Python 3.10+ and NumPy are enough
/usr/bin/python3 tests/run.py --gui   # the GUI tests, each module under xvfb-run
/usr/bin/python3 tests/run.py --all   # everything CI runs
python3 tests/run.py tests/engine     # one folder, or name test modules
```

A module is a GUI test when it imports `gi`, `cairo` or `yaml`, itself or through
`tests/support`: it needs the system's Python with PyGObject (GTK 4, libadwaita) and PyYAML,
and `xvfb-run`. The runner gives GUI tests `NOWCAPTION_REQUIRE_GUI_TESTS=1`, so a missing GTK
fails them instead of skipping them; `--list` shows which group each module is in. CI's
`reliability` job runs the core tests on Python 3.10, 3.12 and 3.14, and its `app` job the GUI
tests. A test module also runs on its own, from the repository root:

```sh
PYTHONPATH=src:tests python3 tests/engine/test_worker.py
```

The tests are grouped by what they test, mirroring `src/nowcaption/`:

```
tests/
  run.py       # the runner
  support/     # what test modules share: fakes, the private XDG folders, GTK and the display
  engine/      # engine/ and words.py: worker, Captioner, punctuation, capture, history, models
  app/         # app.py, control/ and settings.py: listening, saving, quitting, commands
  ui/          # the windows: caption window, docking, pointer, animations, options, transcript
  desktop/     # desktop/: KDE Plasma's panels, autostart
  packaging/   # the version, the speech model's files, the AppImage, the release workflows
  tools/       # the scripts in tools/ and tests/, and the benchmark's scoring
```

A new test goes in the module named after what it tests (`ui/caption_window/docking.py` is
tested in `ui/test_docking.py`), as a `unittest.TestCase` whose docstring says why it exists.
Every test module starts with `import support.sandbox`, which gives the process private XDG
folders (no test may touch the user's settings, transcript or history). Fakes and helpers that
more than one module needs go in `tests/support/`, never in a test module: test modules never
import each other. Tests that build the real application use `support.gtk` (and
`support.windows` for windows); those that need only NumPy run the application's own methods
through `support.app_loader`.

The scripts at the top of `tests/` are run by hand. `tests/test_live.py` and
`tests/test_memory.py` need the downloaded models and sherpa-onnx (the `.venv` that
`tools/run-dev.sh` sets up), and `test_live.py` also a PulseAudio or PipeWire server.
`tests/test_flatpak.sh` tests an installed Flatpak, with `capture_print.py`, `transcript_check.py`
and the clips in `tests/fixtures/`. Also check that everything compiles:

```sh
python3 -m compileall -q src tests benchmark tools
```

## Lint

The rules are in `pyproject.toml` under `[tool.ruff]`, each ignored rule with its reason. CI
runs Ruff 0.17.4 (`pip install ruff==0.17.4`; other versions may differ slightly):

```sh
ruff check src benchmark tools
```

`tests/` joins that list after its own cleanup. There is no formatter: don't
reformat code you are not otherwise changing. When a finding is a false positive, add
`# noqa: <code>` with a comment saying why, rather than rewriting working code to please it.

## Releases

The version is written in three places that must agree (`tests/test_version.py` checks):
`pyproject.toml`, `src/nowcaption/__init__.py` and the newest `<release>` in
`data/io.github.Techlm77.NowCaption.metainfo.xml`. Push `main` and wait for CI to pass before
pushing the `v*` tag; the tag builds the Flatpak and AppImage release files.
