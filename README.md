# CRAFT

A self-hosted generative studio: chat, image generation, video generation and
music, behind one interface. Users bring their own API key, and every model the
upstream provider publishes shows up automatically.

## What it does

- **Every model, without a hard-coded list.** The catalogue is read from the
  provider's published documentation when you open a page. Picking a model reads
  that model's OpenAPI schema and builds its form — so a model added upstream
  tomorrow appears with the right fields and no code change.
- **Chat** against any chat model, whichever protocol it speaks (OpenAI,
  Anthropic Messages, OpenAI Responses, Gemini), with optional image attachments
  and full history.
- **Image, video and music generation** with an optional reference image, task
  polling, and durable storage of every result (the provider deletes its own
  copies after 14 days).
- **Notifications**: a desktop notification when a chat reply lands while you
  are looking elsewhere, and an optional Telegram bridge that pushes every
  completed response to your chat.
- **Installable**: a PWA that runs standalone on a phone, responsive down to
  small screens, dark glassmorphism throughout.

## Getting started

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

cp .env.example .env      # then edit it, see below
./.venv/bin/python manage.py migrate
./.venv/bin/python manage.py runserver
```

Open http://127.0.0.1:8000, register an account, and paste an API key on the
Settings page. Create one at [kie.ai/api-key](https://kie.ai/api-key).

Two settings matter before anyone else uses your instance:

| Variable | Why |
| --- | --- |
| `DJANGO_SECRET_KEY` | A stable key; one is generated per process otherwise, invalidating sessions on restart. |
| `CREDENTIALS_ENCRYPTION_KEY` | Encrypts stored API keys. Without it a per-process key is used and stored credentials become unreadable after a restart. |

Generate the encryption key with:

```bash
./.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Out of the box the app uses SQLite and the local filesystem, which is what you
want for a checkout. Both are swapped out by configuration alone — see
**Deployment**.

## Layout

```
apps/
  common/      the provider HTTP client, HTML sanitising, Telegram, encryption
  accounts/    auth, per-user settings and credentials
  catalog/     the model catalogue, parsed from the provider's documentation
  chat/        conversations, messages, the four chat protocols
  generation/  image/video/music tasks and their stored results
config/        Django settings and the URL map
templates/     pages, HTML fragments, PWA manifest and service worker
static/        the design system and the page scripts
scripts/       catalogue snapshot builder, container entrypoint
```

Each app follows the same shape: `models/`, `views/`, `serializers/`,
`filters/`, `services/`. Views come in two flavours — page views that render
templates and HTML fragments, and DRF ViewSets under `/api/`.

## How the catalogue works

The provider publishes an `llms.txt` index plus one Markdown page per model
containing that model's OpenAPI specification.

1. Opening a category page fetches the index — one small request — and lists
   that category's models.
2. Selecting a model fetches its page and parses the schema into a form.
3. Both are cached (`CATALOG_INDEX_TTL`, `CATALOG_SPEC_TTL`), and
   `apps/catalog/data/catalog.json` is a bundled snapshot used if the
   documentation cannot be reached.

Refresh the snapshot with `python scripts/build_catalog.py`, or warm the caches
with `python manage.py refresh_catalog`.

Models are reached through one of three shapes, all detected from the docs:

- `jobs` — the unified `/api/v1/jobs/createTask` endpoint (most models)
- `task` — the dedicated product APIs (Suno, Veo, Runway, 4o Image, Flux Kontext…)
- `chat_*` — per-model chat endpoints, one adapter per vendor protocol

## Rendering model output safely

Backend responses are HTML fragments that the page inserts directly, and chat
replies are asked to return HTML in the site's own vocabulary (see
`CHAT_SYSTEM_PROMPT` in `apps/common/services/html.py`).

Model output is treated as untrusted: every fragment passes through an
allow-list sanitiser before it is stored or sent, which strips scripts, event
handlers, inline styles, embedded frames and `javascript:` URLs. A model that
returns plain text or a fenced block is normalised into HTML instead.

