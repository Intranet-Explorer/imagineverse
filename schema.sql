-- Imaginarium v0 schema
-- Visual fields (appearance, prompt_fragment, renderer, style_tags) are
-- written now and unused until the sprite pipeline lands. Cheap to write,
-- expensive to backfill.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS world (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS character (
    id              INTEGER PRIMARY KEY,
    world_id        INTEGER NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    -- prose the user reads
    bio             TEXT NOT NULL,
    -- instruction block the model gets; deliberately NOT the bio
    persona_prompt  TEXT NOT NULL,
    -- speech register + verbal tics, JSON
    voice           TEXT NOT NULL DEFAULT '{}',
    -- unused in v0, needed by the sprite pipeline
    appearance      TEXT NOT NULL DEFAULT '',
    renderer        TEXT NOT NULL DEFAULT 'anima',
    style_tags      TEXT NOT NULL DEFAULT '[]',
    source_desc     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (world_id, name)
);

CREATE TABLE IF NOT EXISTS outfit (
    id               INTEGER PRIMARY KEY,
    character_id     INTEGER NOT NULL REFERENCES character(id) ON DELETE CASCADE,
    slug             TEXT NOT NULL,
    name             TEXT NOT NULL,
    -- unused in v0
    prompt_fragment  TEXT NOT NULL DEFAULT '',
    is_default       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (character_id, slug)
);

CREATE TABLE IF NOT EXISTS location (
    id               INTEGER PRIMARY KEY,
    world_id         INTEGER NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL,
    -- unused in v0
    prompt_fragment  TEXT NOT NULL DEFAULT '',
    -- locked framing every sprite for this location is generated against;
    -- vary it and the staging anchors stop being valid
    camera_contract  TEXT NOT NULL DEFAULT '',
    -- JSON list of {id, pose_class, note}; becomes anchors when images land
    staging          TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (world_id, name)
);

CREATE TABLE IF NOT EXISTS relationship (
    id          INTEGER PRIMARY KEY,
    world_id    INTEGER NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    -- directional: what `from` feels and wants toward `to`
    from_id     INTEGER NOT NULL REFERENCES character(id) ON DELETE CASCADE,
    to_id       INTEGER NOT NULL REFERENCES character(id) ON DELETE CASCADE,
    -- what from_id is trying to get out of to_id in any scene they share
    wants       TEXT NOT NULL DEFAULT '',
    -- what from_id will not say first, and what it costs to say it
    withholds   TEXT NOT NULL DEFAULT '',
    -- the scene's exit: what would actually move this person, and the price.
    -- Without it both sides are specified as immovable and the only variable
    -- left is intensity, so the scene ratchets instead of developing.
    concedes    TEXT NOT NULL DEFAULT '',
    -- symmetric: written identically on both rows of a pair
    history     TEXT NOT NULL DEFAULT '',
    friction    TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (from_id, to_id),
    CHECK (from_id <> to_id)
);

CREATE INDEX IF NOT EXISTS idx_rel_from ON relationship(from_id);
CREATE INDEX IF NOT EXISTS idx_rel_world ON relationship(world_id);

CREATE TABLE IF NOT EXISTS session (
    id           INTEGER PRIMARY KEY,
    world_id     INTEGER NOT NULL REFERENCES world(id) ON DELETE CASCADE,
    location_id  INTEGER NOT NULL REFERENCES location(id),
    premise      TEXT NOT NULL DEFAULT '',
    -- rolling summary of turns 0..summary_upto-1; the prompt sends this plus
    -- the last WINDOW turns verbatim instead of the whole transcript
    summary      TEXT NOT NULL DEFAULT '',
    summary_upto INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS participant (
    session_id    INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    character_id  INTEGER NOT NULL REFERENCES character(id),
    outfit_id     INTEGER REFERENCES outfit(id),
    PRIMARY KEY (session_id, character_id)
);

CREATE TABLE IF NOT EXISTS turn (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    idx           INTEGER NOT NULL,
    speaker       TEXT NOT NULL,          -- character name, or 'Narrator'
    character_id  INTEGER REFERENCES character(id),
    markup        TEXT NOT NULL,          -- raw line, action tags intact
    origin        TEXT NOT NULL,          -- 'ai' | 'human'
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (session_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_turn_session ON turn(session_id, idx);
