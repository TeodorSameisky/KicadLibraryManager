"""The scripts that talk to KiCad, run against a pretend KiCad.

They are the part of the app that cannot be exercised from Python at all, and
the part that broke most often in real use. Skipped where Node is not
installed: the CI image and a deployment have none, and a developer's machine
usually has.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

HARNESS = Path(__file__).parent / "js" / "bridge_tests.js"

NAMES = [
    "answers-new-session",
    "sends-with-the-session",
    "error-reply-rejects",
    "no-session-refuses",
    "waits-then-gives-up",
    "later-page-reuses-session",
    "new-session-replaces",
    "storage-refused",
    "junk-in-storage-is-ignored",
    "place-sends-in-order",
    "place-warns-before-sending",
    "place-stops-on-error",
    "place-needs-kicad",
    "in-kicad-detection",
]


@pytest.fixture(scope="module")
def results():
    done = subprocess.run(
        ["node", str(HARNESS)], capture_output=True, text=True, timeout=60, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_every_scenario_is_accounted_for(results):
    """So a test added to the script cannot be left out of this file."""
    assert sorted(results) == sorted(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_scenario(results, name):
    outcome = results[name]
    assert outcome["ok"], outcome.get("error")
