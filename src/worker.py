import os

from workers import WorkerEntrypoint, Response, wsgi

# Tell the Flask app that it is being loaded inside Cloudflare Workers.
# This must be set before importing app.py so deployment-time snapshot creation
# does not require the runtime SECRET_KEY and does not run init_db().
os.environ["CLOUDFLARE_WORKERS"] = "1"


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


# Import at deployment time so Cloudflare's Python cold-start snapshot contains
# Flask and the application module. Database migrations are disabled in
# Cloudflare mode because the existing Supabase schema is already initialized.
from app import app as _app


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        if request.url.endswith("/health"):
            return Response(
                "ok",
                status=200,
                headers={"content-type": "text/plain; charset=utf-8"}
            )
        _configure_from_cloudflare(self.env)
        # The real Cloudflare secret must be applied before Flask handles the request.
        _app.secret_key = os.environ["SECRET_KEY"]
        return await wsgi.fetch(_app, request, self.env)
