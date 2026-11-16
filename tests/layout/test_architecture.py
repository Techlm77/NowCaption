#!/usr/bin/env python3
"""The package layout's dependency rules (CONTRIBUTING.md, "Package layout"): each part of
src/nowcaption imports only the parts it may use, and no modules import each other in a circle.
This is what keeps the parts apart as the code grows.

The source is read with ast, never imported, so these tests need only the standard library (no
GTK, no NumPy) and run on every supported Python:

    python3 tests/layout/test_architecture.py

NOWCAPTION_ARCHITECTURE_SRC=<a folder holding a nowcaption package> checks that copy instead of
this checkout's src/, to see a planted violation fail, for example.
"""
from __future__ import annotations

import ast
import os
import unittest
from dataclasses import dataclass
from pathlib import Path

PACKAGE = 'nowcaption'

# Each part of the package and the other parts it may import at run time, in CONTRIBUTING.md's
# order. A part may always import its own modules, and any part may import the package root,
# which holds only the version. Imports under `if TYPE_CHECKING:` never run, so they may name a
# type against these directions; imports inside functions run, so they count.
MAY_USE = {
    '__main__': ('launch',),
    'launch': ('app',),
    'app': ('control', 'ui', 'desktop', 'engine', 'settings', 'words'),
    'control': ('engine', 'words', 'style'),
    'ui': ('engine', 'desktop', 'settings', 'style'),
    'desktop': (),
    'engine': ('words',),
    'settings': ('style', 'engine'),
    'style': (),
    'words': (),
}
FILE_NAMES = {'__main__': '__main__.py', 'launch': 'launch.py', 'app': 'app.py', 'settings': 'settings.py',
              'style': 'style.py', 'words': 'words.py', '': 'the package root (__init__.py)'}

# The one gi import the engine has: capture.py imports GStreamer when a Capture is made, not with
# the module, so the rest of the engine imports with NumPy alone.
CAPTURE, CAPTURE_FUNCTION = f'{PACKAGE}.engine.capture', 'Capture.__init__'
CAPTURE_GI = {'gi': set(), 'gi.repository': {'Gst', 'GstApp'}}

# settings.py, style.py and words.py are plain data and config: no GTK at module level.
PLAIN = ('settings', 'style', 'words')
PLAIN_GI = {'gi': set(), 'gi.repository': {'GLib'}}  # settings.py keeps its file with GLib's KeyFile


def source_folder() -> Path:
    """The folder holding the nowcaption package: NOWCAPTION_ARCHITECTURE_SRC, or this checkout's src/."""
    if os.environ.get('NOWCAPTION_ARCHITECTURE_SRC'):
        return Path(os.environ['NOWCAPTION_ARCHITECTURE_SRC']).resolve()
    for folder in Path(__file__).resolve().parents:  # wherever this file sits under tests/
        if (folder / 'src' / PACKAGE / '__init__.py').is_file():
            return folder / 'src'
    raise FileNotFoundError(f'no src/{PACKAGE} above {__file__}')


@dataclass(frozen=True)
class Import:
    """One import statement's target, as it runs."""
    path: Path            # the importing file
    line: int
    module: str           # the importing module, e.g. nowcaption.ui.preview
    target: str           # the module it imports, e.g. nowcaption.engine or gi.repository
    names: tuple[str, ...]  # what `from target import …` takes, if not modules of their own
    function: str         # the enclosing function, e.g. Capture.__init__; '' at module level


def part_of(module: str) -> str:
    """The part a module belongs to: 'engine' for nowcaption.engine.capture, '' for the package root."""
    parts = module.split('.')
    return parts[1] if len(parts) > 1 else ''


def internal(target: str) -> bool:
    """Whether an import's target is part of NowCaption."""
    return target == PACKAGE or target.startswith(PACKAGE + '.')


def is_gi(target: str) -> bool:
    """Whether an import's target is PyGObject: GTK, GLib, GStreamer and the rest."""
    return target == 'gi' or target.startswith('gi.')


def is_type_checking(test: ast.expr) -> bool:
    """Whether an if statement's test is TYPE_CHECKING (or typing.TYPE_CHECKING)."""
    return ((isinstance(test, ast.Name) and test.id == 'TYPE_CHECKING')
            or (isinstance(test, ast.Attribute) and test.attr == 'TYPE_CHECKING'))


