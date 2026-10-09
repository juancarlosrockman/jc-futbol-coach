"""Cloudflare entry point for JC Futbol Coach."""
import os
from urllib.parse import urlsplit
from workers import WorkerEntrypoint, wsgi

os.environ["CLOUDFLARE_WORKERS"] = "1"
os.environ.setdefault("SECRET_KEY", "__CLOUDFLARE_RUNTIME_SECRET__")

_app = None
_db_initialized = False


def _apply_bindings(env):
    for key in ("SECRET_KEY", "COACH_USER", "COACH_PASSWORD",
                "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"):
        value = getattr(env, key, None)
        if value:
            os.environ[key] = str(value)

    hyperdrive = getattr(env, "HYPERDRIVE", None)
    connection_string = getattr(hyperdrive, "connectionString", None) if hyperdrive else None
    if connection_string:
        os.environ["DATABASE_URL"] = str(connection_string)


def _get_app():
    global _app, _db_initialized
    if _app is None:
        from app import app as flask_app, init_db
        _app = flask_app
        secret = os.environ.get("SECRET_KEY") or "__CLOUDFLARE_RUNTIME_SECRET__"
        _app.secret_key = secret
        _app.config["SECRET_KEY"] = secret
        init_db()
        _db_initialized = True
    elif not _db_initialized:
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
        app = _get_app()
        return await wsgi.fetch(app, request, self.env)
