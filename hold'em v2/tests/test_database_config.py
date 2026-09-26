"""Connection settings must be stated, never assumed.

They used to default to "localhost", 5432, user "postgres" and a blank
password - which is exactly a stock local PostgreSQL install. A developer who
cloned the project without an .env got no error at all: they connected to
whatever PostgreSQL was on their own machine and recorded hands into a
database nobody else could see.

Since the team shares one server, a missing setting has to be an error that
names itself. These tests are about that, and about the one thing the
migration needed from db.insert_hand.
"""

import os

import pytest

from database import db


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """No POSTGRES_* in the environment, and no .env to fall back on."""
    for name in ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DATABASE",
                 "POSTGRES_USER", "POSTGRES_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    # connection_settings() loads ROOT/.env; point ROOT at an empty directory
    # so the real one cannot supply anything.
    monkeypatch.setattr(db, "ROOT", str(tmp_path))
    return monkeypatch


def configured(monkeypatch, **overrides):
    values = {"POSTGRES_HOST": "db.example", "POSTGRES_PORT": "5433",
              "POSTGRES_DATABASE": "poker_tracker",
              "POSTGRES_USER": "poker_tracker", "POSTGRES_PASSWORD": "secret"}
    values.update(overrides)
    for name, value in values.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


# -- nothing is assumed -------------------------------------------------------

def test_no_configuration_at_all_is_an_error(clean_env):
    with pytest.raises(db.ConfigurationError) as caught:
        db.connection_settings()
    message = str(caught.value)
    for name in db.REQUIRED_SETTINGS:
        assert name in message
    assert ".env" in message


@pytest.mark.parametrize("missing", list(db.REQUIRED_SETTINGS))
def test_each_setting_is_required_by_name(clean_env, missing):
    configured(clean_env, **{missing: None})
    with pytest.raises(db.ConfigurationError) as caught:
        db.connection_settings()
    assert missing in str(caught.value)


@pytest.mark.parametrize("missing", list(db.REQUIRED_SETTINGS))
def test_an_empty_value_counts_as_missing(clean_env, missing):
    """A blank password is the classic accidental local connection."""
    configured(clean_env, **{missing: "   "})
    with pytest.raises(db.ConfigurationError) as caught:
        db.connection_settings()
    assert missing in str(caught.value)


def test_there_is_no_localhost_or_5432_or_postgres_default(clean_env):
    """The three values that together are a stock install."""
    with pytest.raises(db.ConfigurationError):
        db.connection_settings()
    source = open(db.__file__, encoding="utf-8").read()
    assert 'os.getenv("POSTGRES_HOST", "localhost")' not in source
    assert 'os.getenv("POSTGRES_USER", "postgres")' not in source


def test_a_configuration_error_is_a_database_error():
    """So every existing caller already handles it."""
    assert issubclass(db.ConfigurationError, db.DatabaseError)


# -- what it returns when it is configured ------------------------------------

def test_settings_are_read_from_the_environment(clean_env):
    configured(clean_env)
    settings = db.connection_settings()
    assert settings == {"host": "db.example", "port": 5433,
                        "dbname": "poker_tracker", "user": "poker_tracker",
                        "password": "secret"}


def test_the_port_must_be_a_number(clean_env):
    configured(clean_env, POSTGRES_PORT="not-a-port")
    with pytest.raises(db.ConfigurationError) as caught:
        db.connection_settings()
    assert "POSTGRES_PORT" in str(caught.value)


def test_the_database_name_may_default(clean_env):
    """A name cannot on its own send the connection to another server."""
    configured(clean_env, POSTGRES_DATABASE=None)
    assert db.connection_settings()["dbname"] == db.DEFAULT_DATABASE == "poker_tracker"


def test_surrounding_whitespace_is_trimmed(clean_env):
    configured(clean_env, POSTGRES_HOST="  db.example  ")
    assert db.connection_settings()["host"] == "db.example"


