"""The nuh test suite.

The package import runs before conftest and before any test module imports
nuh, so the stray-write guard starts and HOME moves to an empty directory
here (tests/write_guard.py).
"""

import os

from tests import write_guard
from tests.hook_env import fake_home

write_guard.install([os.path.dirname(os.path.dirname(os.path.abspath(__file__))), fake_home()])
write_guard.isolate_home(fake_home())
