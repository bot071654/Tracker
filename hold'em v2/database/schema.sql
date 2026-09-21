CREATE TABLE IF NOT EXISTS poker_hands (
    id SERIAL PRIMARY KEY,

    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    player_card_1 VARCHAR(3) NOT NULL,
    player_card_2 VARCHAR(3) NOT NULL,

    flop_card_1 VARCHAR(3) NOT NULL,
    flop_card_2 VARCHAR(3) NOT NULL,
    flop_card_3 VARCHAR(3) NOT NULL,

    turn_card VARCHAR(3) NOT NULL,
    river_card VARCHAR(3) NOT NULL,

    dealer_card_1 VARCHAR(3) NOT NULL,
    dealer_card_2 VARCHAR(3) NOT NULL,

    player_hand VARCHAR(30) NOT NULL,
    dealer_hand VARCHAR(30) NOT NULL,

    hand_fingerprint VARCHAR(100) UNIQUE NOT NULL
);

-- Added after the first hands were recorded, so they are added conditionally
-- and left nullable; tools/setup_database.py fills them in for older rows.
--
-- winner is the showdown comparison: 'Player', 'Dealer' or 'Tie'.
-- dealer_qualified is the Casino Hold'em rule shown on the table itself - the
-- dealer needs a pair of fours or better. When the dealer does not qualify the
-- ante pays and the call bet is returned, whichever hand is stronger, so both
-- facts are kept rather than merged into one verdict.
ALTER TABLE poker_hands ADD COLUMN IF NOT EXISTS winner VARCHAR(10);
ALTER TABLE poker_hands ADD COLUMN IF NOT EXISTS dealer_qualified BOOLEAN;

-- Round timings, in seconds, measured by the tracker as it watches the table.
-- round_started_at is when cards first appeared on an empty table;
-- seconds_since_previous_round runs from the previous showdown to that moment.
ALTER TABLE poker_hands ADD COLUMN IF NOT EXISTS round_started_at TIMESTAMP;
ALTER TABLE poker_hands ADD COLUMN IF NOT EXISTS deal_to_flop_seconds DOUBLE PRECISION;
ALTER TABLE poker_hands ADD COLUMN IF NOT EXISTS flop_to_showdown_seconds DOUBLE PRECISION;
ALTER TABLE poker_hands ADD COLUMN IF NOT EXISTS round_seconds DOUBLE PRECISION;
ALTER TABLE poker_hands ADD COLUMN IF NOT EXISTS seconds_since_previous_round DOUBLE PRECISION;
