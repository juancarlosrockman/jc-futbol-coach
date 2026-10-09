"""Cloudflare entry point for JC Futbol Coach with explicit static asset serving."""
import os
from urllib.parse import urlsplit

from workers import WorkerEntrypoint, wsgi

os.environ["CLOUDFLARE_WORKERS"] = "1"
os.environ.setdefault("SECRET_KEY", "__CLOUDFLARE_RUNTIME_SECRET__")

from app import app  # noqa: E402


def _apply_bindings(env):
    secret = getattr(env, "SECRET_KEY", None)
    if secret:
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

        # Cloudflare's ASSETS directory is src/static, so map Flask's
        # /static/<file> URLs to the asset root, and expose the manifest
        # at the existing Flask endpoint /manifest.webmanifest.
        if path == "/manifest.webmanifest":
            return await self.env.ASSETS.fetch("https://assets.local/manifest.webmanifest")
        if path.startswith("/static/"):
            asset_path = path[len("/static/"):]
            if asset_path and ".." not in asset_path.split("/"):
                return await self.env.ASSETS.fetch("https://assets.local/" + asset_path)

        return await wsgi.fetch(app, request, self.env)
