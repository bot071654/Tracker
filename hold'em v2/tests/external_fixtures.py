"""Fixtures that are deliberately NOT in the repository (whole-screen casino frames kept outside version control).

A test that needs one skips with a clear reason when the file is absent. A file that is present but cannot be read still FAILS,
so a damaged fixture is never hidden as a skip. Setting HIDE_ENV to 1 makes every external fixture count as absent (used by
test_external_fixture_skips.py to prove the skips; nothing is moved or touched).
"""
import os

import pytest

HIDE_ENV = "PHT_HIDE_EXTERNAL_FIXTURES"


def available(path):
    return os.environ.get(HIDE_ENV) != "1" and os.path.exists(path)


def skip_reason(path):
    return "external fixture not present (%s); whole-screen frames are kept out of the repository" % os.path.basename(path)


def load_frame(path):
    """The frame at `path` as read by OpenCV; skips the test when the file is absent, fails when it is present but unreadable."""
    import cv2

    if not available(path):
        pytest.skip(skip_reason(path))
    frame = cv2.imread(path)
    assert frame is not None, "external fixture is present but cannot be read: %s" % path
    return frame
