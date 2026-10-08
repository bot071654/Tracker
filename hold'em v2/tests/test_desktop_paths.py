"""Per-user data locations, first-run seeding and application identity (desktop/paths.py, desktop/version.py).

The most important property: running from source moves NOTHING. Every constant the tracker already used
(config/config.json, logs/tracker.log, recognition/templates, data/poker_hands.xlsx, scenarios, window state)
must still be the same path as before the packaging work.
"""

import json
import os
import sys

import pytest

from desktop import paths, version

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(paths.HOME_ENV, raising=False)


def test_from_source_every_constant_is_where_it_always_was():
    from config import settings
    from poker import scenario_engine, scenarios
    from ui import window_geometry

    assert not paths.is_frozen()
    assert settings.ROOT == PROJECT
    assert settings.RESOURCE_ROOT == PROJECT
    assert settings.CONFIG_PATH == os.path.join(PROJECT, "config", "config.json")
    assert settings.LOG_PATH == os.path.join(PROJECT, "logs", "tracker.log")
    assert settings.TEMPLATE_DIR == os.path.join(PROJECT, "recognition", "templates")
    assert settings.EXCEL_PATH == os.path.join(PROJECT, "data", "poker_hands.xlsx")
    assert scenarios.SCENARIOS_PATH == os.path.join(PROJECT, "config", "scenarios.json")
    assert scenario_engine.ENGINE_CONFIG_PATH == os.path.join(PROJECT, "config", "scenario_engine.json")
    assert window_geometry.STATE_PATH == os.path.join(PROJECT, "config", "window_state.json")


def test_seeding_is_a_noop_when_the_home_is_the_project_itself(tmp_path):
    assert paths.seed_user_files(bundled=str(tmp_path), home=str(tmp_path)) == {
        "templates": 0, "config": False, "scenarios": False, "engine": False}


@pytest.mark.parametrize("platform_name, expected_home", [
    ("win32", os.path.join("LocalApp", "PokerHandTracker")),
    ("darwin", os.path.join("Library", "Application Support", "PokerHandTracker")),
])
def test_packaged_data_goes_to_the_per_user_folder_not_beside_the_executable(monkeypatch, tmp_path, platform_name, expected_home):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr(sys, "platform", platform_name)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalApp"))
    monkeypatch.setattr(paths, "_user_home", lambda: str(tmp_path))

    home = paths.app_home()
    assert home.endswith(expected_home) or home == os.path.join(str(tmp_path), expected_home) or expected_home in home
    assert paths.resource_root() == str(tmp_path / "bundle")
    assert not home.startswith(str(tmp_path / "bundle")), "user data must never live inside the bundle"
    for where in (paths.config_dir(), paths.data_dir(), paths.template_dir(), paths.debug_dir(), paths.state_dir()):
        assert where.startswith(home)
    if platform_name == "darwin":
        assert paths.log_dir() == os.path.join(str(tmp_path), "Library", "Logs", "PokerHandTracker")
        assert paths.cache_dir() == os.path.join(str(tmp_path), "Library", "Caches", "PokerHandTracker")
    else:
        assert paths.log_dir() == os.path.join(home, "logs")
    # configuration, logs, cache and state are separate folders
    assert len({paths.config_dir(), paths.log_dir(), paths.cache_dir(), paths.state_dir(), paths.data_dir()}) == 5


def test_home_override_wins_in_every_mode(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path / "portable"))
    assert paths.app_home() == str(tmp_path / "portable")
    assert paths.config_dir() == str(tmp_path / "portable" / "config")
    assert paths.state_dir() == str(tmp_path / "portable" / "state")


def _fake_bundle(root):
    (root / "config").mkdir(parents=True)
    (root / "recognition" / "templates" / "ranks").mkdir(parents=True)
    (root / "recognition" / "templates" / "suits").mkdir(parents=True)
    (root / "recognition" / "templates" / "ranks" / "A_1.png").write_bytes(b"png-a")
    (root / "recognition" / "templates" / "suits" / "S_1.png").write_bytes(b"png-s")
    (root / "config" / "config.json").write_text(json.dumps({
        "regions": {"dealer_1": [1, 2, 3, 4]}, "screen_size": [1920, 1080], "result_boxes": {"x": [1, 1, 1, 1]},
        "confidence_threshold": 0.62, "monitor": 1}))
    (root / "config" / "scenarios.json").write_text('{"rules": []}')
    (root / "config" / "scenario_engine.json").write_text('{"high_card_source": "player"}')


def test_first_run_seeds_defaults_without_this_machines_calibration(tmp_path):
    bundle, home = tmp_path / "bundle", tmp_path / "home"
    _fake_bundle(bundle)
    done = paths.seed_user_files(bundled=str(bundle), home=str(home))
    assert done == {"templates": 2, "config": True, "scenarios": True, "engine": True}
    config = json.loads((home / "config" / "config.json").read_text())
    assert config == {"confidence_threshold": 0.62, "monitor": 1}        # thresholds kept, calibration stripped
    assert (home / "recognition" / "templates" / "ranks" / "A_1.png").read_bytes() == b"png-a"
    for folder in ("data", "logs", "debug", "state", "cache"):
        assert (home / folder).is_dir()


def test_seeding_never_overwrites_the_users_own_files(tmp_path):
    bundle, home = tmp_path / "bundle", tmp_path / "home"
    _fake_bundle(bundle)
    paths.seed_user_files(bundled=str(bundle), home=str(home))
    (home / "config" / "config.json").write_text('{"regions": {"dealer_1": [9, 9, 9, 9]}}')
    (home / "recognition" / "templates" / "ranks" / "A_1.png").write_bytes(b"taught-by-the-user")
    (home / "config" / "scenarios.json").write_text('{"rules": ["mine"]}')
    again = paths.seed_user_files(bundled=str(bundle), home=str(home))
    assert again == {"templates": 0, "config": False, "scenarios": False, "engine": False}
    assert json.loads((home / "config" / "config.json").read_text()) == {"regions": {"dealer_1": [9, 9, 9, 9]}}
    assert (home / "recognition" / "templates" / "ranks" / "A_1.png").read_bytes() == b"taught-by-the-user"
    assert (home / "config" / "scenarios.json").read_text() == '{"rules": ["mine"]}'


def test_a_new_shipped_template_is_added_on_upgrade_but_taught_ones_stay(tmp_path):
    bundle, home = tmp_path / "bundle", tmp_path / "home"
    _fake_bundle(bundle)
    paths.seed_user_files(bundled=str(bundle), home=str(home))
    (bundle / "recognition" / "templates" / "ranks" / "K_1.png").write_bytes(b"new")
    assert paths.seed_user_files(bundled=str(bundle), home=str(home))["templates"] == 1


def test_version_identity_is_complete():
    info = version.as_dict()
    assert info["name"] == "Poker Hand Tracker" and info["version"] == version.VERSION
    assert info["platform"] in ("Windows", "macOS", "Linux") and info["architecture"] in ("x64", "arm64") or info["architecture"]
    line = version.describe()
    assert version.VERSION in line and info["platform"] in line and info["architecture"] in line


@pytest.mark.parametrize("machine, expected", [("AMD64", "x64"), ("x86_64", "x64"), ("arm64", "arm64"), ("aarch64", "arm64")])
def test_architecture_is_normalised(monkeypatch, machine, expected):
    monkeypatch.setattr(version.platform, "machine", lambda: machine)
    assert version.architecture() == expected


def test_build_info_file_sets_build_and_commit(monkeypatch, tmp_path):
    (tmp_path / "build_info.json").write_text(json.dumps({"build": "123", "commit": "abc1234"}))
    monkeypatch.setattr(version, "_HERE", str(tmp_path))
    assert version.build_number() == "123" and version.commit() == "abc1234"
    assert "(build 123)" in version.describe()
