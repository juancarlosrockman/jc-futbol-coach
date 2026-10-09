"""Small pg8000 adapter preserving the app's synchronous connection API."""
from urllib.parse import urlsplit, unquote
import pg8000.dbapi


class DictCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def _row(self, row):
        if row is None:
            return None
        return {col[0]: row[i] for i, col in enumerate(self._cursor.description or ())}

    def fetchone(self):
        return self._row(self._cursor.fetchone())

    def fetchall(self):
        return [self._row(row) for row in self._cursor.fetchall()]

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description


class Connection:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        cursor = self._conn.cursor()
        cursor.execute(sql, params or ())
        return DictCursor(cursor)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()


def connect(database_url):
    """Connect using the PostgreSQL URL exposed by Cloudflare Hyperdrive."""
    parsed = urlsplit(database_url)
    if parsed.scheme not in ("postgres", "postgresql"):
        raise RuntimeError("DATABASE_URL debe ser una URL PostgreSQL.")
    database = unquote(parsed.path.lstrip("/"))
    if not parsed.hostname or not database or not parsed.username:
        raise RuntimeError("DATABASE_URL no contiene host, usuario o base de datos.")
    raw = pg8000.dbapi.connect(
        user=unquote(parsed.username),
        password=unquote(parsed.password or ""),
        host=parsed.hostname,
        port=parsed.port or 5432,
        database=database,
        ssl_context=None,
    )
    return Connection(raw)
