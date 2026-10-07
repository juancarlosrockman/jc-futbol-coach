import os

from workers import WorkerEntrypoint, wsgi, env


# Cloudflare bindings are exposed through the Workers environment, not
# os.environ. The existing Flask application already reads its configuration
# from os.environ, so bridge the Cloudflare bindings before importing app.py.
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

from app import app


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await wsgi.fetch(app, request, self.env)
