SCHEMA_VERSION = 1

SCHEMA_SQL = """
-- Parent/Caregiver accounts (dashboard login)
CREATE TABLE IF NOT EXISTS caregivers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    name            TEXT NOT NULL,
    email           TEXT,
    role            TEXT DEFAULT 'parent',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Child profiles with therapy metadata
CREATE TABLE IF NOT EXISTS children (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    caregiver_id        INTEGER NOT NULL REFERENCES caregivers(id),
    name                TEXT NOT NULL,
    age                 INTEGER,
    date_of_birth       DATE,
    communication_level TEXT DEFAULT 'verbal',     -- verbal/limited/nonverbal
    preferred_games     TEXT DEFAULT '[]',          -- JSON array
    favorite_topics     TEXT DEFAULT '[]',          -- JSON array
    favorite_animals    TEXT DEFAULT '[]',          -- JSON array
    sensory_preferences TEXT DEFAULT '[]',          -- JSON array
    attention_score     REAL DEFAULT 0.5,
    speech_score        REAL DEFAULT 0.5,
    difficulty_level    INTEGER DEFAULT 1,
    avatar              TEXT DEFAULT 'default',
    face_encoding       BLOB,                       -- DEPRECATED: use face_embeddings table
    is_active           BOOLEAN DEFAULT 1,
    last_seen           TIMESTAMP,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Multiple face embeddings per child for better recognition
CREATE TABLE IF NOT EXISTS face_embeddings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id            INTEGER NOT NULL REFERENCES children(id),
    embedding           BLOB NOT NULL,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Therapy sessions
CREATE TABLE IF NOT EXISTS sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id            INTEGER NOT NULL REFERENCES children(id),
    session_type        TEXT NOT NULL,              -- conversation/therapy/game
    start_time          TIMESTAMP NOT NULL,
    end_time            TIMESTAMP,
    duration_seconds    INTEGER,
    games_played        TEXT DEFAULT '[]',          -- JSON array
    words_spoken        INTEGER DEFAULT 0,
    attention_score     REAL,
    engagement_score    REAL,
    eye_contact_score   REAL,
    social_skill_score  REAL,
    speech_score        REAL,
    mood_start          TEXT,
    mood_end            TEXT,
    difficulty_level    INTEGER,
    notes               TEXT,
    report_json         TEXT                        -- Full session report
);

-- Individual game/trial results
CREATE TABLE IF NOT EXISTS game_results (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL REFERENCES sessions(id),
    child_id            INTEGER NOT NULL REFERENCES children(id),
    game_type           TEXT NOT NULL,
    difficulty_level    INTEGER,
    score               REAL,
    correct_count       INTEGER DEFAULT 0,
    total_count         INTEGER DEFAULT 0,
    response_time_avg   REAL,
    completed           BOOLEAN DEFAULT 0,
    data_json           TEXT,                       -- Game-specific data
    played_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Rewards and achievements
CREATE TABLE IF NOT EXISTS achievements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id            INTEGER NOT NULL REFERENCES children(id),
    achievement_type    TEXT NOT NULL,               -- star/badge/level
    achievement_id      TEXT NOT NULL,
    achievement_name    TEXT NOT NULL,
    description         TEXT,
    earned_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(child_id, achievement_id)
);

-- Reward token balance and history
CREATE TABLE IF NOT EXISTS rewards (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id            INTEGER NOT NULL REFERENCES children(id),
    reward_type         TEXT NOT NULL,               -- star/token
    amount              INTEGER DEFAULT 1,
    reason              TEXT,
    earned_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Emotion detection log
CREATE TABLE IF NOT EXISTS emotion_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id            INTEGER,
    session_id          INTEGER REFERENCES sessions(id),
    source              TEXT NOT NULL,               -- voice/face/context
    emotion             TEXT NOT NULL,
    confidence          REAL,
    timestamp           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Engagement tracking
CREATE TABLE IF NOT EXISTS engagement_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id            INTEGER,
    session_id          INTEGER REFERENCES sessions(id),
    engaged             BOOLEAN,
    face_detected       BOOLEAN,
    pitch               REAL,
    yaw                 REAL,
    engagement_score    REAL,
    timestamp           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Long-term memory entries
CREATE TABLE IF NOT EXISTS memories (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id            INTEGER NOT NULL REFERENCES children(id),
    memory_type         TEXT NOT NULL,               -- interest/preference/milestone/note
    category            TEXT,
    content             TEXT NOT NULL,
    importance          REAL DEFAULT 0.5,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_referenced     TIMESTAMP
);

-- Safety/distress events
CREATE TABLE IF NOT EXISTS safety_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id            INTEGER,
    session_id          INTEGER REFERENCES sessions(id),
    event_type          TEXT NOT NULL,               -- distress/content_block/session_limit
    severity            TEXT DEFAULT 'low',          -- low/medium/high
    details             TEXT,
    action_taken        TEXT,
    acknowledged        BOOLEAN DEFAULT 0,
    timestamp           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
