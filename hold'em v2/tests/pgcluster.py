"""Test support: a throwaway PostgreSQL cluster and a real API server process.

Nothing here touches any existing database. The cluster is initialised in a temporary folder with the PostgreSQL
binaries already installed on the machine (set PG_BIN to their folder if they are not found), listens only on
127.0.0.1 on a free port, and is deleted afterwards. The API server is the real FastAPI app in its own process,
pointed at that cluster. Tests that need these skip cleanly when PostgreSQL binaries are not installed.

The project's real .env is never read: db.ROOT is pointed at an empty folder and every POSTGRES_* variable is set.
"""

import glob
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pytest

from database import db

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = ".exe" if sys.platform == "win32" else ""


def find_pg_bin():
    candidates = [os.environ.get("PG_BIN")]
    for pattern in (r"C:\Program Files\PostgreSQL\*\bin", "/usr/lib/postgresql/*/bin",
                    "/opt/homebrew/opt/postgresql@*/bin", "/usr/local/opt/postgresql@*/bin", "/Applications/Postgres.app/Contents/Versions/*/bin"):
        candidates += sorted(glob.glob(pattern), reverse=True)
    for folder in candidates:
        if folder and os.path.exists(os.path.join(folder, "initdb" + EXE)):
            return folder
    return None


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Cluster:
    def __init__(self, pg_bin):
        self.bin = pg_bin
        self.root = tempfile.mkdtemp(prefix="pgtest-")
        self.data = os.path.join(self.root, "data")
        self.port = free_port()
        self.user, self.dbname = "pgtest", "poker_tracker_test"

    def _run(self, name, *args, **kwargs):
        # Output goes to a file, not a pipe: pg_ctl starts postgres, which inherits the handles, and a pipe
        # would never reach end-of-file while the server runs.
        out = os.path.join(self.root, "%s.out" % name)
        with open(out, "ab") as sink:
            return subprocess.run([os.path.join(self.bin, name + EXE), *args], check=True, stdin=subprocess.DEVNULL,
                                  stdout=sink, stderr=sink, timeout=120, **kwargs)

    def start(self):
        self._run("initdb", "-D", self.data, "-U", self.user, "-A", "trust", "-E", "UTF8", "--no-locale")
        log = os.path.join(self.root, "pg.log")
        self._run("pg_ctl", "-D", self.data, "-o", "-p %d -c listen_addresses=127.0.0.1 -c fsync=off" % self.port,
                  "-l", log, "-w", "start")
        self._run("psql", "-h", "127.0.0.1", "-p", str(self.port), "-U", self.user, "-d", "postgres",
                  "-c", "CREATE DATABASE %s" % self.dbname)

    def stop(self):
        try:
            self._run("pg_ctl", "-D", self.data, "-m", "immediate", "stop")
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self.root, ignore_errors=True)

    def env(self):
        return {"POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": str(self.port), "POSTGRES_DATABASE": self.dbname,
                "POSTGRES_USER": self.user, "POSTGRES_PASSWORD": "unused-trust-auth", "POSTGRES_SSLMODE": "disable"}


@pytest.fixture(scope="session")
def pg_cluster_process():
    """One throwaway cluster for the whole run. It changes nothing in this process: see pg_cluster for that."""
    pg_bin = find_pg_bin()
    if pg_bin is None:
        pytest.skip("PostgreSQL binaries not found (set PG_BIN)")
    cluster = Cluster(pg_bin)
    try:
        cluster.start()
        yield cluster
    finally:
        cluster.stop()


@pytest.fixture(scope="module")
def pg_cluster(pg_cluster_process):
    """Point this process at the throwaway cluster for the length of ONE test module, then put everything back.

    The POSTGRES_* variables must not outlive the module: tests of other modules (test_database_config) assert on the
    settings they read from a clean environment.
    """
    empty_root = tempfile.mkdtemp(prefix="no-dotenv-")
    patch = pytest.MonkeyPatch()
    try:
        for key, value in pg_cluster_process.env().items():
            patch.setenv(key, value)
        patch.setattr(db, "ROOT", empty_root)            # the real .env must never be read
        yield pg_cluster_process
    finally:
        patch.undo()
        shutil.rmtree(empty_root, ignore_errors=True)


class ApiServer:
    """The real backend in its own process (it marks itself POKER_TRACKER_BACKEND=1, so it must not share ours)."""

    def __init__(self, cluster, extra_env=None):
        self.cluster, self.extra_env = cluster, extra_env or {}
        self.port = free_port()
        self.process = None
        self.log_path = os.path.join(cluster.root, "api-%d.log" % self.port)

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.port

    def start(self):
        env = dict(os.environ)
        env.update(self.cluster.env())
        env.update(self.extra_env)
        env["PYTHONPATH"] = PROJECT
        with open(self.log_path, "ab") as log:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", str(self.port),
                 "--log-level", "warning"], cwd=PROJECT, env=env, stdout=log, stderr=log)
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(self.url + "/api/v1/health", timeout=2):
                    return self
            except (urllib.error.URLError, OSError):
                if self.process.poll() is not None:
                    raise RuntimeError("API server exited: %s" % open(self.log_path, errors="replace").read()[-2000:])
                time.sleep(0.3)
        raise RuntimeError("API server did not start")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
