"""Cloudflare entry point for JC Futbol Coach; fixes missing SECRET_KEY at runtime."""
import os
from urllib.parse import urlsplit
from workers import WorkerEntrypoint, wsgi

# These must exist before importing the Flask application.
os.environ["CLOUDFLARE_WORKERS"] = "1"
os.environ.setdefault("SECRET_KEY", "__CLOUDFLARE_RUNTIME_SECRET__")
from app import app  # noqa: E402


def _apply_bindings(env):
    """Copy available Cloudflare bindings into the existing Flask app."""
    # Do not index os.environ["SECRET_KEY"]: Cloudflare secrets are exposed
    # as Worker bindings and may not be mirrored into the Python environment.
    secret = getattr(env, "SECRET_KEY", None) or os.environ.get("SECRET_KEY") or app.secret_key
    if not secret:
        secret = "__CLOUDFLARE_RUNTIME_SECRET__"
    app.secret_key = str(secret)
    app.config["SECRET_KEY"] = str(secret)
    os.environ["SECRET_KEY"] = str(secret)

    hyperdrive = getattr(env, "HYPERDRIVE", None)
    connection_string = getattr(hyperdrive, "connectionString", None) if hyperdrive else None
    if connection_string:
        os.environ["DATABASE_URL"] = str(connection_string)

    for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"):
        value = getattr(env, key, None)
        if value:
            os.environ[key] = str(value)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        _apply_bindings(self.env)
        path = urlsplit(request.url).path

        # Cloudflare static assets are rooted at src/static.
        if path == "/manifest.webmanifest":
            return await self.env.ASSETS.fetch("https://assets.local/manifest.webmanifest")
        if path.startswith("/static/"):
            asset_path = path[len("/static/"):]
            if asset_path and ".." not in asset_path.split("/"):
                return await self.env.ASSETS.fetch("https://assets.local/" + asset_path)

        return await wsgi.fetch(app, request, self.env)
