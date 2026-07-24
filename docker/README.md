# 🐳 Docker Guide — Billing Backend

Run the billing backend (a Django app) **and** its PostgreSQL database on your
machine with one command — no need to install Python or Postgres yourself.

> **What is Docker, in one sentence?** It runs the app inside a self-contained
> "box" (a *container*) that already has the right Python, database, and
> libraries baked in — so it works the same on everyone's laptop.

---

## Quick start (TL;DR)

```bash
# 1. Install Docker Desktop and leave it running.
#    https://www.docker.com/products/docker-desktop/

# 2. From the project root, create your settings file (one time):
cp .env.example .env

# 3. Start everything:
cd docker
docker compose up --build
```

Then open 👉 <http://localhost:5002>

Press `Ctrl + C` to stop, or run `docker compose down` to stop and clean up.

---

## Step by step

### 1. Install Docker Desktop (one time)

Download and install from
<https://www.docker.com/products/docker-desktop/> (Mac, Windows, or Linux).
**Open Docker Desktop and leave it running** — you'll see a whale icon 🐳 in your
menu bar / system tray when it's ready. You do **not** need a Docker account.

### 2. Create your settings file (one time)

The app reads secrets and settings from a `.env` file. A template is committed to
the repo. From the **project root** (the folder that contains `docker/`):

```bash
cp .env.example .env
```

On Windows PowerShell use `copy .env.example .env` instead. The defaults work for
local development.

> **⚠️ Critical — shared secret.** `SECRET_KEY` in this `.env` **must be the same
> value** as the pettycash backend's `SECRET_KEY`. JWTs are signed by one service
> and verified by the other; if the keys differ, auth breaks across the two.
> Both `.env.example` files ship with `SECRET_KEY=replace-me` — keep them equal.

### 3. Start the app 🚀

```bash
cd docker
docker compose up --build
```

The first run takes a few minutes. It:
- Builds the app image from the `Dockerfile` (installs Python + dependencies).
- Starts a **PostgreSQL** database container.
- Waits for the database, ensures the `pettycashv2` schema exists, then runs
  Django migrations.
- Starts the app.

When you open `cd docker && docker compose up`, a development override is applied
automatically: your local code is mounted into the container and the app runs
Django's auto-reloading dev server, so **saving a file reloads the app** — no
rebuild needed. (Rebuild only when you change `requirements.txt` or the
`Dockerfile`.)

Open 👉 <http://localhost:5002>

### 4. Stopping

- **Quick stop:** click the running terminal and press `Ctrl + C`.
- **Full stop / cleanup** (from `docker/`): `docker compose down` — keeps your
  database data for next time.

---

## Ports (what runs where)

| Service | Address | Notes |
|---------|---------|-------|
| Billing app | http://localhost:5002 | `5001` is taken by the pettycash app |
| Billing database | `localhost:5433` | PostgreSQL; `5432` is taken by the pettycash db |

Inside Docker the app reaches its database at `db:5432` over the compose network;
the `5433` mapping above is only so tools on your laptop (psql, a DB GUI) can
connect.

---

## Everyday commands (run from the `docker/` folder)

| I want to… | Command |
|------------|---------|
| Start and watch logs | `docker compose up` |
| Start in the background | `docker compose up -d` |
| Rebuild after changing dependencies | `docker compose up --build` |
| Stop the app | `docker compose down` |
| Stop **and wipe the database** | `docker compose down -v` |
| See the app's logs | `docker compose logs -f app` |
| Open a shell inside the app container | `docker compose exec app sh` |
| Run migrations manually | `docker compose exec app python manage.py migrate` |

> ⚠️ `docker compose down -v` **deletes all local database data.** Use it only
> when you want a clean database.

---

## How this fits with the other repos

This repository is **one piece of a larger system** made of separate repos:

| Repo | What it is | Typical local port |
|------|------------|--------------------|
| **This repo** (billing backend) | Django backend + database | app `5002`, db `5433` |
| Pettycash backend | Flask backend + database | app `5001`, db `5432` |
| Billing frontend | Separate UI | `3000` (`FRONTEND_APP_URL`) |
| Onboarding frontend | Separate UI | — |

The Docker setup in this folder starts **only this repo** (the Django app and its
database). The other repos are cloned and run separately.

> A combined "run all four at once" orchestrator is planned separately. For now,
> start each repo on its own. Ports and env vars here are kept clean so they slot
> into that setup without collisions.

---

## Troubleshooting

**"Cannot connect to the Docker daemon" / "docker: command not found"**
→ Docker Desktop isn't running. Open it and wait for the whale icon, then retry.

**"port is already allocated" for `5002` or `5433`**
→ Something else is using that port (a previous run, or another local service).
Run `docker compose down` first, or stop the other process.

**Migrations failed / database looks broken**
→ Reset with a clean start (deletes local DB data):
```bash
docker compose down -v
docker compose up --build
```

**JWT / login fails between billing and pettycash**
→ Almost always a `SECRET_KEY` mismatch. Make sure the `SECRET_KEY` in this
repo's `.env` is identical to the pettycash backend's `.env`.

**Still stuck?** → Copy the last ~20 lines from the terminal and share them with
the team.
