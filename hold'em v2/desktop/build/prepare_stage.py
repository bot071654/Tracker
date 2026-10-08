"""Prepare everything a build bakes in, in desktop/build/stage/, and write the build metadata.

    python desktop/build/prepare_stage.py --build-number 42 [--api-url https://api.example.com]

* stage/config/config.json   the project's config WITHOUT this machine's calibration (regions, screen_size, result_boxes):
                             a calibration measured on one screen is wrong on every other
* stage/config/scenarios.json, scenario_engine.json   copied as they are (review scenarios.json before a release: it holds
                             whatever rules the developer's copy has saved)
* stage/desktop/build_info.json    build number, git commit, UTC time
* stage/desktop/api_defaults.json the production backend address for this build (public; never a credential)

Everything is written under stage/ and NOTHING into the source tree: the app looks for api_defaults.json next to its code, so a copy
left in the source tree would make every run from source (and the tests) talk to the production backend.
None of the generated files is committed (see desktop/build/.gitignore).
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from desktop import appconfig, paths  # noqa: E402


def git_commit():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=20)
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True, text=True, timeout=20)
        commit = out.stdout.strip() or "unknown"
        return commit + ("+dirty" if dirty.stdout.strip() else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def main(argv=None, stage_dir=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-number", default=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M"))
    parser.add_argument("--api-url", default="", help="production backend address baked into this build (https only)")
    args = parser.parse_args(argv)

    api_url = appconfig.normalize_base_url(args.api_url) if args.api_url else ""
    stage = stage_dir or os.path.join(HERE, "stage")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(os.path.join(stage, "config"))
    os.makedirs(os.path.join(stage, "desktop"))

    with open(os.path.join(ROOT, "config", "config.json"), "r", encoding="utf-8") as handle:
        config = json.load(handle)
    for key in paths.MACHINE_SPECIFIC_CONFIG:
        config.pop(key, None)
    with open(os.path.join(stage, "config", "config.json"), "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=4)
    for name in ("scenarios.json", "scenario_engine.json"):
        shutil.copy2(os.path.join(ROOT, "config", name), os.path.join(stage, "config", name))

    info = {"build": args.build_number, "commit": git_commit(),
            "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    with open(os.path.join(stage, "desktop", "build_info.json"), "w", encoding="utf-8") as handle:
        json.dump(info, handle, indent=2)
    with open(os.path.join(stage, "desktop", "api_defaults.json"), "w", encoding="utf-8") as handle:
        json.dump({"api_url": api_url}, handle, indent=2)
    print("Staged build %s (%s), backend %s" % (info["build"], info["commit"], api_url or "<none: the user is asked>"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
