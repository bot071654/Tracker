"""The packaged application's dependency surface, checked statically: what it needs, and that nothing dangerous is reachable."""

import importlib.util
import os
import sys

BUILD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "desktop", "build")


def _audit():
    spec = importlib.util.spec_from_file_location("audit_imports", os.path.join(BUILD, "audit_imports.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_imports"] = module
    spec.loader.exec_module(module)
    return module


def test_the_packaged_app_needs_exactly_the_pinned_runtime_packages():
    audit = _audit()
    _modules, external = audit.walk()
    required = {name for name, info in external.items() if info["plain"]}
    assert required == {"cv2", "numpy", "mss", "openpyxl", "pyttsx3", "certifi"}
    pinned = open(os.path.join(BUILD, "requirements-desktop.txt"), encoding="utf-8").read().lower()
    certifi_line = [line for line in pinned.splitlines() if line.startswith("certifi==")]
    assert certifi_line and "sys_platform" not in certifi_line[0], "certifi must be pinned for every platform"
    for package, requirement in (("cv2", "opencv-python"), ("numpy", "numpy"), ("mss", "mss"), ("openpyxl", "openpyxl"), ("pyttsx3", "pyttsx3"), ("certifi", "certifi")):
        assert requirement + "==" in pinned, "%s is needed but not pinned" % requirement


def test_no_automation_library_server_stack_or_dev_tool_is_imported_by_the_packaged_app():
    audit = _audit()
    _modules, external = audit.walk()
    assert not (set(external) & (audit.FORBIDDEN - {"psycopg", "psycopg2"}))      # the DB driver is allowed only as a guarded import
    assert external["psycopg"]["plain"] is False and external["dotenv"]["plain"] is False


def test_the_pinned_runtime_list_and_the_spec_exclude_what_must_not_ship():
    pinned = open(os.path.join(BUILD, "requirements-desktop.txt"), encoding="utf-8").read().lower()
    spec = open(os.path.join(BUILD, "PokerHandTracker.spec"), encoding="utf-8").read()
    for forbidden in ("psycopg", "dotenv", "pyautogui", "fastapi", "uvicorn"):
        assert forbidden + "==" not in pinned and forbidden + ">" not in pinned
        assert '"%s"' % forbidden in spec, "%s is not in the spec's excludes" % forbidden


def test_the_source_never_imports_an_input_automation_library():
    root = os.path.dirname(BUILD.rsplit(os.sep, 1)[0])
    offenders = []
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".venv", "node_modules", "extension", "__pycache__", ".git", "tests", "dist", "backups")]
        for name in names:
            if name.endswith(".py"):
                text = open(os.path.join(folder, name), encoding="utf-8", errors="replace").read()
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith(("import pyautogui", "from pyautogui", "import pynput", "from pynput", "import keyboard", "from keyboard")):
                        offenders.append("%s: %s" % (os.path.join(folder, name), stripped))
    assert offenders == []


def test_a_build_never_writes_defaults_into_the_source_tree(tmp_path):
    """Regression: api_defaults.json in the source tree made every source run (and the tests) believe it should use the production backend."""
    spec = importlib.util.spec_from_file_location("prepare_stage", os.path.join(BUILD, "prepare_stage.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source_desktop = os.path.join(module.ROOT, "desktop")
    before = sorted(os.listdir(source_desktop))
    stage = str(tmp_path / "stage")
    assert module.main(["--build-number", "7", "--api-url", "https://api.example.com"], stage_dir=stage) == 0
    assert sorted(os.listdir(source_desktop)) == before, "the build changed the source tree"
    assert not os.path.exists(os.path.join(source_desktop, "api_defaults.json"))
    assert os.path.exists(os.path.join(stage, "desktop", "api_defaults.json")) and os.path.exists(os.path.join(stage, "desktop", "build_info.json"))
    config = open(os.path.join(stage, "config", "config.json"), encoding="utf-8").read()
    assert '"regions"' not in config and '"screen_size"' not in config


def test_a_source_run_has_no_default_backend_and_so_stays_in_direct_database_mode():
    from desktop import appconfig
    assert not os.path.exists(os.path.join(os.path.dirname(BUILD), "api_defaults.json"))
    assert appconfig.remote_mode() is False or os.environ.get(appconfig.API_URL_ENV)
