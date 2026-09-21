# Deploy Contract Vault Backend on Render

This guide deploys the Django API (`gunicorn`), Celery worker, Celery beat, and Redis on [Render](https://render.com).

App data lives in **Firebase / Firestore**. Redis is required for Celery. SQLite is only used by Django (including `django-celery-beat`) and is **ephemeral** on Render unless you attach a disk or switch to PostgreSQL.

## What you will create

| Resource | Type | Purpose |
| --- | --- | --- |
| `contractvault-api` | Web Service | Django + Gunicorn |
| `contractvault-redis` | Key Value (Redis) | Celery broker and result backend |
| `contractvault-worker` | Background Worker | Celery worker (AI extraction, emails, status jobs) |
| `contractvault-beat` | Background Worker | Celery beat (daily reminders, hourly status updates) |

Use the **same region** for every resource (for example `Oregon`).

## Architecture notes

- Primary datastore: Firebase Auth, Firestore, Firebase Storage.
- Celery tasks: `REDIS_URL`.
- Tesseract OCR is a **fallback** when Gemini Vision fails. Render’s native Python image does not include `tesseract`. Gemini + PyMuPDF still work. To enable Tesseract, deploy with Docker (see [Optional: Docker for Tesseract](#optional-docker-for-tesseract)).
- `nixpacks.toml` is used by Nixpacks (for example Railway). Render **does not** use it for native Python services.

---

## Docker: single service (testing)

For local or Render smoke tests, run **API + Celery worker + Celery beat + Redis in one container**. This is not a production layout (one process crash can take everything down, and you cannot scale workers independently).

### Files

| File | Role |
| --- | --- |
| `Dockerfile` | Python 3.12, Tesseract, Redis, app install |
| `docker-entrypoint.sh` | migrate, collectstatic, start Redis + worker + beat + Gunicorn |
| `docker-compose.yml` | One `api` service on port 8000 |
| `.dockerignore` | Skips `venv/`, `.env`, sqlite, git |

The container starts its own Redis (`EMBEDDED_REDIS=1`). Compose sets `REDIS_URL=redis://127.0.0.1:6379/0` so a host `REDIS_URL` in `.env` is not used inside the container.

### Run locally

From the `backend` folder (Docker Desktop must be running):

```bash
docker compose up --build
```

API: `http://127.0.0.1:8000/health/`

Compose loads `backend/.env` for Firebase, Gemini, and Brevo. Keep `.env` on the host; it is not copied into the image.

Stop with `Ctrl+C`, then `docker compose down`.

Equivalent without Compose:

```bash
docker build -t contractvault-api .
docker run --rm -p 8000:8000 --env-file .env -e EMBEDDED_REDIS=1 -e REDIS_URL=redis://127.0.0.1:6379/0 -e ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0 contractvault-api
```

### Render as one Docker web service

Use this only for testing. Production should split web / worker / beat / Redis (sections below).

1. Push the repo (Root Directory `backend` if needed).
2. **New → Web Service** → connect the repo.
3. Language: **Docker**.
4. Health check path: `/health/`
5. Set env vars from [Environment variables](#5-environment-variables).
6. For in-container Redis, set:
   - `EMBEDDED_REDIS` = `1`
   - `REDIS_URL` = `redis://127.0.0.1:6379/0`
7. `ALLOWED_HOSTS` must include `your-service.onrender.com`.
8. Do **not** create separate worker, beat, or Redis instances.

Render injects `PORT`. The entrypoint binds Gunicorn to `$PORT`.

To use Render Key Value Redis instead of embedded Redis (still one web service, but external broker):

- `EMBEDDED_REDIS` = `0`
- `REDIS_URL` = the Render internal Redis URL
- Start command can stay the default `CMD` (worker and beat still run in the same container)

---

## 1. Put the code on GitHub

Render deploys from GitHub, GitLab, or Bitbucket.

1. Create a repository (backend-only, or a monorepo that includes `backend/`).
2. Do **not** commit `.env`, `venv/`, or `db.sqlite3`.
3. Push the branch you want to deploy (usually `main`).

If the Git repo root is the parent of `backend/`, set **Root Directory** to `backend` on every Render service.

---

## 2. Create a Render account and connect Git

1. Open [https://dashboard.render.com](https://dashboard.render.com) and sign in.
2. Connect your Git provider when prompted.
3. Grant access to the Contract Vault repository.

---

## 3. Create Redis (Key Value)

1. Dashboard → **New** → **Key Value**.
2. Name: `contractvault-redis`.
3. Maxmemory policy: `noeviction` (safer for Celery).
4. Create the instance and wait until it is **Available**.
5. Copy the **Internal Redis URL** (`rediss://...` or `redis://...`). Workers on Render should use the internal URL.

Celery reads this as `REDIS_URL`.

---

## 4. Create the web service

1. Dashboard → **New** → **Web Service**.
2. Select the repository (and **Root Directory** `backend` if needed).
3. Configure:

| Setting | Value |
| --- | --- |
| Name | `contractvault-api` |
| Language | Python 3 |
| Branch | `main` (or your deploy branch) |
| Region | Same as Redis |
| Instance type | Starter or higher (Free sleeps and is a poor fit for Celery) |
| Build command | `pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate --noinput` |
| Start command | `gunicorn contractvault_api.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120` |

4. **Health Check Path**: `/health/`
5. Add environment variables (next section) **before** the first deploy, or the service will boot with empty Firebase keys.
6. Click **Create Web Service**.

Optional: set **Python Version** with env var `PYTHON_VERSION` = `3.12.8` (Django 6 requires Python 3.12+). Do not rely on Render’s default if it is 3.14 unless you have tested that combination.

---

## 5. Environment variables

Add these on the **web service**, then copy the same set to the worker and beat services.

Mark secrets as **Secret**. Never paste production keys into git.

| Key | Required | Notes |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | Yes | Long random string. Generate with `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `DEBUG` | Yes | `False` |
| `ALLOWED_HOSTS` | Yes | Your Render host, comma-separated. Example: `contractvault-api.onrender.com,.onrender.com` |
| `CORS_ALLOWED_ORIGINS` | Yes | Frontend origins, **no trailing slash**. Example: `https://your-frontend.example.com` |
| `FRONTEND_URL` | Yes | Same as the production frontend origin |
| `REDIS_URL` | Yes | Internal Redis URL from step 3 |
| `FIREBASE_PROJECT_ID` | Yes | From the Firebase service account JSON |
| `FIREBASE_PRIVATE_KEY_ID` | Yes | From the JSON |
| `FIREBASE_PRIVATE_KEY` | Yes | See [Firebase private key](#firebase-private-key) |
| `FIREBASE_CLIENT_EMAIL` | Yes | From the JSON |
| `FIREBASE_CLIENT_ID` | Yes | From the JSON |
| `FIREBASE_STORAGE_BUCKET` | Yes | Example: `your-project.appspot.com` |
| `GEMINI_API_KEY` | Yes | Google AI / Gemini |
| `BREVO_API_KEY` | Yes | Transactional email |
| `BREVO_SENDER_EMAIL` | Recommended | Verified sender in Brevo |
| `BREVO_SENDER_NAME` | Optional | Default: `ContractVault AI` |
| `PYTHON_VERSION` | Recommended | `3.12.8` |

After the first successful deploy, add the Render hostname to `ALLOWED_HOSTS` if you used a placeholder. Then **redeploy**.

If you later attach a custom domain, add that hostname to `ALLOWED_HOSTS` and the HTTPS origin to `CORS_ALLOWED_ORIGINS`.

### Firebase private key

`settings.py` turns escaped newlines into real newlines:

```text
FIREBASE_PRIVATE_KEY.replace("\\n", "\n")
```

In the Render dashboard, paste the key as a **single line** with `\n` sequences, for example:

```text
-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----\n
```

Do not paste a multi-line PEM unless you confirm Firebase still initializes. Do not wrap the value in extra quotes in the dashboard.

---

## 6. Create the Celery worker

1. Dashboard → **New** → **Background Worker**.
2. Same repo, branch, root directory, and region as the web service.
3. Configure:

| Setting | Value |
| --- | --- |
| Name | `contractvault-worker` |
| Language | Python 3 |
| Build command | `pip install -r requirements.txt && python manage.py migrate --noinput` |
| Start command | `celery -A celery_app worker --loglevel=info --concurrency=2` |

4. Copy **all** environment variables from the web service (including `REDIS_URL`).
5. Create the worker.

---

## 7. Create Celery beat

Beat runs the schedules defined in `celery_app/celery.py`:

- `send-contract-reminders-daily` — 08:00 UTC
- `update-contract-statuses` — every hour

1. Dashboard → **New** → **Background Worker**.
2. Same repo, branch, root, region, and env vars.
3. Configure:

| Setting | Value |
| --- | --- |
| Name | `contractvault-beat` |
| Language | Python 3 |
| Build command | `pip install -r requirements.txt && python manage.py migrate --noinput` |
| Start command | `celery -A celery_app beat --loglevel=info` |

Use **one** beat process only. Two beat instances will duplicate scheduled tasks.

The in-code `beat_schedule` does not need `django_celery_beat`’s database scheduler. If you instead use:

```bash
celery -A celery_app beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

then web, worker, and beat must share a real database (PostgreSQL). SQLite on separate Render services is not shared.

---

## 8. Frontend configuration

Point the frontend API base URL at the web service:

```text
https://contractvault-api.onrender.com
```

No trailing slash unless your client already expects one. CORS must list the exact frontend origin (`https://...`, not `http://`).

---

## 9. Verify the deploy

1. Open `https://<your-service>.onrender.com/health/` — expect `{"status":"ok"}`.
2. Web service logs: Gunicorn started, no Firebase credential errors.
3. Worker logs: `celery@... ready`.
4. Beat logs: schedule registered for reminder and status tasks.
5. From the frontend: sign in (Firebase), then call a protected API such as `/api/dashboard/` with a valid token.

---

## 10. Ongoing deploys

Every push to the connected branch rebuilds and deploys automatically.

If a build fails, Render keeps the last good web deploy. Failed env or start commands still take the service down, so check logs under **Logs**.

Useful dashboard actions:

- **Manual Deploy** → Deploy latest commit
- Restart a single service after changing env vars (env changes usually trigger a restart)

---

## SQLite, disks, and PostgreSQL

Firestore holds contracts, clients, and users. SQLite on Render is still used for Django migrations (`django_celery_beat` tables).

On Render, the filesystem is **reset on each deploy** (and not shared across services). That is acceptable if you only need SQLite so `migrate` succeeds.

If you later store anything important in Django’s ORM:

1. Create a Render **PostgreSQL** instance in the same region.
2. Add `dj-database-url` (or equivalent) and set `DATABASE_URL`.
3. Point web, worker, and beat at the same Postgres URL.
4. Run `python manage.py migrate` as a **Pre-Deploy Command** on the web service.

A Render **persistent disk** can keep SQLite on **one** instance only. It does not sync to the worker/beat services, so it is not a substitute for Postgres if those processes share Django tables.

---

## Production Docker (split services)

When you outgrow the all-in-one test container, keep the same `Dockerfile` but run **three** processes as separate Render services and a **Key Value** Redis. Override the Docker command:

- Web: `gunicorn contractvault_api.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
- Worker: `celery -A celery_app worker --loglevel=info --concurrency=2`
- Beat: `celery -A celery_app beat --loglevel=info`

Set `EMBEDDED_REDIS=0` and point `REDIS_URL` at Render Redis on every service.

---

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Deploy fails health check | Health path is `/health/` (not `/`). Wait for Gunicorn to bind `$PORT`. |
| `DisallowedHost` | Add the Render hostname to `ALLOWED_HOSTS`. |
| Browser CORS errors | `CORS_ALLOWED_ORIGINS` must match the frontend origin exactly. |
| Firebase init / auth errors | `FIREBASE_PRIVATE_KEY` newlines (`\n`). Service account has Firestore and Storage access. |
| Celery never runs tasks | Worker is running. `REDIS_URL` is the **internal** Redis URL on all three services. Same region. |
| Reminders never send | Beat is running (only one instance). Brevo key and verified sender. Worker is up to execute the task. |
| Uploads fail | `FIREBASE_STORAGE_BUCKET` and Storage rules. Files ≤ 25 MB; PDF/JPEG/PNG only. |
| Build / import errors | Python 3.12+. `requirements.txt` installs. `collectstatic` succeeds (WhiteNoise). |
| Free instance “asleep” | First request is slow. Celery worker/beat on Free can stop; use a paid instance for production jobs. |

Official references:

- [Deploy a Flask/Python app](https://render.com/docs/deploy-flask) (same Gunicorn pattern)
- [Python version](https://render.com/docs/python-version)
- [Native runtimes vs Docker](https://render.com/docs/native-runtimes)
- [Background workers](https://render.com/docs/background-workers)
- [Key Value (Redis)](https://render.com/docs/key-value)

---

## Local commands (for comparison)

These match production, using your local `.env` and Redis:

```bash
python manage.py collectstatic --noinput
python manage.py migrate
gunicorn contractvault_api.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 120
celery -A celery_app worker --loglevel=info --concurrency=2
celery -A celery_app beat --loglevel=info
```