# -- SSL mode: optional, and invisible when unset -----------------------------
#
# The local Docker container has never needed sslmode, so the whole point is
# that not setting POSTGRES_SSLMODE must be indistinguishable from before this
# existed - test_settings_are_read_from_the_environment above already proves
# that (its exact five-key dict has no room for a sixth key appearing by
# accident); these tests are about what happens once it IS set.

def test_no_sslmode_means_no_sslmode_key_at_all(clean_env):
    """Not merely "falsy" - the key must be genuinely absent.

    psycopg receives connection_settings() unpacked as **kwargs (see
    get_connection()), so an sslmode key present with any value, even one
    meant to mean "off", would still ask psycopg to negotiate SSL. Absence is
    the only way to reproduce today's local-Docker connection exactly.
    """
    configured(clean_env)
    assert "sslmode" not in db.connection_settings()


@pytest.mark.parametrize("mode", list(db.SSL_MODES))
def test_every_recognised_sslmode_is_passed_through(clean_env, mode):
    configured(clean_env, POSTGRES_SSLMODE=mode)
    assert db.connection_settings()["sslmode"] == mode


def test_sslmode_disable_for_local_docker():
    assert "disable" in db.SSL_MODES


def test_sslmode_require_for_a_cloud_server():
    assert "require" in db.SSL_MODES


def test_sslmode_is_trimmed_and_lowercased(clean_env):
    configured(clean_env, POSTGRES_SSLMODE="  REQUIRE  ")
    assert db.connection_settings()["sslmode"] == "require"


def test_an_unrecognised_sslmode_is_a_named_configuration_error(clean_env):
    configured(clean_env, POSTGRES_SSLMODE="yes-please")
    with pytest.raises(db.ConfigurationError) as caught:
        db.connection_settings()
    message = str(caught.value)
    assert "yes-please" in message
    assert "require" in message                # one of the valid options is named


def test_sslmode_does_not_disturb_the_other_settings(clean_env):
    configured(clean_env, POSTGRES_SSLMODE="require")
    settings = db.connection_settings()
    assert settings["host"] == "db.example"
    assert settings["port"] == 5433
    assert settings["dbname"] == "poker_tracker"
    assert settings["user"] == "poker_tracker"
    assert settings["password"] == "secret"
    assert settings["sslmode"] == "require"


def test_an_sslmode_configuration_error_does_not_expose_the_password(clean_env):
    configured(clean_env, POSTGRES_SSLMODE="yes-please",
               POSTGRES_PASSWORD="hunter2-very-secret")
    with pytest.raises(db.ConfigurationError) as caught:
        db.connection_settings()
    assert "hunter2" not in str(caught.value)


# -- the password never appears in what we print ------------------------------

def test_describe_target_never_includes_the_password(clean_env):
    configured(clean_env, POSTGRES_PASSWORD="hunter2-very-secret")
    described = db.describe_target()
    assert described == "poker_tracker@db.example:5433/poker_tracker"
    assert "hunter2" not in described


def test_a_connection_failure_names_the_target_but_not_the_password(clean_env):
    configured(clean_env, POSTGRES_HOST="127.0.0.1", POSTGRES_PORT="1",
               POSTGRES_PASSWORD="hunter2-very-secret")
    with pytest.raises(db.DatabaseError) as caught:
        db.get_connection()
    message = str(caught.value)
    assert "127.0.0.1:1" in message
    assert "hunter2" not in message


def test_a_connection_failure_with_sslmode_set_still_hides_the_password(clean_env):
    configured(clean_env, POSTGRES_HOST="127.0.0.1", POSTGRES_PORT="1",
               POSTGRES_PASSWORD="hunter2-very-secret", POSTGRES_SSLMODE="require")
    with pytest.raises(db.DatabaseError) as caught:
        db.get_connection()
    assert "hunter2" not in str(caught.value)


# -- importing a history needs recorded_at to be writable ---------------------

def test_insert_hand_writes_recorded_at_only_when_the_record_carries_one():
    """The tracker's records have none, so they keep CURRENT_TIMESTAMP."""
    source = open(db.__file__, encoding="utf-8").read()
    assert "recorded_at" not in db.COLUMNS
    assert 'if record.get("recorded_at") is not None:' in source
