import logging
from werkzeug.security import generate_password_hash
from robot.database.connection import db
from robot.database.schema import SCHEMA_SQL

# Increment this whenever a new migration block is added below
SCHEMA_VERSION = 5

logger = logging.getLogger(__name__)

class DatabaseMigrator:
    def migrate(self):
        """Run all necessary database migrations."""
        logger.info("Checking database schema version...")
        
        with db.get_cursor() as cursor:
            # Check if schema_info exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_info'")
            if not cursor.fetchone():
                logger.info("Initializing new database schema...")
                # Split and execute statements (sqlite3 cursor.execute only runs one statement)
                # But executescript handles multiple
                cursor.connection.executescript(SCHEMA_SQL)
                
                # Set initial version
                cursor.execute("INSERT INTO schema_info (version) VALUES (?)", (SCHEMA_VERSION,))
                
                self._seed_initial_data(cursor)
                logger.info("Database schema initialized successfully.")
                return

            # Check current version
            cursor.execute("SELECT MAX(version) FROM schema_info")
            current_version = cursor.fetchone()[0] or 0
            
            if current_version < SCHEMA_VERSION:
                logger.info(f"Migrating database from version {current_version} to {SCHEMA_VERSION}...")
                self._run_migrations(cursor, current_version, SCHEMA_VERSION)
                logger.info("Database migration completed successfully.")
            else:
                logger.debug(f"Database schema is up to date (version {current_version}).")

    def _run_migrations(self, cursor, current_version, target_version):
        """Execute specific migration scripts based on version differences."""
        if current_version < 2:
            logger.info("Applying migration v2: skill_scores tables")
            cursor.connection.executescript("""
                CREATE TABLE IF NOT EXISTS skill_scores (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    child_id     INTEGER NOT NULL REFERENCES children(id),
                    domain       TEXT NOT NULL,
                    score        REAL  DEFAULT 0.0,
                    skill_level  INTEGER DEFAULT 1,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(child_id, domain)
                );

                CREATE TABLE IF NOT EXISTS skill_score_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    child_id    INTEGER NOT NULL REFERENCES children(id),
                    domain      TEXT NOT NULL,
                    score       REAL,
                    skill_level INTEGER,
                    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_skill_history
                    ON skill_score_history(child_id, domain, recorded_at);
            """)
            cursor.execute("INSERT INTO schema_info (version) VALUES (2)")
            logger.info("Migration v2 applied.")

        if current_version < 3:
            logger.info("Applying migration v3: face_encoding column")
            cursor.execute("ALTER TABLE children ADD COLUMN face_encoding BLOB")
            cursor.execute("INSERT INTO schema_info (version) VALUES (3)")
            logger.info("Migration v3 applied.")

        if current_version < 4:
            logger.info("Applying migration v4: advanced therapy fields")
            cursor.execute("ALTER TABLE children ADD COLUMN favorite_animals TEXT DEFAULT '[]'")
            cursor.execute("ALTER TABLE children ADD COLUMN sensory_preferences TEXT DEFAULT '[]'")
            cursor.execute("ALTER TABLE sessions ADD COLUMN eye_contact_score REAL")
            cursor.execute("ALTER TABLE sessions ADD COLUMN social_skill_score REAL")
            cursor.execute("INSERT INTO schema_info (version) VALUES (4)")
            logger.info("Migration v4 applied.")

        if current_version < 5:
            logger.info("Applying migration v5: ensuring all advanced therapy fields exist safely")
            # If a user's DB was created mid-development, some columns might be missing despite version bumps.
            # We use try/except to add them safely if they don't exist.
            try: cursor.execute("ALTER TABLE children ADD COLUMN favorite_animals TEXT DEFAULT '[]'")
            except Exception: pass
            
            try: cursor.execute("ALTER TABLE children ADD COLUMN sensory_preferences TEXT DEFAULT '[]'")
            except Exception: pass
            
            try: cursor.execute("ALTER TABLE sessions ADD COLUMN eye_contact_score REAL")
            except Exception: pass
            
            try: cursor.execute("ALTER TABLE sessions ADD COLUMN social_skill_score REAL")
            except Exception: pass
            
            cursor.execute("INSERT INTO schema_info (version) VALUES (5)")
            logger.info("Migration v5 applied.")

    def _seed_initial_data(self, cursor):
        """Insert default data for a fresh database."""
        try:
            # Add default admin user (password: admin)
            password_hash = generate_password_hash("admin")
            cursor.execute(
                "INSERT INTO caregivers (username, password_hash, name, role) VALUES (?, ?, ?, ?)",
                ("admin", password_hash, "System Admin", "admin")
            )
            caregiver_id = cursor.lastrowid
            
            # Add a sample child profile
            cursor.execute(
                "INSERT INTO children (caregiver_id, name, age, communication_level) VALUES (?, ?, ?, ?)",
                (caregiver_id, "Test Child", 5, "verbal")
            )
            logger.info("Seeded initial admin user and sample child.")
        except Exception as e:
            logger.error(f"Error seeding initial data: {e}")

# Expose run function
def run_migrations():
    migrator = DatabaseMigrator()
    migrator.migrate()
