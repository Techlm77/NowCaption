# Contributing to NowCaption

NowCaption is a GTK 4 / libadwaita app in Python. To run it from a checkout, see
"Run from source" in [README.md](README.md).

## Package layout

```
src/nowcaption/
  __init__.py  __main__.py  launch.py  app.py      # app.py = the Adw.Application, wiring only
  settings.py  style.py  words.py                  # plain data/config, no GTK imports at module level
  engine/      # speech pipeline + transcript storage. No GTK/Adw (GStreamer only in capture.py)
  ui/          # every GTK widget and window
  desktop/     # desktop integration: XDG portals, autostart, KDE Plasma/KWin
```

Dependencies go one way, with no cycles:

- `app` uses `ui`, `desktop`, `engine` and `settings`;
- `ui` uses `engine`, `desktop`, `settings` and `style`;
- `engine` uses `words` only;
- `settings` uses `style` and `engine` (the history constants);
- nothing imports `app`.

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
