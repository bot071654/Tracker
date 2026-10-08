-- 0002: who first reported a hand.
--
-- poker_hands.hand_fingerprint stays globally UNIQUE: several people watching the same table record the same
-- round once, and the second report is a "duplicate". These columns keep who (and which installation) got
-- there first. Both are nullable so every hand recorded before accounts existed stays valid.

ALTER TABLE poker_hands ADD COLUMN IF NOT EXISTS reported_by_user_id   BIGINT REFERENCES users(id)   ON DELETE SET NULL;
ALTER TABLE poker_hands ADD COLUMN IF NOT EXISTS reported_by_device_id UUID   REFERENCES devices(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS poker_hands_reported_by_user_idx ON poker_hands (reported_by_user_id);
