"""A private home for the test process: XDG cache, config and data folders of its own, removed when
it exits, so no test reads or writes the user's settings, transcript or history; and NowCaption's
log kept quiet, as the tests inject failures on purpose.

Every test module imports it. It must come before GTK starts, which reads these folders once, the
first time it needs them: support.gtk imports it first. The rest of NowCaption reads them only
when it uses them.
"""
import atexit
import logging
import os
import shutil
import tempfile

HOME = tempfile.mkdtemp(prefix='nowcaption-tests-')
atexit.register(shutil.rmtree, HOME, True)
for _name, _folder in (('XDG_CACHE_HOME', 'cache'), ('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data')):
    os.environ[_name] = os.path.join(HOME, _folder)
    os.mkdir(os.environ[_name])

logging.getLogger('nowcaption').setLevel(logging.CRITICAL)
