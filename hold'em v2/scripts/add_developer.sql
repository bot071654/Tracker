-- Give one teammate their own login to the shared database.
--
-- Run it on the database host, once per person:
--
--     docker exec -it poker_tracker_postgres \
--         psql -U poker_tracker -d poker_tracker
--
-- then paste the statements below, having replaced the name and the password.
--
-- Give each person their OWN role. Sharing the poker_tracker login means you
-- cannot withdraw one person's access without changing everybody's, and you
-- cannot tell who recorded what.
--
-- Choose the password with:
--     python -c "import secrets; print(secrets.token_urlsafe(24))"
-- and send it to them privately. Do not put it in this file, in the
-- repository, or in a chat channel the whole team can read.

-- 1. the login
CREATE ROLE dev_name LOGIN PASSWORD 'replace-me';

-- 2. reach the database and the schema
GRANT CONNECT ON DATABASE poker_tracker TO dev_name;
GRANT USAGE   ON SCHEMA public          TO dev_name;

-- 3. read the history and add to it. Nothing more.
--
-- No UPDATE and no DELETE: the tracker only ever inserts, and a recorded hand
-- is a fact about a round that happened. Withholding those two means a
-- mistake, a bad script or a stolen laptop cannot quietly rewrite or erase
-- the history everyone else is relying on.
GRANT SELECT, INSERT ON poker_hands TO dev_name;

-- 4. the id column draws from a sequence, so INSERT needs this too
GRANT USAGE ON SEQUENCE poker_hands_id_seq TO dev_name;


-- ---------------------------------------------------------------------------
-- Checking what someone can do
-- ---------------------------------------------------------------------------
-- \du                                    list the roles
-- \dp poker_hands                        list the grants on the table

-- ---------------------------------------------------------------------------
-- Withdrawing access
-- ---------------------------------------------------------------------------
-- REVOKE ALL ON poker_hands          FROM dev_name;
-- REVOKE ALL ON SEQUENCE poker_hands_id_seq FROM dev_name;
-- REVOKE ALL ON SCHEMA public        FROM dev_name;
-- REVOKE CONNECT ON DATABASE poker_tracker FROM dev_name;
-- DROP ROLE dev_name;
--
-- Their recorded hands stay. They belong to the table, not to the role.
