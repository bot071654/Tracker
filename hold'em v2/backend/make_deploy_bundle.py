"""Assemble the minimal directory that is deployed to Railway:  python -m backend.make_deploy_bundle <target-dir>

Only what the API imports goes in (backend/, database/, the two poker/ modules that build a hand record, and the three
desktop/ modules database.db consults) - never the desktop app, the tests, the extension, logs or any .env. The Railway
start command (apply migrations, then serve) and the health check come from the railway.json written here, so the
deployment does not depend on dashboard settings.
"""

import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = [
    "backend/__init__.py", "backend/requirements.txt", "backend/migrate.py", "backend/manage.py", "backend/app/__init__.py", "backend/app/main.py",
    "backend/app/v1.py", "backend/app/security.py", "backend/app/settings.py",
    "database/__init__.py", "database/db.py", "database/schema.sql",
    "poker/__init__.py", "poker/hand_record.py", "poker/hand_evaluator.py",
    "desktop/__init__.py", "desktop/appconfig.py", "desktop/paths.py", "desktop/version.py",
]
START = "python -m backend.migrate && python -m uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT"


def build(target):
    shutil.rmtree(target, ignore_errors=True)
    for relative in FILES:
        destination = os.path.join(target, relative)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, relative), destination)
    migrations = os.path.join(ROOT, "backend", "migrations")
    shutil.copytree(migrations, os.path.join(target, "backend", "migrations"), ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(os.path.join(ROOT, "backend", "requirements.txt"), os.path.join(target, "requirements.txt"))
    with open(os.path.join(target, "railway.json"), "w", encoding="utf-8") as handle:
        json.dump({"$schema": "https://railway.com/railway.schema.json",
                   "deploy": {"startCommand": START, "healthcheckPath": "/api/v1/health", "healthcheckTimeout": 120,
                              "restartPolicyType": "ON_FAILURE", "restartPolicyMaxRetries": 5}}, handle, indent=2)
    return sorted(os.path.relpath(os.path.join(d, f), target).replace("\\", "/") for d, _, fs in os.walk(target) for f in fs)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    files = build(os.path.abspath(sys.argv[1]))
    print("Deploy bundle: %d files" % len(files))
    print("\n".join("  " + name for name in files))
