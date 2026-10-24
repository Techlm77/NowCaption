#!/usr/bin/env python3
"""1.1.16 packaging fixes.

- AppRun keeps a user's PYTHONPATH and PYTHONHOME away from the bundled Python, and saves the
  system's value of every variable it changes; appimage/gio-launch-desktop, which GIO starts
  programs through, gives them back.
- launch.py gives programs NowCaption starts the LD_PRELOAD it started with.
- The release workflows check the tag against pyproject.toml, and either may create the
  release first.

AppRun, gio-launch-desktop and the workflow steps run for real with fake programs. Real GIO
opens a link with a handler for a made-up URI scheme, so no browser starts. Needs PyGObject's
Gio for that test, PyYAML for the workflow tests, and no display.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from nowcaption import launch

ROOT = Path(__file__).resolve().parents[1]
SHELL_VARS = {'PWD', 'OLDPWD', 'SHLVL', '_'}

try:
    import yaml
except ImportError:
    yaml = None


def executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def read_env(path):
    """The environment a fake program saved with env -0."""
    return dict(item.split('=', 1) for item in path.read_text().split('\0') if item)


def wait_for(path):
    for _ in range(100):
        if path.exists():
            return path
        time.sleep(0.1)
    raise AssertionError(f'{path} was not written')


class AppRunTests(unittest.TestCase):
    """AppRun and gio-launch-desktop from the repository in a fake AppDir, whose python3 and
    glibc loader save the environment they were started with."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()  # AppRun finds itself with readlink -f
        self.addCleanup(shutil.rmtree, self.root)
        self.here = self.root / 'AppDir'
        (self.here / 'usr/bin').mkdir(parents=True)
        shutil.copy(ROOT / 'appimage/AppRun', self.here / 'AppRun')
        shutil.copy(ROOT / 'appimage/gio-launch-desktop', self.here / 'usr/bin/gio-launch-desktop')
        fake = '#!/bin/sh\nprintf "%s\\n" "$0" "$@" > "$FAKE_OUT.argv"\nexec env -0 > "$FAKE_OUT"\n'
        executable(self.here / 'usr/bin/python3', fake)
        executable(self.here / 'usr/lib/compat/ld-linux-x86-64.so.2', fake)
        cache = self.here / 'usr/lib/gdk-pixbuf-2.0/loaders.cache.in'
        cache.parent.mkdir(parents=True)
        cache.write_text('"@APPDIR@/usr/lib/gdk-pixbuf-2.0/loaders/libpixbufloader-svg.so"\n')
        self.out = self.root / 'env'
        self.host = {'PATH': os.environ['PATH'], 'HOME': str(self.root), 'FAKE_OUT': str(self.out),
                     'XDG_CACHE_HOME': str(self.root / 'cache'), 'PYTHONPATH': '/host/python',
                     'XDG_DATA_DIRS': '/host/share', 'GIO_MODULE_DIR': '/host/gio', 'PYTHONHOME': ''}

    def run_apprun(self, glibc):
        (self.here / 'usr/lib/compat/GLIBC_VERSION').write_text(glibc + '\n')
        subprocess.run(['sh', str(self.here / 'AppRun'), '--flag'], env=self.host, check=True,
                       timeout=10)
        return read_env(self.out), self.out.with_suffix('.argv').read_text().splitlines()

    def check_round_trip(self, env):
        """Each variable AppRun changed is saved, and a program GIO starts through
        gio-launch-desktop gets exactly the system's environment back."""
        names = env['NOWCAPTION_HOST_VARS'].split()
        changed = {k for k in set(env) | set(self.host) if env.get(k) != self.host.get(k)}
        changed -= SHELL_VARS | {k for k in changed if k.startswith('NOWCAPTION_HOST_')}
        self.assertIn('GIO_MODULE_DIR', changed)
        self.assertLessEqual(changed, set(names))
        for name in names:
            self.assertEqual(env.get('NOWCAPTION_HOST_' + name), self.host.get(name), name)
        self.assertEqual(env['GIO_LAUNCH_DESKTOP'], str(self.here / 'usr/bin/gio-launch-desktop'))
        child_out = self.root / 'child-env'
        subprocess.run([env['GIO_LAUNCH_DESKTOP'], 'sh', '-c', 'env -0 > "$0"', str(child_out)],
                       env=env, check=True, timeout=10)
        child = read_env(child_out)
        self.assertTrue(child.pop('GIO_LAUNCHED_DESKTOP_FILE_PID').isdigit())
        strip = lambda e: {k: v for k, v in e.items() if k not in SHELL_VARS}  # noqa: E731
        self.assertEqual(strip(child), strip(self.host))

    def test_system_glibc_drops_the_users_python_variables_and_gives_them_back(self):
        env, argv = self.run_apprun('0.1')  # older than any system's: the system's glibc
        self.assertEqual(argv, [str(self.here / 'usr/bin/python3'),
                                str(self.here / 'usr/bin/nowcaption-appimage'), '--flag'])
        self.assertNotIn('PYTHONPATH', env)
        self.assertNotIn('PYTHONHOME', env)
        self.assertEqual(env['GIO_MODULE_DIR'], str(self.here / 'usr/lib/gio/modules'))
        self.assertEqual(env['NOWCAPTION_HOST_PYTHONHOME'], '')  # set, though empty
        self.assertNotIn('NOWCAPTION_HOST_GI_TYPELIB_PATH', env)  # the system has none
        self.check_round_trip(env)

    def test_bundled_glibc_sets_its_own_python_home_and_gives_the_systems_back(self):
        self.host['PYTHONHOME'] = '/host/home'
        env, argv = self.run_apprun('999')  # newer than any system's: the bundled glibc
        self.assertEqual(argv[0], str(self.here / 'usr/lib/compat/ld-linux-x86-64.so.2'))
        self.assertEqual(env['PYTHONHOME'], str(self.here / 'usr'))
        self.assertNotIn('PYTHONPATH', env)
        self.check_round_trip(env)

    def test_gio_starts_programs_through_gio_launch_desktop(self):
        """Real GIO, as when GTK opens a link without a desktop portal."""
        try:
            from gi.repository import Gio  # noqa: F401
        except ImportError:
            self.skipTest('needs PyGObject')
        out = self.root / 'opened-env'
        executable(self.root / 'record', f'#!/bin/sh\nenv -0 > "{out}.part" && mv "{out}.part" "{out}"\n')
        apps = self.root / 'data/applications'
        apps.mkdir(parents=True)
        (apps / 'nowcaption-test-handler.desktop').write_text(textwrap.dedent(f"""\
            [Desktop Entry]
            Type=Application
            Name=NowCaption test handler
            Exec={self.root / 'record'} %u
            MimeType=x-scheme-handler/x-nowcaption-test;
            """))
        (self.root / 'config').mkdir()
        (self.root / 'config/mimeapps.list').write_text(
            '[Default Applications]\nx-scheme-handler/x-nowcaption-test=nowcaption-test-handler.desktop\n')
        env = {'PATH': os.environ['PATH'], 'HOME': str(self.root), 'XDG_DATA_HOME': str(self.root / 'data'),
               'XDG_CONFIG_HOME': str(self.root / 'config'), 'XDG_DATA_DIRS': str(self.root / 'none'),
               'GIO_MODULE_DIR': str(self.root / 'bundled-gio'), 'GSK_RENDERER': 'cairo',
               'GIO_LAUNCH_DESKTOP': str(self.here / 'usr/bin/gio-launch-desktop'),
               'NOWCAPTION_HOST_VARS': 'GIO_MODULE_DIR GSK_RENDERER\n XDG_DATA_DIRS GIO_LAUNCH_DESKTOP',
               'NOWCAPTION_HOST_XDG_DATA_DIRS': '/host/share'}
        script = 'from gi.repository import Gio\nGio.AppInfo.launch_default_for_uri("x-nowcaption-test:hello", None)\n'
        subprocess.run([sys.executable, '-c', script], env=env, check=True, timeout=20)
        child = read_env(wait_for(out))
        self.assertEqual(child['XDG_DATA_DIRS'], '/host/share')
        for name in ('GIO_MODULE_DIR', 'GSK_RENDERER', 'GIO_LAUNCH_DESKTOP', 'NOWCAPTION_HOST_VARS',
                     'NOWCAPTION_HOST_XDG_DATA_DIRS'):
            self.assertNotIn(name, child)
        self.assertIn('GIO_LAUNCHED_DESKTOP_FILE_PID', child)


