"""Cloudflare entry point for the existing JC Fútbol Coach Flask app.

This replaces the temporary test response with Cloudflare's supported WSGI
adapter. It keeps src/app.py, templates, static files and the existing routes.
"""
import os

from workers import WorkerEntrypoint, wsgi

# Must be set before importing app.py: the existing app intentionally skips
# init_db() on Cloudflare because schema/data must not be modified at startup.
os.environ["CLOUDFLARE_WORKERS"] = "1"
os.environ.setdefault("SECRET_KEY", "__CLOUDFLARE_RUNTIME_SECRET__")

from app import app  # noqa: E402


def _apply_bindings(env):
    """Expose Cloudflare bindings to the legacy Flask app for this request."""
    secret = getattr(env, "SECRET_KEY", None)
    if secret:
        # Flask reads app.secret_key for session signing, not just os.environ.
        app.secret_key = str(secret)
        app.config["SECRET_KEY"] = str(secret)
        os.environ["SECRET_KEY"] = str(secret)

    hyperdrive = getattr(env, "HYPERDRIVE", None)
    connection_string = (
        getattr(hyperdrive, "connectionString", None) if hyperdrive else None
    )
    if connection_string:
        os.environ["DATABASE_URL"] = str(connection_string)

    for key in (
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REDIRECT_URI",
    ):
        value = getattr(env, key, None)
        if value:
            os.environ[key] = str(value)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        _apply_bindings(self.env)
        return await wsgi.fetch(app, request, self.env)
