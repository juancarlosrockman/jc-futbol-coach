"""Cloudflare entry point for JC Futbol Coach."""
import asyncio
import os
from urllib.parse import quote, urlsplit
from workers import WorkerEntrypoint, wsgi

os.environ["CLOUDFLARE_WORKERS"] = "1"
os.environ.setdefault("SECRET_KEY", "__CLOUDFLARE_RUNTIME_SECRET__")

_app = None
_db_initialized = False
_db_init_lock = asyncio.Lock()


def _apply_bindings(env):
    """Copy Cloudflare bindings into the environment before app code uses them."""
    for key in ("SECRET_KEY", "COACH_USER", "COACH_PASSWORD",
                "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"):
        value = getattr(env, key, None)
        if value:
            os.environ[key] = str(value)

    hyperdrive = getattr(env, "HYPERDRIVE", None)
    if hyperdrive:
        # Python Workers exposes Hyperdrive connection fields (host, port,
        # user, password, database). Some environments may also expose the
        # JavaScript-style connectionString property, so prefer it if present.
        connection_string = getattr(hyperdrive, "connectionString", None)
        if connection_string:
            os.environ["DATABASE_URL"] = str(connection_string)
        else:
            host = getattr(hyperdrive, "host", None)
            port = getattr(hyperdrive, "port", None) or 5432
            user = getattr(hyperdrive, "user", None)
            password = getattr(hyperdrive, "password", None)
            database = getattr(hyperdrive, "database", None)
            if host and user and database and password is not None:
                os.environ["DATABASE_URL"] = (
                    f"postgresql://{quote(str(user), safe='')}:{quote(str(password), safe='')}"
                    f"@{host}:{int(port)}/{quote(str(database), safe='')}"
                )


def _get_app():
    """Import Flask without opening PostgreSQL or running migrations."""
    global _app
    if _app is None:
        from app import app as flask_app
        _app = flask_app
        secret = os.environ.get("SECRET_KEY") or "__CLOUDFLARE_RUNTIME_SECRET__"
        _app.secret_key = secret
        _app.config["SECRET_KEY"] = secret
    return _app


async def _ensure_db_initialized():
    """Run schema setup once per isolate, serializing concurrent cold requests."""
    global _db_initialized
    if _db_initialized:
        return
    async with _db_init_lock:
        if _db_initialized:
            return
        # Do not run migrations for the public landing page or static assets.
        # The lock prevents parallel cold requests from racing through the
        # startup migrations and data-repair queries at the same time.
        from app import init_db
        init_db()
        _db_initialized = True

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        _apply_bindings(self.env)
        path = urlsplit(request.url).path
        if path == "/manifest.webmanifest":
            return await self.env.ASSETS.fetch("https://assets.local/manifest.webmanifest")
        if path == "/sw.js":
            return await self.env.ASSETS.fetch("https://assets.local/sw.js")
        if path.startswith("/static/"):
            asset_path = path[len("/static/"):]
            if asset_path and ".." not in asset_path.split("/"):
                return await self.env.ASSETS.fetch("https://assets.local/" + asset_path)
        # The public landing page can render without touching PostgreSQL.
        # Database migrations are deferred until a dynamic, non-home request.
        initialize_db = path != "/"
        app = _get_app()
        if initialize_db:
            await _ensure_db_initialized()
        return await wsgi.fetch(app, request, self.env)