class LaunchTests(unittest.TestCase):
    def test_restart_remembers_the_ld_preload_it_started_with(self):
        env = {'WAYLAND_DISPLAY': 'wayland-0', 'LD_PRELOAD': 'libhost.so'}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch('ctypes.util.find_library', return_value='/lib/libgtk4-layer-shell.so.0'), \
                mock.patch('os.execve', side_effect=SystemExit) as execve:
            with self.assertRaises(SystemExit):
                launch.preload_layer_shell()
        restarted = execve.call_args[0][2]
        self.assertEqual(restarted['LD_PRELOAD'], '/lib/libgtk4-layer-shell.so.0 libhost.so')
        self.assertEqual(restarted['NOWCAPTION_HOST_LD_PRELOAD'], 'libhost.so')
        self.assertEqual(restarted['NOWCAPTION_REEXEC'], '1')

    def test_after_the_restart_programs_get_the_original_ld_preload(self):
        for host, expected in (('libhost.so', 'libhost.so'), ('', None)):
            env = {'WAYLAND_DISPLAY': 'wayland-0', 'NOWCAPTION_REEXEC': '1', 'NOWCAPTION_HOST_LD_PRELOAD': host,
                   'LD_PRELOAD': '/lib/libgtk4-layer-shell.so.0 ' + host}
            with mock.patch.dict(os.environ, env, clear=True), mock.patch('os.execve') as execve:
                launch.preload_layer_shell()
                self.assertEqual(os.environ.get('LD_PRELOAD'), expected)
                self.assertNotIn('NOWCAPTION_REEXEC', os.environ)
                self.assertNotIn('NOWCAPTION_HOST_LD_PRELOAD', os.environ)
            execve.assert_not_called()