class ImportReader(ast.NodeVisitor):
    """Collects a module's run-time imports, with relative imports resolved to module names."""

    def __init__(self, module: str, path: Path, modules: dict[str, Path]) -> None:
        self.module, self.path, self.modules = module, path, modules
        self.package = module if path.name == '__init__.py' else module.rpartition('.')[0]
        self.scope: list[str] = []  # enclosing classes and functions
        self.depth = 0              # enclosing functions: a class body runs with its module
        self.found: list[Import] = []

    def visit_If(self, node: ast.If) -> None:
        if is_type_checking(node.test):
            for child in node.orelse:  # an else branch does run
                self.visit(child)
        else:
            self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.add(node, alias.name, ())

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            base = self.package.split('.')
            keep = len(base) - (node.level - 1)
            if keep < 1:
                raise ValueError(f'{self.path}:{node.lineno} imports from above the {PACKAGE} package')
            target = '.'.join(base[:keep] + ([node.module] if node.module else []))
        else:
            target = node.module or ''
        names = [alias.name for alias in node.names]
        submodules = [name for name in names if f'{target}.{name}' in self.modules]
        for name in submodules:  # `from package import module` imports that module
            self.add(node, f'{target}.{name}', ())
        rest = tuple(name for name in names if name not in submodules)
        if rest or not submodules:
            self.add(node, target, rest)

    def add(self, node: ast.stmt, target: str, names: tuple[str, ...]) -> None:
        function = '.'.join(self.scope) if self.depth else ''
        self.found.append(Import(self.path, node.lineno, self.module, target, names, function))


class Tree:
    """Every module under a folder's nowcaption package, and the imports each one runs."""

    def __init__(self, src: Path) -> None:
        self.src = src
        self.modules: dict[str, Path] = {}
        for path in sorted((src / PACKAGE).rglob('*.py')):
            parts = path.relative_to(src).with_suffix('').parts
            self.modules['.'.join(parts[:-1] if parts[-1] == '__init__' else parts)] = path
        self.imports: list[Import] = []
        for module, path in self.modules.items():
            reader = ImportReader(module, path, self.modules)
            reader.visit(ast.parse(path.read_text(encoding='utf-8'), str(path)))
            self.imports += reader.found

    def where(self, imp: Import) -> str:
        """file:line of an import, from the repository root."""
        return f'{self.relative(imp.path)}:{imp.line}'

    def relative(self, path: Path) -> str:
        """A file's path from the repository root (the folder above src/)."""
        return str(path.relative_to(self.src.parent))

    def resolve(self, target: str) -> str:
        """The module an internal import runs: the target itself, or the package it is in."""
        while target not in self.modules and '.' in target:
            target = target.rpartition('.')[0]
        return target


def file_name(part: str) -> str:
    """How CONTRIBUTING.md names a part: 'app.py', or 'ui' for a package."""
    return FILE_NAMES.get(part, part)


def listing(parts: tuple[str, ...]) -> str:
    """'a, b and c'."""
    names = [file_name(part) for part in parts]
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]


def rule(part: str) -> str:
    """The dependency rule for one part, in CONTRIBUTING.md's words."""
    if not MAY_USE.get(part):
        return f'{file_name(part)} uses nothing else in NowCaption'
    return f'{file_name(part)} may use only {listing(MAY_USE[part])}'


def gi_import(imp: Import, allowed: dict[str, set[str]]) -> bool:
    """Whether a gi import is one of these targets, taking only these names."""
    return imp.target in allowed and set(imp.names) <= allowed[imp.target]


def described(imp: Import) -> str:
    """An import's target and names, as written: 'gi.repository (Gst, GstApp)'."""
    return f'{imp.target} ({", ".join(imp.names)})' if imp.names else imp.target


