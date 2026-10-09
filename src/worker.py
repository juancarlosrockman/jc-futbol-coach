"""Cloudflare entry point for JC Futbol Coach."""
import os
from urllib.parse import urlsplit
from workers import WorkerEntrypoint, wsgi

os.environ["CLOUDFLARE_WORKERS"] = "1"
os.environ.setdefault("SECRET_KEY", "__CLOUDFLARE_RUNTIME_SECRET__")

_app = None
_db_initialized = False


def _apply_bindings(env):
    """Copy Cloudflare bindings into the environment before app code uses them."""
    for key in ("SECRET_KEY", "COACH_USER", "COACH_PASSWORD",
                "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"):
        value = getattr(env, key, None)
        if value:
            os.environ[key] = str(value)

    hyperdrive = getattr(env, "HYPERDRIVE", None)
    connection_string = getattr(hyperdrive, "connectionString", None) if hyperdrive else None
    if connection_string:
        os.environ["DATABASE_URL"] = str(connection_string)


def _get_app(initialize_db=False):
    """Import Flask cheaply; initialize/migrate the database only for DB-backed routes."""
    global _app, _db_initialized
    if _app is None:
        from app import app as flask_app
        _app = flask_app
        secret = os.environ.get("SECRET_KEY") or "__CLOUDFLARE_RUNTIME_SECRET__"
        _app.secret_key = secret
        _app.config["SECRET_KEY"] = secret

    if initialize_db and not _db_initialized:
        # This migration includes schema changes and data repairs. Do not run it
        # for the public landing page or static assets; it is needed only before
        # a request that actually uses the database.
        from app import init_db
        init_db()
        _db_initialized = True
    return _app

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        _apply_bindings(self.env)
        path = urlsplit(request.url).path
        if path == "/manifest.webmanifest":
            return await self.env.ASSETS.fetch("https://assets.local/manifest.webmanifest")
        if path.startswith("/static/"):
            asset_path = path[len("/static/"):]
            if asset_path and ".." not in asset_path.split("/"):
                return await self.env.ASSETS.fetch("https://assets.local/" + asset_path)
        # The public landing page can render without touching PostgreSQL.
        # Database migrations are deferred until a dynamic, non-home request.
        initialize_db = path != "/"
        app = _get_app(initialize_db=initialize_db)
        return await wsgi.fetch(app, request, self.env)
