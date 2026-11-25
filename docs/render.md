# Deploy the dashboard and API on Render

The Render build serves the dashboard and API from one HTTPS address. Vehicle
records and allocation state live in your existing PostgreSQL database.
GitHub Actions requests the public liveness endpoint every ten minutes.

## Deploy

1. Push this implementation to the default branch of your GitHub repository.
2. In Render, create a Blueprint and connect this repository. Render reads
   `render.yaml` and builds `Dockerfile.render`.
3. When prompted, enter `DATABASE_URL` as a Render secret. Use a database dedicated
   to this application, reachable from Render, with permission to create the
   application's tables on first startup. Use your provider's direct PostgreSQL
   connection or a session pooler and its required TLS settings.
4. Render generates `API_TOKEN`. After deployment, copy it from the service's
   environment settings and enter it in the dashboard's API token field.
5. Visit the Render URL. Verify `/livez` returns `{"status":"alive"}` and
   `/readyz` returns HTTP 200. Generate a record through the dashboard and retrieve
   it from History.

The free web-service plan is selected in the Blueprint. The database remains
under your existing provider's billing and retention settings. The launcher
requires PostgreSQL, a token, and a built dashboard before startup.

Render supplies `PORT`. The container runs one Uvicorn process and uses
`WORKER_ID=auto`. During overlapping deployments, each instance claims its own
worker lease. Reclaimed worker IDs retain their fence and allocation history.
Database failures or exhausted worker slots cause startup to fail.

Automatic selection scans worker IDs 0–1023 using the existing transactional
claim operation, stopping after five seconds between attempts. Heartbeats keep
the chosen ID for that process. Fixed `WORKER_ID` values continue to use exclusive
leases, including when they share a database with automatic instances.

The hosted dashboard displays one backend and hides the Test Lab controls.
Dashboard assets and health endpoints are publicly accessible. Vehicle APIs and
statistics require the shared API token. This deployment supports trusted users
sharing that token. Separate customer accounts require additional access controls.

For optional Google Maps, add `VITE_GOOGLE_MAPS_API_KEY` to the Render service's
environment and rebuild. Restrict this browser key to the deployed website
and the required Google APIs. Terrain remains an opt-in control.

## Enable the ten-minute check

1. Copy the Render service origin, for example `https://your-app.onrender.com`.
2. In GitHub, open **Settings → Secrets and variables → Actions → Variables**.
3. Create a repository variable named `RENDER_APP_URL` with that origin.
4. Open **Actions → Render keep-alive → Run workflow** on the default branch.
5. Confirm that the check succeeds. Inspect subsequent runs to confirm scheduling.

The workflow schedule is `*/10 * * * *` in UTC, at minutes 00, 10, 20, 30, 40,
and 50 of every hour. Scheduled jobs are skipped until the URL variable is set.
A manual run with missing configuration reports an error.

The script checks `/livez`, accepts HTTP 200 with the expected JSON, and retries
up to three times with 60-second request timeouts and five-second delays.
The workflow has a four-minute overall timeout. It runs on GitHub and therefore
continues independently of the Render application's lifecycle.

GitHub may delay or drop scheduled runs under load. Public repositories'
schedules are disabled after 60 days of repository inactivity. Render's free
services sleep after 15 idle minutes and consume the workspace's free instance
hours while active. Treat this setup as a best-effort keep-alive.
[GitHub schedules](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
and [Render free-service limits](https://render.com/docs/free).

## Verify before release

- Build `Dockerfile.render` and run it with a temporary PostgreSQL database,
  `API_TOKEN`, and a chosen `PORT`.
- Open the dashboard, enter the token, generate an event, inspect History, and
  open Live map. Check that backend readiness becomes visible with the token.
- Start a second instance against the same database. Both `/readyz` responses
  should succeed with different worker IDs. Stop the first instance and verify
  that its records are still available through the second.
- Run `python scripts/render_keepalive.py` with `RENDER_APP_URL` set to the local
  test origin. Then run the GitHub workflow against the deployed HTTPS origin.
