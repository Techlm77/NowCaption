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

Dependencies go one way, with no modules importing each other in a circle:

- `app` uses `control`, `ui`, `desktop`, `engine`, `settings` and `words`: it makes the
  collaborators and the windows and wires them together;
- `control` uses `engine`, `words` and `style`. Each collaborator holds the app and reaches the
  windows, the settings and the other collaborators through it;
- `ui` uses `engine`, `desktop`, `settings` and `style`, never `control` or `app`. A window holds the
  app and uses only: the methods app.py groups under "what the windows ask of the app"
  (`history_days()`, `save_transcript()`, `worker`…), short forwarders to a collaborator, and
  `set_words()`; its start-at-login and shortcut methods (`starts_at_login()`, `set_autostart()`,
  `set_shortcuts()`); its actions (`app.colour`, `lookup_action()`); and the attributes `settings`,
  `word_list`, `shortcuts`, `fullscreen_watch`, `panel_float`, `closing` and `started`;
- `desktop` uses nothing else in NowCaption;
- `engine` uses `words` only, and never gi: `engine/capture.py` imports GStreamer inside
  `Capture.__init__`, when a Capture is made, so the engine imports with NumPy alone;
- `settings` uses `style` and `engine` (the history constants); `style` and `words` use nothing else
  in NowCaption. None of the three imports GTK at module level (`settings` keeps its file with
  GLib's key file);
- only `launch` imports `app`, inside `main()` once gtk4-layer-shell is loaded, and `__main__` imports
  only `launch`. The package's own `__init__.py` holds the version and imports nothing.

Imports under `if TYPE_CHECKING:` never run, so they may name a type against these directions:
`control` names `NowCaption` that way, and `desktop` names `Settings`. The windows leave the app
unannotated (their tests pass fakes). Imports inside functions do run, so they follow the rules.

`tests/test_architecture.py` checks these rules. It reads the source with `ast` and imports
nothing, so it needs only the standard library, and each failure names the file, the line and the
rule broken. A new top-level module or package goes in the layout above and in the test's `MAY_USE`
table.

A package's `__init__.py` states its public API (explicit imports and `__all__`), so other
code can write `engine.RecognitionWorker`. It stays short, with no logic in it.

## Conventions

- One clear responsibility per module and per class. Each module starts with a short
  docstring saying what it does. The dependency rules live here, under "Package layout", where
  `tests/test_architecture.py` checks them, and each package's `__init__.py` docstring repeats its
  own rule in a line.
- No GTK or Adw in `engine/`: it must import with NumPy alone, so its tests run anywhere.
- Comments explain *why*, not what the next line does. Plain English, British spelling.
- Public classes, functions and methods have a docstring (a class's covers its `__init__`), GTK
  signal handlers and virtual methods (`on_*`, `do_*`) included. Most also have type hints
  (Python 3.10 syntax, `X | None`). Those without are mostly callbacks whose arguments GTK, GLib,
  D-Bus or GStreamer pass in (the `on_*` and `do_*` methods, timers, D-Bus signals),
  sherpa-onnx's model objects, and the app the windows take, which their tests replace with
  fakes. Modules with annotations start with `from __future__ import annotations`, so
  annotations cost nothing at run time. `Callable` and `Iterable` come from `collections.abc`
  (the standard library has loaded it already); anything else needed only for annotations is
  imported under `if TYPE_CHECKING:`.
- Python 3.10 is the oldest supported version; CI also runs 3.12 and 3.14.
- Tests are never weakened: don't loosen an assertion or delete a test to make a change pass.
  When code moves, point the test at its new place (a monkeypatch must target the module where
  the name is looked up).

## Running the tests

Each file in `tests/` is a plain unittest script, run on its own from the repository root:

```sh
PYTHONPATH=src:tests python3 tests/test_reliability.py
```

Tests that use GTK (`test_app.py`, `test_window.py`, `test_qol_features.py` and the
`test_fixes_1116_*.py` files) need the system PyGObject with GTK 4 and libadwaita, and those
that build windows need a display. Run them under Xvfb with `NOWCAPTION_REQUIRE_GUI_TESTS=1`,
so a missing GTK fails the run instead of skipping its tests:

```sh
xvfb-run -a env NOWCAPTION_REQUIRE_GUI_TESTS=1 PYTHONPATH=src:tests /usr/bin/python3 tests/test_window.py
```

`.github/workflows/tests.yml` lists exactly what CI runs. `tests/test_live.py` and
`tests/test_memory.py` need the downloaded models and sherpa-onnx (the `.venv` that
`tools/run-dev.sh` sets up), and `test_live.py` also a PulseAudio or PipeWire server, so CI does
not run them.
`tests/test_flatpak.sh` tests an installed Flatpak. Also check that everything compiles:

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
