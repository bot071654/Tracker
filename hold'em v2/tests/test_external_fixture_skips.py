"""Tests that need a fixture kept outside the repository skip with a clear reason when it is absent; they never error, and a damaged fixture still fails.

The whole-screen frames in tests/fixtures/dealer_fusion/ are not committed. Nothing here reads, copies, moves or changes those files:
the helper's existence check is metadata only, and the module-level run hides them by environment variable before anything is read.
"""
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import external_fixtures as ext                                 # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = ["tests/test_dealer_fusion_fallback.py", "tests/test_paper_white_diagnostics.py", "tests/test_fusion_refusal_record.py",
           "tests/test_read_diagnostics.py"]


def skipped(call, *args):
    with pytest.raises(pytest.skip.Exception) as caught:
        call(*args)
    return str(caught.value)


# -- the helper ------------------------------------------------------------------------------------------------------------------------

def test_an_absent_fixture_skips_with_a_reason_that_names_the_file(tmp_path):
    missing = str(tmp_path / "round45_result-panel_t391.6.png")
    reason = skipped(ext.load_frame, missing)
    assert "round45_result-panel_t391.6.png" in reason and "not present" in reason and "out of the repository" in reason
    assert not ext.available(missing)


def test_a_present_readable_fixture_is_loaded_unchanged(tmp_path):
    path = str(tmp_path / "frame.png")
    picture = np.arange(4 * 5 * 3, dtype=np.uint8).reshape(4, 5, 3)
    assert cv2.imwrite(path, picture)
    assert ext.available(path)
    assert np.array_equal(ext.load_frame(path), picture)


def test_a_present_but_unreadable_fixture_fails_it_is_never_hidden_as_a_skip(tmp_path):
    path = tmp_path / "frame.png"
    path.write_bytes(b"this is not a picture")
    try:
        ext.load_frame(str(path))
    except pytest.skip.Exception:
        pytest.fail("a damaged fixture was skipped instead of failing")
    except AssertionError as failure:
        assert "cannot be read" in str(failure)
    else:
        pytest.fail("a damaged fixture was accepted")


def test_hiding_makes_a_present_fixture_count_as_absent_without_reading_it(tmp_path, monkeypatch):
    path = str(tmp_path / "frame.png")
    assert cv2.imwrite(path, np.zeros((3, 3, 3), np.uint8))
    monkeypatch.setenv(ext.HIDE_ENV, "1")
    assert not ext.available(path)
    monkeypatch.setattr(cv2, "imread", lambda *a, **k: pytest.fail("a hidden fixture must not be read"))
    assert "not present" in skipped(ext.load_frame, path)
    monkeypatch.delenv(ext.HIDE_ENV)
    assert ext.available(path)


@pytest.mark.parametrize("value", ["", "0", "yes"])
def test_only_the_value_one_hides(tmp_path, monkeypatch, value):
    path = str(tmp_path / "frame.png")
    assert cv2.imwrite(path, np.zeros((3, 3, 3), np.uint8))
    monkeypatch.setenv(ext.HIDE_ENV, value)
    assert ext.available(path)


# -- the real modules, with the real fixtures hidden ----------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def hidden_run(tmp_path_factory):
    """The four dependent modules run in a separate process with the external fixtures hidden and every data folder in a temporary place."""
    work = tmp_path_factory.mktemp("hidden")
    report = work / "report.xml"
    env = dict(os.environ, **{ext.HIDE_ENV: "1", "POKER_TRACKER_HOME": str(work / "home")})
    done = subprocess.run([sys.executable, "-B", "-m", "pytest", *MODULES, "-q", "-p", "no:cacheprovider", "-k", "not replay",
                           "--basetemp", str(work / "base"), "--junitxml", str(report)],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=1500)
    cases = [(case.get("classname", "") + "::" + case.get("name", ""), case) for case in ET.parse(str(report)).getroot().iter("testcase")]
    return done, cases


def outcome(case):
    for tag in ("error", "failure", "skipped"):
        found = case.find(tag)
        if found is not None:
            return tag, found.get("message", "")
    return "passed", ""


def test_without_the_external_fixtures_nothing_errors_or_fails(hidden_run):
    done, cases = hidden_run
    bad = [(name, outcome(case)) for name, case in cases if outcome(case)[0] in ("error", "failure")]
    assert not bad, bad[:5]
    assert done.returncode == 0, done.stdout[-1500:]


def test_the_dependent_tests_are_skipped_with_the_external_fixture_reason_and_are_many(hidden_run):
    _, cases = hidden_run
    skips = [(name, outcome(case)[1]) for name, case in cases if outcome(case)[0] == "skipped"]
    fixture_skips = [name for name, reason in skips if "external fixture not present" in reason]
    assert len(fixture_skips) >= 60, len(fixture_skips)
    for module in ("test_dealer_fusion_fallback", "test_paper_white_diagnostics", "test_fusion_refusal_record", "test_read_diagnostics"):
        assert any(module in name for name in fixture_skips), module
    assert all("round45" in reason or "round0" in reason or "no casino screenshot" in reason or "external fixture" in reason
               for _, reason in skips), [s for s in skips if "external fixture" not in s[1]][:5]


def test_the_tests_that_need_no_external_fixture_still_run_and_pass(hidden_run):
    _, cases = hidden_run
    passed = [name for name, case in cases if outcome(case)[0] == "passed"]
    assert len(passed) >= 20, len(passed)
    for module in ("test_paper_white_diagnostics", "test_read_diagnostics"):
        assert any(module in name for name in passed), module
