import aiosqlite
import asyncio
import json
import logging
import os
import weakref

from .services.snippets import build_snippets

logger = logging.getLogger(__name__)


def _safe_snippets(content: str) -> tuple:
    """A failed snippet must never fail a parse. NULL hands the row to the read-path
    fallback and the backfill."""
    try:
        return build_snippets(content)
    except Exception:
        logger.exception("Snippet build failed; storing NULL")
        return None, None


# DATA_DIR lets a container mount a volume somewhere other than the source tree.
# Unset, this resolves to backend/data exactly as before.
_DEFAULT_DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def resolve_db_dir():
    """Where the database and image caches live.

    Exposed as a function so tests can exercise the override without reloading this
    module. Reloading it would build a fresh Database class with empty connection
    registries, orphaning every worker thread opened before the reload.
    """
    return os.environ.get("DATA_DIR") or _DEFAULT_DB_DIR


DB_DIR = resolve_db_dir()
DB_PATH = os.path.join(DB_DIR, "feeds.db")

FEED_SOURCE_COLUMNS = {
    "id", "category_id", "name", "url", "site_url", "item_selector", "link_selector",
    "content_selector", "title_selector", "date_selector", "image_selector",
    "exclude_selector", "content_exclude_selector", "negative_keywords", "exclusion_patterns",
    "translator", "translate_to", "detected_language", "status", "error_count", "last_error",
    "last_checked_at", "next_check_at", "last_generation_at", "feed_article_count",
    "use_parse_date", "desktop_view_mode", "mobile_view_mode", "enabled", "created_at",
    "updated_at", "last_fetch_at", "last_fetch_links_count", "last_fetch_has_images",
}

