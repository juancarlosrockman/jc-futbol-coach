import os

from workers import WorkerEntrypoint, wsgi


_app = None


def _configure_from_cloudflare(env):
    """Expose Cloudflare bindings/secrets through os.environ for the existing Flask app."""
    os.environ["DATABASE_URL"] = env.HYPERDRIVE.connectionString

    for _name in (
        "SECRET_KEY",
        "COACH_USER",
        "COACH_PASSWORD",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REDIRECT_URI",
    ):
        try:
            _value = getattr(env, _name)
        except AttributeError:
            _value = None
        if _value:
            os.environ[_name] = str(_value)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        global _app

        # Configure the environment before importing Flask because app.py
        # validates SECRET_KEY and initializes the database at import time.
        _configure_from_cloudflare(self.env)

        if _app is None:
            from app import app
            _app = app

        return await wsgi.fetch(_app, request, self.env)