@unittest.skipIf(yaml is None, 'needs PyYAML')
class ReleaseWorkflowTests(unittest.TestCase):
    """The workflow steps' scripts, run with sh and a fake gh."""

    def steps(self, workflow):
        jobs = yaml.safe_load((ROOT / '.github/workflows' / workflow).read_text())['jobs']
        return jobs, [step for job in jobs.values() for step in job['steps']]

    def run_step(self, script, env):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        # The release does not exist yet, and creating it fails: the other workflow just did.
        executable(root / 'bin/gh', '#!/bin/sh\necho "$*" >> "$GH_LOG"\n'
                   'case "$*" in "release view"*) [ -e "$GH_RELEASE" ] ;;\n'
                   '"release create"*) touch "$GH_RELEASE"; exit 1 ;; esac\n')
        env = dict(env, PATH=f'{root / "bin"}:{os.environ["PATH"]}', GH_LOG=str(root / 'gh.log'),
                   GH_RELEASE=str(root / 'release'))
        result = subprocess.run(['sh', '-e', '-c', script], cwd=ROOT, env=env, capture_output=True,
                                text=True, timeout=10)
        log = (root / 'gh.log').read_text().splitlines() if (root / 'gh.log').exists() else []
        return result, log

    def test_both_workflows_fail_early_on_a_tag_that_is_not_the_version(self):
        version = next(line.split('"')[1] for line in (ROOT / 'pyproject.toml').read_text().splitlines()
                       if line.startswith('version = '))
        for workflow, name in (('flatpak-repo.yml', 'GITHUB_REF_NAME'), ('appimage.yml', 'TAG')):
            jobs, steps = self.steps(workflow)
            check = next(s for s in steps if s.get('name') == 'Check the tag matches the version')
            build = jobs['build']['steps']
            first_build = next(i for i, s in enumerate(build) if 'build' in (s.get('run', '') + s.get('name', '')).lower())
            self.assertLess(build.index(check), first_build, workflow)
            for tag, ok in (('v' + version, True), ('v0.0.1', False), (version, False)):
                result, _ = self.run_step(check['run'], {name: tag})
                self.assertEqual(result.returncode == 0, ok, (workflow, tag, result.stdout))

    def test_either_workflow_may_create_the_release_first(self):
        for workflow, files in (('flatpak-repo.yml', 'NowCaption-*.flatpak'),
                                ('appimage.yml', 'NowCaption-*.AppImage')):
            _, steps = self.steps(workflow)
            script = next(s['run'] for s in steps if 'gh release create' in s.get('run', ''))
            result, log = self.run_step(script, {'TAG': 'v9.9.9'})
            self.assertEqual(result.returncode, 0, (workflow, result.stderr))
            self.assertTrue(log[-1].startswith('release upload v9.9.9 ' + files), log)

    def test_containers_with_the_signing_key_are_pinned(self):
        jobs, _ = self.steps('flatpak-repo.yml')
        images = [job['container']['image'] for job in jobs.values() if 'container' in job]
        self.assertEqual(len(images), 2)
        for image in images:
            self.assertRegex(image, r'@sha256:[0-9a-f]{64}$')


if __name__ == '__main__':
    unittest.main(verbosity=2)