class Database:
    # Every live instance, so close_all_sync can reach the ones still referenced at
    # interpreter shutdown. Note that GC does not clean these up on our behalf:
    # aiosqlite.Connection has no __del__, and its running Thread keeps it reachable,
    # so a worker is only ever stopped by an explicit _stop_conn().
    _instances: "weakref.WeakSet" = weakref.WeakSet()

    # Every open connection, held strongly. Tracking wrappers alone is not enough: a
    # Database built inside a test goes out of scope when the test ends and drops out
    # of the WeakSet above, while its worker thread runs on with nothing able to reach
    # it. Entries are removed as they are stopped, so this never grows unbounded.
    _live_connections: set = set()

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self._db = None  # persistent connection
        self._lock = None
        self._loop = None
        Database._instances.add(self)

    def _get_lock(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._loop is not loop or self._lock is None:
            # The connection belongs to the loop that is being replaced and can never
            # be used again. Stop its worker before dropping it: merely reassigning
            # self._db leaks a live non-daemon thread that hangs interpreter exit.
            # Under pytest-asyncio every test gets a fresh loop, so this fires often.
            self._stop_connection()
            self._loop = loop
            self._lock = asyncio.Lock()
        return self._lock

    async def _get_db(self):
        """Get or create the persistent database connection.

        Serialised: two concurrent cold-start callers would otherwise each open a
        connection and orphan one, leaking its file handle and WAL lock for the
        lifetime of the process.
        """
        async with self._get_lock():
            # Default True: aiosqlite has renamed private members before (0.20's
            # _stop_running became 0.21's stop), and a missing attribute must not
            # read as "dead" — that would rebuild the connection on every call.
            if self._db is not None and not getattr(self._db, '_running', True):
                try:
                    await self._db.close()
                except Exception:
                    pass
                self._db = None
            if self._db is None:
                self._db = await aiosqlite.connect(self.db_path, timeout=30)
                Database._live_connections.add(self._db)
                await self._db.execute('PRAGMA busy_timeout = 30000')
                await self._db.execute('PRAGMA journal_mode = WAL')
                self._db.row_factory = aiosqlite.Row
            return self._db

    async def close(self):
        """Close the persistent connection (call on app shutdown)."""
        async with self._get_lock():
            if self._db:
                await self._db.close()
                self._db = None
                self._loop = None

    def _stop_connection(self):
        """Drop the current connection, stopping its worker thread first.

        aiosqlite builds its worker thread without `daemon=True` and parks it on a
        blocking `SimpleQueue.get()`, so `threading._shutdown()` joins it forever unless
        something queues the stop sentinel. `Connection.stop()` queues it and tolerates
        having no running loop; `close()` cannot be used here because it awaits.

        Dropping the reference is *not* sufficient. `Connection` defines no `__del__`
        anywhere in its MRO, and a running `Thread` stays reachable from
        `threading._active`, so an unstopped worker is never collected and never dies.
        Every path that discards `self._db` must therefore come through here.

        The method that queues the sentinel was renamed: 0.20 (the Pi) has
        `_stop_running`, 0.21+ (dev machines) has a public `stop`. requirements.txt says
        `aiosqlite>=0.20.0`, so both are live and both must work. Never fall back to
        `_stop` — on 0.20 `Connection` subclasses `Thread`, so that name resolves to
        `threading.Thread._stop`, which marks the thread dead without ever stopping it.
        """
        conn, self._db = self._db, None
        if conn is None:
            return
        Database._stop_conn(conn)

    @staticmethod
    def _stop_conn(conn):
        """Queue the stop sentinel for one connection's worker and forget it."""
        Database._live_connections.discard(conn)
        stop = getattr(conn, "stop", None)
        if stop is None:
            stop = getattr(conn, "_stop_running", None)
        if stop is not None:
            stop()

    def close_sync(self):
        """Close from a synchronous context, with no event loop required.

        Call once before a non-app process exits — pytest sessions and ad-hoc scripts.

        `atexit` is not an option: CPython runs `threading._shutdown()` *before* atexit
        handlers, so a handler registered here never gets to run. The call has to be
        explicit, while the interpreter is still live.
        """
        self._stop_connection()

    async def init(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        db = await self._get_db()
        await db.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS feed_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                item_selector TEXT,
                link_selector TEXT,
                status TEXT DEFAULT 'grey',
                last_fetch_at TIMESTAMP,
                last_fetch_links_count INTEGER DEFAULT 0,
                last_fetch_has_images BOOLEAN DEFAULT 0,
                last_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                desktop_view_mode TEXT DEFAULT 'standard',
                mobile_view_mode TEXT DEFAULT 'standard',
                FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE SET NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS feed_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                title TEXT,
                content TEXT,
                pub_date TEXT,
                main_image TEXT,
                parse_status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                last_retry_at TIMESTAMP,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_id) REFERENCES feed_sources (id) ON DELETE CASCADE,
                UNIQUE(source_id, url)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS fever_auth (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                api_key TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS web_auth (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                session_token TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS web_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS translation_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER,
                article_url TEXT,
                cache_hit_tokens INTEGER DEFAULT 0,
                cache_miss_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                cost_cny REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS image_focal_points (
                image_hash TEXT PRIMARY KEY,
                focal_x INTEGER NOT NULL DEFAULT 50,
                focal_y INTEGER NOT NULL DEFAULT 50,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_article_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER,
                source_id INTEGER,
                source_name TEXT,
                event_type TEXT NOT NULL,
                primary_topic TEXT,
                secondary_topics TEXT,
                region TEXT,
                article_type TEXT,
                dwell_seconds INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_events_type_topic
                ON user_article_events(event_type, primary_topic)
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS article_votes (
                article_id       INTEGER PRIMARY KEY,
                vote             TEXT NOT NULL,
                primary_topic    TEXT,
                secondary_topics TEXT,
                region           TEXT,
                article_type     TEXT,
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_article_votes_updated
                ON article_votes(updated_at)
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS article_impressions (
                article_id   INTEGER PRIMARY KEY,
                count        INTEGER NOT NULL DEFAULT 0,
                last_seen_at TIMESTAMP
            )
        ''')
        cursor = await db.execute('SELECT COUNT(*) FROM article_impressions')
        row = await cursor.fetchone()
        if row and row[0] == 0:
            await db.execute('''
                INSERT INTO article_impressions (article_id, count, last_seen_at)
                SELECT article_id, COUNT(*), MAX(created_at)
                FROM user_article_events
                WHERE event_type = 'impression' AND article_id IS NOT NULL
                GROUP BY article_id
            ''')
            await db.commit()
        # Migrations
        migrations = [
            'ALTER TABLE feed_sources ADD COLUMN exclusion_patterns TEXT DEFAULT NULL',
            'ALTER TABLE feed_sources ADD COLUMN last_generation_at TIMESTAMP DEFAULT NULL',
            'ALTER TABLE feed_sources ADD COLUMN feed_article_count INTEGER DEFAULT 0',
            'ALTER TABLE feed_sources ADD COLUMN detected_language TEXT DEFAULT NULL',
            'ALTER TABLE feed_sources ADD COLUMN translate_to TEXT DEFAULT NULL',
            'ALTER TABLE feed_sources ADD COLUMN content_selector TEXT DEFAULT NULL',
            'ALTER TABLE feed_sources ADD COLUMN title_selector TEXT DEFAULT NULL',
            'ALTER TABLE feed_sources ADD COLUMN date_selector TEXT DEFAULT NULL',
            'ALTER TABLE feed_sources ADD COLUMN image_selector TEXT DEFAULT NULL',
            'ALTER TABLE feed_sources ADD COLUMN latest_title TEXT DEFAULT NULL',
            'ALTER TABLE feed_articles ADD COLUMN is_read INTEGER DEFAULT 0',
            'ALTER TABLE feed_articles ADD COLUMN is_saved INTEGER DEFAULT 0',
            'ALTER TABLE feed_sources ADD COLUMN next_check_at TIMESTAMP DEFAULT NULL',
            'ALTER TABLE feed_sources ADD COLUMN error_count INTEGER DEFAULT 0',
            'ALTER TABLE feed_sources ADD COLUMN enabled INTEGER DEFAULT 1',
            'ALTER TABLE feed_sources ADD COLUMN exclude_selector TEXT DEFAULT NULL',
            'ALTER TABLE feed_sources ADD COLUMN content_exclude_selector TEXT DEFAULT NULL',
            'ALTER TABLE feed_sources ADD COLUMN negative_keywords TEXT DEFAULT NULL',
            'ALTER TABLE feed_sources ADD COLUMN translator TEXT DEFAULT \'google\'',
            'ALTER TABLE feed_sources DROP COLUMN latest_pub_date',
            'ALTER TABLE feed_sources DROP COLUMN latest_title',
            'ALTER TABLE feed_sources ADD COLUMN use_parse_date INTEGER DEFAULT 0',
            'ALTER TABLE feed_sources ADD COLUMN site_url TEXT DEFAULT NULL',
            'ALTER TABLE feed_articles ADD COLUMN primary_topic TEXT DEFAULT NULL',
            'ALTER TABLE feed_articles ADD COLUMN secondary_topics TEXT DEFAULT NULL',
            'ALTER TABLE feed_articles ADD COLUMN region TEXT DEFAULT NULL',
            'ALTER TABLE feed_articles ADD COLUMN article_type TEXT DEFAULT NULL',
            'ALTER TABLE feed_articles ADD COLUMN tag_confidence TEXT DEFAULT NULL',
            'ALTER TABLE feed_articles ADD COLUMN ai_summary TEXT DEFAULT NULL',
            'ALTER TABLE feed_articles ADD COLUMN topic_extracted_at TIMESTAMP DEFAULT NULL',
            'ALTER TABLE feed_articles ADD COLUMN original_title TEXT DEFAULT NULL',
            'ALTER TABLE feed_articles ADD COLUMN original_excerpt TEXT DEFAULT NULL',
            'ALTER TABLE feed_articles ADD COLUMN snippet TEXT DEFAULT NULL',
            'ALTER TABLE feed_articles ADD COLUMN featured_snippet TEXT DEFAULT NULL',
            # translate_to is an on/off flag now — the language comes from
            # system_settings.target_language. Normalise legacy per-feed values.
            "UPDATE feed_sources SET translate_to = '1' "
            "WHERE translate_to IS NOT NULL AND translate_to != '1'",
        ]
        for migration in migrations:
            try:
                await db.execute(migration)
            except Exception as e:
                err_msg = str(e).lower()
                if "duplicate column" in err_msg or "no such column" in err_msg:
                    pass  # Column already exists or already dropped
                else:
                    logger.warning(f"Migration statement failed: {migration} - Error: {e}")
        await db.commit()

        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_articles_untagged
                ON feed_articles(topic_extracted_at) WHERE topic_extracted_at IS NULL
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_feed_articles_source_status_pubdate
                ON feed_articles(source_id, parse_status, pub_date DESC, id DESC)
        ''')
        await db.commit()

        # Warm the settings cache so the sync readers in translation.py and
        # fetch.py see saved settings before the first scheduled refresh runs.
        await self.get_system_settings()

    async def get_categories(self):
        db = await self._get_db()
        cursor = await db.execute('''
            SELECT c.id, c.name, COUNT(f.id) as source_count
            FROM categories c
            LEFT JOIN feed_sources f ON c.id = f.category_id
            GROUP BY c.id, c.name
            ORDER BY c.name
        ''')
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def log_translation_usage(self, source_id, article_url, cache_hit_tokens,
                                    cache_miss_tokens, completion_tokens, cost_cny):
        db = await self._get_db()
        await db.execute(
            '''INSERT INTO translation_usage
               (source_id, article_url, cache_hit_tokens, cache_miss_tokens,
                completion_tokens, cost_cny)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (source_id, article_url, cache_hit_tokens, cache_miss_tokens,
             completion_tokens, cost_cny),
        )
        await db.commit()

    async def get_translation_usage_by_source(self, days: int = 7):
        db = await self._get_db()
        cursor = await db.execute(
            '''SELECT source_id,
                      COUNT(*) AS calls,
                      COALESCE(SUM(cost_cny), 0) AS cost_cny
               FROM translation_usage
               WHERE created_at >= datetime('now', ?)
               GROUP BY source_id''',
            (f'-{int(days)} days',),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def create_category(self, name: str):
        db = await self._get_db()
        cursor = await db.execute('INSERT INTO categories (name) VALUES (?)', (name,))
        await db.commit()
        return cursor.lastrowid

    async def update_category(self, id: int, name: str):
        db = await self._get_db()
        await db.execute('UPDATE categories SET name = ? WHERE id = ?', (name, id))
        await db.commit()

    async def delete_category(self, id: int):
        db = await self._get_db()
        cursor = await db.execute('SELECT COUNT(*) FROM feed_sources WHERE category_id = ?', (id,))
        count = (await cursor.fetchone())[0]
        if count > 0:
            raise ValueError("Cannot delete category with attached sources")

        await db.execute('DELETE FROM categories WHERE id = ?', (id,))
        await db.commit()

    async def get_feed_sources(self):
        db = await self._get_db()
        cursor = await db.execute('''
            SELECT f.*, c.name as category_name,
                   (SELECT title FROM feed_articles
                    WHERE source_id = f.id AND parse_status = 'success'
                    ORDER BY pub_date DESC, id DESC LIMIT 1) as latest_title,
                   (SELECT pub_date FROM feed_articles
                    WHERE source_id = f.id AND parse_status = 'success'
                    ORDER BY pub_date DESC, id DESC LIMIT 1) as latest_pub_date
            FROM feed_sources f
            LEFT JOIN categories c ON f.category_id = c.id
            ORDER BY f.created_at DESC
        ''')
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_feed_source(self, id: int):
        db = await self._get_db()
        cursor = await db.execute('SELECT * FROM feed_sources WHERE id = ?', (id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def create_feed_source(self, data: dict):
        db = await self._get_db()
        invalid_keys = set(data.keys()) - FEED_SOURCE_COLUMNS
        if invalid_keys:
            raise ValueError(f"Invalid column names for feed_sources: {invalid_keys}")
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['?'] * len(data))
        values = tuple(data.values())

        cursor = await db.execute(f'INSERT INTO feed_sources ({columns}) VALUES ({placeholders})', values)
        await db.commit()
        return cursor.lastrowid

    async def update_feed_source(self, id: int, data: dict):
        db = await self._get_db()
        data_copy = dict(data)
        data_copy['updated_at'] = 'CURRENT_TIMESTAMP'
        invalid_keys = set(data_copy.keys()) - FEED_SOURCE_COLUMNS
        if invalid_keys:
            raise ValueError(f"Invalid column names for feed_sources: {invalid_keys}")
        set_parts = []
        bind_values = []
        for k, v in data_copy.items():
            if v == 'CURRENT_TIMESTAMP':
                set_parts.append(f"{k} = CURRENT_TIMESTAMP")
            else:
                set_parts.append(f"{k} = ?")
                bind_values.append(v)
        set_clause = ', '.join(set_parts)
        values = tuple(bind_values + [id])

        await db.execute(f'UPDATE feed_sources SET {set_clause} WHERE id = ?', values)
        await db.commit()

    async def toggle_feed_source_enabled(self, id: int) -> bool:
        """Toggle enabled state. Returns new enabled value."""
        db = await self._get_db()
        cursor = await db.execute('SELECT enabled FROM feed_sources WHERE id = ?', (id,))
        row = await cursor.fetchone()
        if not row:
            return False
        new_val = 0 if row[0] else 1
        await db.execute('UPDATE feed_sources SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', (new_val, id))
        await db.commit()
        return bool(new_val)

    async def delete_feed_source(self, id: int):
        db = await self._get_db()
        await db.execute('DELETE FROM feed_articles WHERE source_id = ?', (id,))
        await db.execute('DELETE FROM feed_sources WHERE id = ?', (id,))
        await db.commit()

    # --- Feed Articles CRUD ---

    # There is no foreign key on feed_articles.source_id, so a fetch still in flight when
    # its source is deleted will happily insert rows for a source that no longer exists.
    # Those rows are then unreachable: get_untagged_articles and the reader query both
    # INNER JOIN feed_sources, so they can never be tagged, shown or cleaned up. Ten such
    # rows sat in the live database from 2026-05-28 to 2026-08-17. The WHERE EXISTS makes
    # the insert a no-op once the source is gone, which is what the missing FK would do.
    _INSERT_ARTICLE_SQL = (
        'INSERT OR IGNORE INTO feed_articles (source_id, url, main_image) '
        'SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM feed_sources WHERE id = ?)'
    )

    async def insert_feed_article(self, source_id: int, url: str) -> bool:
        """INSERT OR IGNORE a new pending article. Returns True if inserted."""
        db = await self._get_db()
        cursor = await db.execute(
            self._INSERT_ARTICLE_SQL, (source_id, url, None, source_id)
        )
        await db.commit()
        return cursor.rowcount > 0

    async def insert_feed_articles(self, source_id: int, articles: list) -> int:
        """Insert a batch of pending articles. Returns the number of new rows."""
        if not articles:
            return 0

        db = await self._get_db()

        # Prepare data: (source_id, url, main_image, source_id)
        data = []
        for art in articles:
            if isinstance(art, str):
                data.append((source_id, art, None, source_id))
            elif isinstance(art, dict):
                data.append((source_id, art.get('url'), art.get('image'), source_id))
            else:
                continue

        cursor = await db.executemany(self._INSERT_ARTICLE_SQL, data)
        await db.commit()
        return cursor.rowcount

    async def count_orphaned_articles(self) -> int:
        """Articles whose feed source no longer exists."""
        db = await self._get_db()
        cursor = await db.execute("""
            SELECT COUNT(*) FROM feed_articles a
            LEFT JOIN feed_sources f ON f.id = a.source_id
            WHERE f.id IS NULL
        """)
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def purge_orphaned_articles(self) -> int:
        """Clear rows orphaned before the insert guard existed. Returns rows removed."""
        db = await self._get_db()
        cursor = await db.execute("""
            DELETE FROM feed_articles
            WHERE source_id NOT IN (SELECT id FROM feed_sources)
        """)
        await db.commit()
        return cursor.rowcount

    async def get_pending_articles(self, source_id: int = None, max_retry: int = 10):
        """Get articles needing parsing."""
        db = await self._get_db()
        if source_id is not None:
            cursor = await db.execute(
                "SELECT * FROM feed_articles WHERE source_id = ? AND parse_status = 'pending' AND retry_count < ? ORDER BY created_at",
                (source_id, max_retry)
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM feed_articles WHERE parse_status = 'pending' AND retry_count < ? ORDER BY created_at",
                (max_retry,)
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_retry_articles(self, max_retry: int = 10):
        """Get articles that failed at least once and need retry."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM feed_articles WHERE parse_status = 'pending' AND retry_count > 0 AND retry_count < ? ORDER BY created_at",
            (max_retry,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def update_article_parsed(self, article_id: int, title: str, content: str, pub_date: str, main_image: str,
                                    original_title: str = None, original_excerpt: str = None):
        db = await self._get_db()
        snippet, featured_snippet = _safe_snippets(content)
        await db.execute(
            "UPDATE feed_articles SET title = ?, content = ?, pub_date = ?, main_image = ?, "
            "original_title = ?, original_excerpt = ?, snippet = ?, featured_snippet = ?, "
            "parse_status = 'success', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (title, content, pub_date, main_image, original_title, original_excerpt,
             snippet, featured_snippet, article_id)
        )
        await db.commit()

    async def update_article_translation(self, article_id: int, title: str, content: str,
                                         original_title: str = None, original_excerpt: str = None):
        """Persist translated title/content immediately after translation succeeds to prevent re-paying on retry."""
        db = await self._get_db()
        await db.execute(
            "UPDATE feed_articles SET title = ?, content = ?, original_title = ?, "
            "original_excerpt = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (title, content, original_title, original_excerpt, article_id)
        )
        await db.commit()

    async def get_signed_image_articles(self, source_id: int):
        """Successfully-parsed articles for a source that still hold signed/expiring
        image URLs (CloudFront/S3). SQL-prefiltered so it is a no-op for the vast
        majority of sources, which never serve signed image URLs."""
        db = await self._get_db()
        cursor = await db.execute(
            """SELECT id, url, content, main_image FROM feed_articles
               WHERE source_id = ? AND parse_status = 'success'
                 AND (content LIKE '%Signature=%' OR content LIKE '%Key-Pair-Id=%'
                      OR content LIKE '%X-Amz-Signature=%'
                      OR main_image LIKE '%Signature=%' OR main_image LIKE '%Key-Pair-Id=%'
                      OR main_image LIKE '%X-Amz-Signature=%')""",
            (source_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def update_article_images(self, article_id: int, content: str, main_image: str):
        """Update only the content + main_image of an article (used by image re-cache)."""
        db = await self._get_db()
        snippet, featured_snippet = _safe_snippets(content)
        await db.execute(
            "UPDATE feed_articles SET content = ?, main_image = ?, snippet = ?, featured_snippet = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (content, main_image, snippet, featured_snippet, article_id)
        )
        await db.commit()

    async def backfill_snippets(self, batch_size: int = 200) -> int:
        """Fill snippet columns for one batch of rows. Returns rows updated, 0 when done."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id, content FROM feed_articles "
            "WHERE snippet IS NULL AND parse_status = 'success' LIMIT ?",
            (batch_size,)
        )
        rows = await cursor.fetchall()
        if not rows:
            return 0

        updates = []
        for row in rows:
            try:
                snippet, featured_snippet = build_snippets(row["content"])
            except Exception:
                # Deliberately not _safe_snippets: its NULL would re-select this row on
                # the next batch and the loop would never terminate.
                logger.exception("Snippet backfill failed for article %s", row["id"])
                snippet, featured_snippet = "", ""
            updates.append((snippet, featured_snippet, row["id"]))

        await db.executemany(
            "UPDATE feed_articles SET snippet = ?, featured_snippet = ? WHERE id = ?",
            updates
        )
        await db.commit()
        return len(updates)

    async def update_article_failed(self, article_id: int, error_message: str):
        db = await self._get_db()
        await db.execute(
            "UPDATE feed_articles SET retry_count = retry_count + 1, last_retry_at = CURRENT_TIMESTAMP, error_message = ?, parse_status = CASE WHEN retry_count + 1 >= 10 THEN 'failed' ELSE 'pending' END, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (error_message, article_id)
        )
        await db.commit()

    async def reset_failed_articles(self, source_id: int):
        """Reset failed articles back to pending for a new hourly cycle."""
        db = await self._get_db()
        await db.execute(
            "UPDATE feed_articles SET parse_status = 'pending', retry_count = 0 WHERE source_id = ? AND parse_status = 'failed'",
            (source_id,)
        )
        await db.commit()

    async def reset_all_articles(self, source_id: int):
        """Reset all articles for a source back to pending (e.g. after selector change)."""
        db = await self._get_db()
        await db.execute(
            "UPDATE feed_articles SET parse_status = 'pending', retry_count = 0 WHERE source_id = ?",
            (source_id,)
        )
        await db.commit()

    async def get_feed_articles(self, source_id: int, limit: int = 50):
        """Get successfully parsed articles for RSS feed."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM feed_articles WHERE source_id = ? AND parse_status = 'success' ORDER BY pub_date DESC, id DESC LIMIT ?",
            (source_id, limit)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_prunable_articles(self, source_id: int, keep: int = 50):
        """Get articles that would be pruned beyond the keep limit."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM feed_articles WHERE source_id = ? AND id NOT IN (SELECT id FROM feed_articles WHERE source_id = ? ORDER BY created_at DESC LIMIT ?)",
            (source_id, source_id, keep)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def delete_old_articles(self, source_id: int, keep: int = 50):
        """Prune articles beyond the keep limit."""
        db = await self._get_db()
        await db.execute(
            "DELETE FROM feed_articles WHERE source_id = ? AND id NOT IN (SELECT id FROM feed_articles WHERE source_id = ? ORDER BY created_at DESC LIMIT ?)",
            (source_id, source_id, keep)
        )
        await db.commit()

    async def get_all_source_articles(self, source_id: int) -> list[dict]:
        """Every article for a source — used to release their cached images."""
        db = await self._get_db()
        cursor = await db.execute(
            'SELECT content, main_image FROM feed_articles WHERE source_id = ?', (source_id,)
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_article_image_blobs(self) -> list[tuple]:
        """Every article's content and main_image, for the orphan sweep."""
        db = await self._get_db()
        cursor = await db.execute('SELECT content, main_image FROM feed_articles')
        return [(row['content'], row['main_image']) for row in await cursor.fetchall()]

    async def has_article_image_reference(self, image_url: str) -> bool:
        """Return True if any article still references the image URL."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT 1 FROM feed_articles WHERE main_image = ? OR instr(COALESCE(content, ''), ?) > 0 LIMIT 1",
            (image_url, image_url)
        )
        row = await cursor.fetchone()
        return row is not None

    async def get_article_stats(self, source_id: int) -> dict:
        """Count articles by parse_status for dashboard display."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT parse_status, COUNT(*) as count FROM feed_articles WHERE source_id = ? GROUP BY parse_status",
            (source_id,)
        )
        rows = await cursor.fetchall()
        stats = {row[0]: row[1] for row in rows}
        return stats

    # --- Feed Sources scheduling ---

    async def get_sources_due_for_refresh(self):
        """Get enabled sources where next_check_at is NULL or in the past."""
        db = await self._get_db()
        cursor = await db.execute('''
            SELECT f.*, c.name as category_name
            FROM feed_sources f
            LEFT JOIN categories c ON f.category_id = c.id
            WHERE (f.next_check_at IS NULL OR f.next_check_at <= datetime('now'))
              AND f.enabled = 1
            ORDER BY CASE f.status WHEN 'red' THEN 0 WHEN 'yellow' THEN 1 ELSE 2 END,
                     f.next_check_at ASC
        ''')
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # --- Fever API ---

    async def get_fever_auth(self):
        db = await self._get_db()
        cursor = await db.execute('SELECT * FROM fever_auth LIMIT 1')
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def set_fever_auth(self, username: str, api_key: str):
        db = await self._get_db()
        await db.execute('DELETE FROM fever_auth')
        await db.execute(
            'INSERT INTO fever_auth (username, api_key) VALUES (?, ?)',
            (username, api_key)
        )
        await db.commit()

    async def delete_fever_auth(self):
        db = await self._get_db()
        await db.execute('DELETE FROM fever_auth')
        await db.commit()

    async def get_fever_items(self, since_id=None, max_id=None, with_ids=None, limit=50):
        db = await self._get_db()
        if with_ids:
            placeholders = ','.join(['?'] * len(with_ids))
            cursor = await db.execute(
                f"SELECT * FROM feed_articles WHERE parse_status = 'success' AND id IN ({placeholders}) ORDER BY id DESC LIMIT ?",
                (*with_ids, limit)
            )
        elif since_id is not None:
            cursor = await db.execute(
                "SELECT * FROM feed_articles WHERE parse_status = 'success' AND id > ? ORDER BY id ASC LIMIT ?",
                (since_id, limit)
            )
        elif max_id is not None:
            cursor = await db.execute(
                "SELECT * FROM feed_articles WHERE parse_status = 'success' AND id < ? ORDER BY id DESC LIMIT ?",
                (max_id, limit)
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM feed_articles WHERE parse_status = 'success' ORDER BY id DESC LIMIT ?",
                (limit,)
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_total_item_count(self):
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT COUNT(*) FROM feed_articles WHERE parse_status = 'success'"
        )
        return (await cursor.fetchone())[0]

    async def get_unread_item_ids(self):
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id FROM feed_articles WHERE parse_status = 'success' AND is_read = 0"
        )
        return [row[0] for row in await cursor.fetchall()]

    async def get_saved_item_ids(self):
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id FROM feed_articles WHERE parse_status = 'success' AND is_saved = 1"
        )
        return [row[0] for row in await cursor.fetchall()]

    async def mark_item_read(self, item_id: int):
        db = await self._get_db()
        await db.execute(
            'UPDATE feed_articles SET is_read = 1 WHERE id = ?', (item_id,)
        )
        await db.commit()

    async def mark_item_saved(self, item_id: int):
        db = await self._get_db()
        await db.execute(
            'UPDATE feed_articles SET is_saved = 1 WHERE id = ?', (item_id,)
        )
        await db.commit()

    async def mark_item_unsaved(self, item_id: int):
        db = await self._get_db()
        await db.execute(
            'UPDATE feed_articles SET is_saved = 0 WHERE id = ?', (item_id,)
        )
        await db.commit()

    async def mark_feed_read(self, feed_id: int, before_ts: int):
        db = await self._get_db()
        await db.execute(
            "UPDATE feed_articles SET is_read = 1 WHERE source_id = ? AND strftime('%s', created_at) <= ?",
            (feed_id, str(before_ts))
        )
        await db.commit()

    async def mark_group_read(self, group_id: int, before_ts: int):
        db = await self._get_db()
        if group_id == 0:
            await db.execute(
                "UPDATE feed_articles SET is_read = 1 WHERE source_id IN (SELECT id FROM feed_sources WHERE category_id IS NULL) AND strftime('%s', created_at) <= ?",
                (str(before_ts),)
            )
        else:
            await db.execute(
                "UPDATE feed_articles SET is_read = 1 WHERE source_id IN (SELECT id FROM feed_sources WHERE category_id = ?) AND strftime('%s', created_at) <= ?",
                (group_id, str(before_ts))
            )
        await db.commit()

    async def get_last_refreshed_on_time(self):
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT MAX(strftime('%s', updated_at)) FROM feed_articles WHERE parse_status = 'success'"
        )
        row = await cursor.fetchone()
        return int(row[0]) if row and row[0] else 0

    # --- Web Auth ---

    async def get_web_auth(self):
        db = await self._get_db()
        cursor = await db.execute('SELECT * FROM web_auth LIMIT 1')
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def set_web_auth(self, username: str, password_hash: str):
        db = await self._get_db()
        await db.execute('DELETE FROM web_auth')
        await db.execute('DELETE FROM web_sessions')
        await db.execute(
            'INSERT INTO web_auth (username, password_hash) VALUES (?, ?)',
            (username, password_hash)
        )
        await db.commit()

    async def update_web_auth(self, username: str, password_hash: str):
        db = await self._get_db()
        await db.execute(
            'UPDATE web_auth SET username = ?, password_hash = ?',
            (username, password_hash)
        )
        await db.commit()

    async def add_session_token(self, token: str):
        db = await self._get_db()
        await db.execute('INSERT OR REPLACE INTO web_sessions (token) VALUES (?)', (token,))
        # Keep the legacy column populated for old installs and compatibility.
        await db.execute('UPDATE web_auth SET session_token = ?', (token,))
        await db.commit()

    async def validate_session_token(self, token: str) -> bool:
        if not token:
            return False
        db = await self._get_db()
        cursor = await db.execute(
            'SELECT COUNT(*) FROM web_sessions WHERE token = ?', (token,)
        )
        session_count = (await cursor.fetchone())[0]
        if session_count > 0:
            return True

        cursor = await db.execute(
            'SELECT COUNT(*) FROM web_auth WHERE session_token = ?', (token,)
        )
        legacy_count = (await cursor.fetchone())[0]
        return legacy_count > 0

    async def clear_session_token(self, token: str):
        db = await self._get_db()
        await db.execute('DELETE FROM web_sessions WHERE token = ?', (token,))
        await db.execute('UPDATE web_auth SET session_token = NULL WHERE session_token = ?', (token,))
        await db.commit()

    async def get_system_settings(self) -> dict:
        db = await self._get_db()
        cursor = await db.execute('SELECT key, value FROM system_settings')
        rows = await cursor.fetchall()
        settings = {
            'max_articles_per_feed': 50,
            'feed_refresh_interval_hours': 1.0,
            'target_language': 'zh-TW',
            'deepseek_enabled': bool(os.getenv('DEEPSEEK_API_KEY')),
            'deepseek_api_key': os.getenv('DEEPSEEK_API_KEY', ''),
            'deepl_enabled': bool(os.getenv('DEEPL_API_KEY')),
            'deepl_api_key': os.getenv('DEEPL_API_KEY', ''),
            'flaresolverr_enabled': bool(os.getenv('FLARESOLVERR_URL')),
            'flaresolverr_url': os.getenv('FLARESOLVERR_URL', 'http://localhost:8191/v1'),
            'image_max_dimension': 1200,
            'image_jpeg_quality': 70,
            'ai_enabled': True,
        }
        for row in rows:
            k, v = row['key'], row['value']
            if k in ('max_articles_per_feed', 'image_max_dimension', 'image_jpeg_quality'):
                try: settings[k] = int(v)
                except ValueError: pass
            elif k == 'feed_refresh_interval_hours':
                try: settings[k] = float(v)
                except ValueError: pass
            elif k in ('deepseek_enabled', 'deepl_enabled', 'flaresolverr_enabled', 'ai_enabled'):
                settings[k] = v.lower() in ('true', '1', 'yes')
            elif k in ('target_language', 'deepseek_api_key', 'deepl_api_key', 'flaresolverr_url'):
                settings[k] = v
        self._cached_system_settings = settings
        return settings

    def get_system_settings_sync(self) -> dict:
        if hasattr(self, '_cached_system_settings') and self._cached_system_settings:
            return self._cached_system_settings
        return {
            'max_articles_per_feed': 50,
            'feed_refresh_interval_hours': 1.0,
            'target_language': 'zh-TW',
            'deepseek_enabled': bool(os.getenv('DEEPSEEK_API_KEY')),
            'deepseek_api_key': os.getenv('DEEPSEEK_API_KEY', ''),
            'deepl_enabled': bool(os.getenv('DEEPL_API_KEY')),
            'deepl_api_key': os.getenv('DEEPL_API_KEY', ''),
            'flaresolverr_enabled': bool(os.getenv('FLARESOLVERR_URL')),
            'flaresolverr_url': os.getenv('FLARESOLVERR_URL', 'http://localhost:8191/v1'),
            'image_max_dimension': 1200,
            'image_jpeg_quality': 70,
            'ai_enabled': True,
        }

    async def update_system_settings(self, settings_dict: dict):
        db = await self._get_db()
        for k, v in settings_dict.items():
            val_str = 'true' if v is True else ('false' if v is False else str(v))
            await db.execute('INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)', (k, val_str))
        await db.commit()
        await self.get_system_settings()

    async def set_focal_point(self, image_hash: str, focal_x: int, focal_y: int):
        db = await self._get_db()
        await db.execute(
            'INSERT INTO image_focal_points (image_hash, focal_x, focal_y) '
            'VALUES (?, ?, ?) '
            'ON CONFLICT(image_hash) DO UPDATE SET '
            'focal_x = excluded.focal_x, focal_y = excluded.focal_y',
            (image_hash, int(focal_x), int(focal_y)),
        )
        await db.commit()

    async def get_focal_points(self, image_hashes) -> dict:
        """Bulk lookup. Missing hashes are simply absent — callers default to 50/50."""
        unique = list({h for h in (image_hashes or []) if h})
        if not unique:
            return {}
        db = await self._get_db()
        found = {}
        # SQLite caps host parameters near 999, and a 200-article page can carry
        # more image hashes than that once feeds are dense.
        for start in range(0, len(unique), 400):
            chunk = unique[start:start + 400]
            placeholders = ','.join('?' * len(chunk))
            cursor = await db.execute(
                'SELECT image_hash, focal_x, focal_y FROM image_focal_points '
                f'WHERE image_hash IN ({placeholders})',
                chunk,
            )
            for row in await cursor.fetchall():
                found[row['image_hash']] = (row['focal_x'], row['focal_y'])
        return found

    async def delete_focal_points(self, image_hashes) -> int:
        unique = list({h for h in (image_hashes or []) if h})
        if not unique:
            return 0
        db = await self._get_db()
        removed = 0
        for start in range(0, len(unique), 400):
            chunk = unique[start:start + 400]
            placeholders = ','.join('?' * len(chunk))
            cursor = await db.execute(
                f'DELETE FROM image_focal_points WHERE image_hash IN ({placeholders})',
                chunk,
            )
            removed += cursor.rowcount or 0
        await db.commit()
        return removed

    # --- Ranking & Events ---

    async def backfill_event_labels(self) -> int:
        """Copy labels onto events recorded before their article was tagged.

        Correlated subqueries rather than UPDATE...FROM so this does not depend on the
        SQLite version shipped on the Pi.
        """
        db = await self._get_db()
        cursor = await db.execute("""
            UPDATE user_article_events
            SET primary_topic = (SELECT a.primary_topic FROM feed_articles a
                                 WHERE a.id = user_article_events.article_id),
                secondary_topics = (SELECT a.secondary_topics FROM feed_articles a
                                    WHERE a.id = user_article_events.article_id),
                region = (SELECT a.region FROM feed_articles a
                          WHERE a.id = user_article_events.article_id),
                article_type = (SELECT a.article_type FROM feed_articles a
                                WHERE a.id = user_article_events.article_id)
            WHERE primary_topic IS NULL
              AND EXISTS (SELECT 1 FROM feed_articles a
                          WHERE a.id = user_article_events.article_id
                            AND a.primary_topic IS NOT NULL)
        """)
        await db.commit()
        return cursor.rowcount

    async def get_article_exposure(self, article_ids: list[int]) -> dict[int, tuple[int, str]]:
        """Bulk lookup. Missing IDs are simply omitted — callers treat as count 0."""
        unique = list({int(aid) for aid in (article_ids or []) if aid is not None})
        if not unique:
            return {}
        db = await self._get_db()
        found = {}
        for start in range(0, len(unique), 400):
            chunk = unique[start:start + 400]
            placeholders = ','.join('?' * len(chunk))
            cursor = await db.execute(
                'SELECT article_id, count, last_seen_at FROM article_impressions '
                f'WHERE article_id IN ({placeholders})',
                chunk,
            )
            for row in await cursor.fetchall():
                found[row['article_id']] = (row['count'], row['last_seen_at'])
        return found

    async def insert_events(self, events: list[dict]) -> int:
        if not events:
            return 0
        db = await self._get_db()
        rows = []
        for ev in events:
            sec = ev.get("secondary_topics")
            if isinstance(sec, (list, tuple)):
                sec = json.dumps(sec)
            rows.append((
                ev.get("article_id"),
                ev.get("source_id"),
                ev.get("source_name"),
                ev["event_type"],
                ev.get("primary_topic"),
                sec,
                ev.get("region"),
                ev.get("article_type"),
                ev.get("dwell_seconds"),
                ev.get("created_at"),
            ))
        cursor = await db.executemany("""
            INSERT INTO user_article_events (
                article_id, source_id, source_name, event_type,
                primary_topic, secondary_topics, region, article_type,
                dwell_seconds, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
        """, rows)

        try:
            impression_articles = [
                (ev["article_id"],)
                for ev in events
                if ev.get("event_type") == "impression" and ev.get("article_id") is not None
            ]
            if impression_articles:
                await db.executemany("""
                    INSERT INTO article_impressions (article_id, count, last_seen_at)
                    VALUES (?, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(article_id) DO UPDATE
                        SET count = count + 1, last_seen_at = CURRENT_TIMESTAMP
                """, impression_articles)
        except Exception as e:
            logger.warning(f"Failed to upsert article impressions: {e}")

        await db.commit()
        return cursor.rowcount

    @staticmethod
    def _vote_labels(labels: dict) -> tuple:
        sec = labels.get("secondary_topics")
        if isinstance(sec, (list, tuple)):
            sec = json.dumps(list(sec))
        return (
            labels.get("primary_topic"),
            sec,
            labels.get("region"),
            labels.get("article_type"),
        )

    @staticmethod
    def _parse_vote_row(row) -> dict:
        d = dict(row)
        raw = d.get("secondary_topics")
        if isinstance(raw, str) and raw:
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = [raw]
            d["secondary_topics"] = parsed if isinstance(parsed, list) else [parsed]
        else:
            d["secondary_topics"] = []
        return d

    async def upsert_article_vote(self, article_id: int, vote: str, labels: dict) -> None:
        db = await self._get_db()
        primary, sec, region, art_type = self._vote_labels(labels or {})
        await db.execute("""
            INSERT INTO article_votes (
                article_id, vote, primary_topic, secondary_topics, region, article_type
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET
                vote = excluded.vote,
                primary_topic = COALESCE(excluded.primary_topic, article_votes.primary_topic),
                secondary_topics = COALESCE(excluded.secondary_topics, article_votes.secondary_topics),
                region = COALESCE(excluded.region, article_votes.region),
                article_type = COALESCE(excluded.article_type, article_votes.article_type),
                updated_at = CURRENT_TIMESTAMP
        """, (article_id, vote, primary, sec, region, art_type))
        await db.commit()

    async def delete_article_vote(self, article_id: int) -> None:
        db = await self._get_db()
        await db.execute("DELETE FROM article_votes WHERE article_id = ?", (article_id,))
        await db.commit()

    async def get_article_votes(self) -> list[dict]:
        db = await self._get_db()
        cursor = await db.execute("SELECT * FROM article_votes")
        return [self._parse_vote_row(r) for r in await cursor.fetchall()]

    async def get_article_vote(self, article_id: int) -> dict | None:
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM article_votes WHERE article_id = ?", (article_id,)
        )
        row = await cursor.fetchone()
        return self._parse_vote_row(row) if row else None

    async def get_events(self, event_type: str = None, since: str = None) -> list[dict]:
        db = await self._get_db()
        query = "SELECT * FROM user_article_events WHERE 1=1"
        params = []
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        if since:
            query += " AND created_at >= ?"
            params.append(since)
        query += " ORDER BY created_at ASC"
        cursor = await db.execute(query, params)
        return [dict(r) for r in await cursor.fetchall()]

    BEHAVIOURAL_EVENT_TYPES = ("open", "hover", "skip", "read_no_vote")

    async def get_behavioural_signals(self, since: str = None) -> list[dict]:
        db = await self._get_db()
        placeholders = ",".join("?" * len(self.BEHAVIOURAL_EVENT_TYPES))
        query = f"""
            SELECT event_type, dwell_seconds, created_at, primary_topic,
                   secondary_topics, region, article_type
            FROM user_article_events
            WHERE event_type IN ({placeholders})
        """
        params = list(self.BEHAVIOURAL_EVENT_TYPES)
        if since:
            query += " AND created_at >= ?"
            params.append(since)
        cursor = await db.execute(query, params)
        return [dict(r) for r in await cursor.fetchall()]

    async def get_secondary_cooccurrence(self) -> dict[str, dict[str, int]]:
        """Canonical secondary label -> {primary_topic: article count}.

        Feeds the prior in vote_weights._secondary_priors, which is how a label with no
        votes of its own still carries taste. Cached by get_effective_weights; the shape
        only moves when articles are tagged.
        """
        from .services.labels import canonical_label
        db = await self._get_db()
        cursor = await db.execute("""
            SELECT primary_topic, secondary_topics
            FROM feed_articles
            WHERE secondary_topics IS NOT NULL AND secondary_topics != ''
              AND primary_topic IS NOT NULL AND primary_topic != 'unknown'
        """)
        out: dict[str, dict[str, int]] = {}
        for row in await cursor.fetchall():
            raw = row["secondary_topics"]
            try:
                labels = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                labels = [raw]
            if not isinstance(labels, list):
                continue
            topic = row["primary_topic"]
            seen = set()
            for label in labels:
                key = canonical_label(label)
                if not key or key in seen:
                    continue
                seen.add(key)
                bucket = out.setdefault(key, {})
                bucket[topic] = bucket.get(topic, 0) + 1
        return out

    async def get_secondary_display_names(self) -> dict[str, str]:
        """Canonical label -> the spelling that appears most often, for display.

        The canonical key is lowercase and occasionally ungrammatical ("tv sery"), so it
        must never reach the UI.
        """
        from .services.labels import canonical_label
        db = await self._get_db()
        cursor = await db.execute("""
            SELECT secondary_topics FROM feed_articles
            WHERE secondary_topics IS NOT NULL AND secondary_topics != ''
        """)
        counts: dict[str, dict[str, int]] = {}
        for row in await cursor.fetchall():
            raw = row["secondary_topics"]
            try:
                labels = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                labels = [raw]
            if not isinstance(labels, list):
                continue
            for label in labels:
                if not isinstance(label, str):
                    continue
                key = canonical_label(label)
                if not key:
                    continue
                bucket = counts.setdefault(key, {})
                bucket[label] = bucket.get(label, 0) + 1
        return {key: max(spellings.items(), key=lambda kv: (kv[1], kv[0]))[0]
                for key, spellings in counts.items()}

    async def get_untagged_articles(self, limit: int = 50) -> list[dict]:
        db = await self._get_db()
        cursor = await db.execute("""
            SELECT a.id,
                   COALESCE(a.original_title, a.title) AS title,
                   COALESCE(a.original_excerpt, a.content) AS body,
                   a.source_id, f.name AS feed_name
            FROM feed_articles a
            JOIN feed_sources f ON f.id = a.source_id
            WHERE a.topic_extracted_at IS NULL AND a.parse_status = 'success'
            ORDER BY a.id DESC
            LIMIT ?
        """, (limit,))
        return [dict(r) for r in await cursor.fetchall()]

    async def save_article_tags(self, article_id: int, tags: dict):
        db = await self._get_db()
        sec = tags.get("secondary", [])
        if isinstance(sec, (list, tuple)):
            sec = json.dumps(sec)
        await db.execute("""
            UPDATE feed_articles
            SET primary_topic = ?, secondary_topics = ?, region = ?,
                article_type = ?, tag_confidence = ?, ai_summary = ?,
                topic_extracted_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            tags.get("primary"),
            sec,
            tags.get("region"),
            tags.get("type"),
            tags.get("confidence"),
            tags.get("ai_summary"),
            article_id,
        ))
        await db.commit()

    async def get_tag_impression_counts(self, since: str = None) -> dict[str, int]:
        db = await self._get_db()
        query = """
            SELECT primary_topic, COUNT(*) as cnt
            FROM user_article_events
            WHERE event_type = 'impression' AND primary_topic IS NOT NULL
        """
        params = []
        if since:
            query += " AND created_at >= ?"
            params.append(since)
        query += " GROUP BY primary_topic"
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return {r["primary_topic"]: r["cnt"] for r in rows}

    async def get_feed_age_percentiles(self, source_ids: list[int] = None) -> dict[int, dict[int, float]]:
        db = await self._get_db()
        if source_ids:
            placeholders = ",".join("?" * len(source_ids))
            cursor = await db.execute(f"""
                SELECT id, source_id, pub_date
                FROM feed_articles
                WHERE source_id IN ({placeholders})
                ORDER BY source_id, pub_date DESC, id DESC
            """, source_ids)
        else:
            cursor = await db.execute("""
                SELECT id, source_id, pub_date
                FROM feed_articles
                ORDER BY source_id, pub_date DESC, id DESC
            """)
        rows = await cursor.fetchall()
        by_source: dict[int, list[int]] = {}
        for r in rows:
            by_source.setdefault(r["source_id"], []).append(r["id"])
        
        result: dict[int, dict[int, float]] = {}
        for sid, art_ids in by_source.items():
            total = len(art_ids)
            perc_map = {}
            denom = max(1, total - 1)
            for idx, aid in enumerate(art_ids):
                perc_map[aid] = idx / denom
            result[sid] = perc_map
        return result


def close_all_sync():
    """Stop every live connection, from a synchronous context.

    Call once before a non-app process exits — pytest sessions and ad-hoc scripts.
    Closing only the module singleton is not enough: a `Database` held in a module-level
    test variable is never garbage collected, so its worker thread survives to
    `threading._shutdown()` and hangs the process there. See Database.close_sync.
    """
    for instance in list(Database._instances):
        instance.close_sync()
    # Connections whose Database wrapper was already garbage collected. Those are
    # unreachable through _instances but their worker threads are still running.
    for conn in list(Database._live_connections):
        Database._stop_conn(conn)


db = Database()
