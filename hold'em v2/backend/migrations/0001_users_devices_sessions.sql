-- 0001: accounts, installations (devices) and sign-in sessions.
--
-- The desktop app never talks to this database. Only the backend does, and it does so with its own
-- credentials. These tables are how the backend knows who a request comes from.

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         VARCHAR(254) NOT NULL,
    password_hash TEXT         NOT NULL,          -- scrypt$n$r$p$salt$hash, never the password
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_idx ON users (lower(email));

-- One row per installed copy of the app, issued by the backend when a user first signs in on it.
-- It is an opaque id, not a hardware fingerprint: nothing about the machine is read to make it.
CREATE TABLE IF NOT EXISTS devices (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         VARCHAR(80)  NOT NULL,
    platform     VARCHAR(20)  NOT NULL,
    architecture VARCHAR(20)  NOT NULL,
    app_version  VARCHAR(20)  NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS devices_user_idx ON devices (user_id);

-- A sign-in. Only a hash of the token is stored; the token itself exists only on the user's computer.
CREATE TABLE IF NOT EXISTS sessions (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash CHAR(64)    NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id);
