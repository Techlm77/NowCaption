#!/usr/bin/env python3
"""The release workflows (1.1.16) check the tag against pyproject.toml, and either may create
the release first. Their steps' scripts run with sh and a fake gh. Needs PyYAML.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import support.sandbox  # noqa: F401 - private XDG folders and a quiet log for this process
from support.fakes import executable

try:
    import yaml
except ImportError:
    yaml = None

ROOT = Path(__file__).resolve().parents[2]  # the repository


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

    def test_a_model_release_hands_latest_back_to_the_newest_app_release(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/keep-latest.yml').read_text())
        on = workflow.get('on', workflow.get(True))  # YAML 1.1 reads a bare on as true
        self.assertEqual(on, {'release': {'types': ['published']}})
        [job] = workflow['jobs'].values()
        self.assertIn("!startsWith(github.event.release.tag_name, 'v')", job['if'])  # never for app releases
        [step] = job['steps']
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        tags = 'models-v3\nv1.1.16\nmodels-v2\nv1.1.9\nv1.1.10\n'
        executable(root / 'bin/gh', '#!/bin/sh\necho "$*" >> "$GH_LOG"\n'
                   f'case "$*" in "release list"*) printf \'{tags}\' ;; esac\n')
        env = dict(os.environ, PATH=f'{root / "bin"}:{os.environ["PATH"]}', GH_LOG=str(root / 'gh.log'))
        result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step['run']], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        log = (root / 'gh.log').read_text().splitlines()
        self.assertEqual(log[-1], 'release edit v1.1.16 --latest')  # by version, not by list order


if __name__ == '__main__':
    unittest.main(verbosity=2)