## Notifications

Two independent channels, both per-user on the Settings page:

- **Browser** — the chat page raises a desktop notification when a reply
  arrives, but only while the tab is open and *not* in front of you, so an
  answer you are already reading stays quiet. The account preference is one half
  of the switch; the browser's own permission grant is the other, and that is
  per browser and per device, which is why Settings asks for it separately.
- **Telegram** — connect a bot and every completed chat reply and generation is
  pushed to your chat. This one works with no browser open at all.

## Deployment

### Database

SQLite unless PostgreSQL is configured, through either a single `DATABASE_URL`:

```
DATABASE_URL=postgresql://craft:secret@postgres:5432/craft
```

or discrete variables (`DJANGO_DB_HOST`, `DJANGO_DB_NAME`, `DJANGO_DB_USER`,
`DJANGO_DB_PASSWORD`, `DJANGO_DB_PORT`), which are easier to point at an
existing container. `DATABASE_URL` wins if both are set. Percent-encode any
reserved characters in a URL password.

### Media storage

Generated files are downloaded into the app's own storage because the provider
deletes its copies after 14 days. Setting `AWS_STORAGE_BUCKET_NAME` moves that
storage from the local filesystem to an S3 bucket; `AWS_S3_ENDPOINT_URL` points
it at MinIO or any other S3-compatible server instead of Amazon.

```
AWS_STORAGE_BUCKET_NAME=craft-media
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_ENDPOINT_URL=http://minio:9000
```

URLs are signed by default, so a private bucket works with no further setup.
Set `AWS_QUERYSTRING_AUTH=false` when the bucket, or a CDN in front of it, is
meant to be publicly readable. Create the bucket first — the app does not.

### Containers

`docker-compose.prod.yml` runs the app and nothing else: PostgreSQL and MinIO
are expected to be containers you already run, so the app joins their network
and addresses them by container name.

```bash
cp .env.prod.example .env.prod        # then fill it in
docker network ls                     # find the network Postgres and MinIO are on

CRAFT_NETWORK=infra docker compose -f docker-compose.prod.yml up -d --build
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `CRAFT_NETWORK` | `infra` | The existing external Docker network to join. |
| `CRAFT_BIND` | `127.0.0.1` | Interface the port is published on. `0.0.0.0` to expose it on the host. |
| `CRAFT_PORT` | `8000` | Host port. |

The image runs `collectstatic` at build time and serves the hashed assets
through WhiteNoise, so no separate web server is needed for static files. On
start the entrypoint waits for the database, applies migrations, then runs
gunicorn. `/healthz` answers the container healthcheck without touching the
database.

Two details worth knowing if you put your own proxy in front:

- The compose file sets `DJANGO_TRUST_PROXY_HEADERS=true`, which tells Django to
  read the original scheme from `X-Forwarded-Proto`. Without it, the
  HTTPS redirect loops forever behind a proxy that terminates TLS.
- Model calls are synchronous and can legitimately run for minutes, so the
  gunicorn worker timeout defaults to 180s — above `KIE_REQUEST_TIMEOUT`. Raise
  both together, never just one.

## API

The same data is available as JSON for anything that would rather not scrape
HTML. Session or HTTP Basic authentication; everything is scoped to the caller.

```
GET  /api/models/?category=image&search=banana
GET  /api/models/{slug}/            # full request schema
GET  /api/conversations/
POST /api/conversations/{id}/send/
GET  /api/generations/?status=running
POST /api/generations/              # {"model": "<slug>", ...model fields}
POST /api/generations/{id}/refresh/
GET  /api/settings/me/
```

## Tests

```bash
./.venv/bin/python manage.py test
```

Covers documentation parsing, the four chat protocols, request building and
result parsing, the HTML sanitiser, credential encryption, the database
configuration, and the page and API endpoints.