class ArchitectureTests(unittest.TestCase):
    """CONTRIBUTING.md's dependency rules, checked against the source."""

    tree: Tree

    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = Tree(source_folder())

    def report(self, problems: list[str], summary: str) -> None:
        """Fail with every problem found, each on its own line, if there are any."""
        if problems:
            self.fail(f'{summary} (CONTRIBUTING.md, "Package layout"):\n  ' + '\n  '.join(problems))

    def test_the_checker_reads_the_whole_package(self):
        tree = self.tree
        self.assertIn(PACKAGE, tree.modules, f'no {PACKAGE} package in {tree.src}')
        problems = [f'{tree.relative(path)} is in no part that CONTRIBUTING.md names: add it to the layout there '
                    f'and to MAY_USE in tests/layout/test_architecture.py'
                    for module, path in tree.modules.items() if part_of(module) not in MAY_USE and module != PACKAGE]
        found = {part_of(module) for module in tree.modules}
        problems += [f'MAY_USE in tests/layout/test_architecture.py names {part}, '
                     f'but {tree.relative(tree.src / PACKAGE)} has no such module or package'
                     for part in MAY_USE if part not in found]
        self.report(problems, 'The layout and these tests disagree')
        self.assertTrue(any(internal(imp.target) for imp in tree.imports), 'found no imports between modules')

    def test_each_part_imports_only_the_parts_it_may_use(self):
        problems = []
        for imp in self.tree.imports:
            if not internal(imp.target):
                continue
            part, used = part_of(imp.module), part_of(self.tree.resolve(imp.target))
            if used in ('', part) or used in MAY_USE.get(part, ()):
                continue
            why = ('only launch.py imports app.py, once gtk4-layer-shell is loaded' if used == 'app'
                   else rule(part))
            problems.append(f'{self.tree.where(imp)} imports {imp.target}, but {why}')
        self.report(problems, 'A part of NowCaption imports a part it must not use')

    def test_the_engine_never_imports_gi(self):
        problems = [f'{self.tree.where(imp)} imports {described(imp)}, but the engine never uses gi (GTK, GLib, '
                    f'GStreamer), so it imports with NumPy alone. Only engine/capture.py may import Gst and GstApp, '
                    f'inside {CAPTURE_FUNCTION}, when a Capture is made'
                    for imp in self.tree.imports
                    if part_of(imp.module) == 'engine' and is_gi(imp.target)
                    and not (imp.module == CAPTURE and imp.function == CAPTURE_FUNCTION
                             and gi_import(imp, CAPTURE_GI))]
        self.report(problems, 'The engine imports gi')

    def test_settings_style_and_words_import_no_gtk_at_module_level(self):
        problems = [f'{self.tree.where(imp)} imports {described(imp)}, but {file_name(part_of(imp.module))} is '
                    f'plain data and config, with no GTK at module level (settings.py uses GLib only)'
                    for imp in self.tree.imports
                    if part_of(imp.module) in PLAIN and not imp.function
                    and is_gi(imp.target) and not gi_import(imp, PLAIN_GI)]
        self.report(problems, 'settings.py, style.py or words.py imports GTK')

    def test_launch_imports_app_only_inside_a_function(self):
        problems = [f'{self.tree.where(imp)} imports {imp.target} at module level, but launch.py imports app.py '
                    f'(and with it GTK) inside main(), after preload_layer_shell() has loaded gtk4-layer-shell'
                    for imp in self.tree.imports
                    if part_of(imp.module) == 'launch' and not imp.function
                    and ((internal(imp.target) and part_of(imp.target) not in ('', 'launch'))
                         or is_gi(imp.target))]
        self.report(problems, 'launch.py imports GTK too early')

    def test_no_modules_import_each_other_in_a_circle(self):
        tree = self.tree
        edges: dict[str, dict[str, Import]] = {module: {} for module in tree.modules}
        for imp in tree.imports:
            if internal(imp.target):
                used = tree.resolve(imp.target)
                if used != imp.module:
                    edges[imp.module].setdefault(used, imp)
        problems = []
        for cycle in cycles(edges):
            steps = [f'{tree.where(edges[a][b])} imports {b}' for a, b in zip(cycle, cycle[1:] + cycle[:1])]
            problems.append('; '.join(steps))
        self.report(problems, 'Modules import each other in a circle')


def cycles(edges: dict[str, dict[str, Import]]) -> list[list[str]]:
    """One circle of imports (as a list of modules) for each group of modules that import each other."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    groups: list[list[str]] = []

    def connect(module: str) -> None:  # Tarjan's strongly connected components
        index[module] = low[module] = len(index)
        stack.append(module)
        for used in edges[module]:
            if used not in index:
                connect(used)
                low[module] = min(low[module], low[used])
            elif used in stack:
                low[module] = min(low[module], index[used])
        if low[module] == index[module]:
            group = []
            while True:
                member = stack.pop()
                group.append(member)
                if member == module:
                    break
            if len(group) > 1:
                groups.append(group)

    for module in sorted(edges):
        if module not in index:
            connect(module)
    return [circle(edges, sorted(group)) for group in groups]


def circle(edges: dict[str, dict[str, Import]], group: list[str]) -> list[str]:
    """A path through a group of modules that import each other, from its first module back to it."""
    members, start = set(group), group[0]
    paths = {start: [start]}
    queue = [start]
    while queue:  # breadth first, so the circle shown is a shortest one
        module = queue.pop(0)
        for used in sorted(edges[module]):
            if used == start:
                return paths[module]
            if used in members and used not in paths:
                paths[used] = paths[module] + [used]
                queue.append(used)
    return group


if __name__ == '__main__':
    unittest.main(verbosity=2)
