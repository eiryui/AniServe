# AniServe Keepalive

A small Cloudflare Worker that pings AniServe on Render every 5 minutes so the free-tier service does not go to sleep, and serves a status page showing whether the API and its database are healthy.

- **Pings:** `https://aniserve.onrender.com/health` (change `TARGET` in `worker.js` to your own URL)
- **Schedule:** every 5 minutes (Cloudflare Cron Trigger)
- **Status page:** open the worker's URL

Render's free tier sleeps after about 15 minutes without traffic. Pinging `/health` wakes it and keeps it awake.

## Deploy (Cloudflare dashboard)

No Wrangler or command line needed.

1. Go to **Workers & Pages → Create → Create Worker**, give it a name (for example `aniserve-keepalive`) and deploy.
2. Click **Edit code**, replace the contents with `worker.js`, set `TARGET` to your service's `/health` URL, and **Deploy**.
3. Open **Settings → Trigger Events → Add Cron Trigger** and enter:

   ```
   */5 * * * *
   ```

4. Optional: enable ping history, see below.

Open the worker's `*.workers.dev` URL to check that it works.

## Ping history (optional)

Without storage the worker still pings and the page still shows the live status. To also see a bar chart of the last 48 pings (4 hours):

1. Go to **Storage & Databases → KV → Create a namespace** (for example `ping-log`).
2. In the worker, open **Settings → Bindings → Add → KV namespace**.
3. Set the variable name to exactly `PING_LOG`, pick the namespace, and save.

Each bar is one ping: height is response time, red means the ping failed. Hover a bar for the time. Writes are one per ping, well inside the KV free tier.

## The status page

| Item | Meaning |
|---|---|
| Green dot, "All good" | Service is up and `/health` reports `database: "connected"`. |
| Amber dot, "Awake, database unavailable" | The service answers, but it has no database connection. Check `DATABASE_URL` and the Render logs. |
| Red dot, "Down" | The ping failed or returned an error status. |
| Response | Time the last ping took. The first ping after a sleep can take 30 seconds or more. |

The page re-checks every 60 seconds and has a **Ping now** button.

## Routes

| Route | What it does |
|---|---|
| `/` | Status page. |
| `/api/check` | Pings AniServe now and returns the result as JSON. |
| `/api/history` | Returns the stored ping history as JSON, or `null` if no KV namespace is bound. |

Every visit to `/api/check` (including the page's own refresh) is also a ping, and is logged to history when KV is bound.

## Configuration

Edit the constants at the top of `worker.js`:

| Constant | Default | Notes |
|---|---|---|
| `TARGET` | `https://aniserve.onrender.com/health` | The URL to ping. |
| `HISTORY_LIMIT` | `48` | Number of pings kept in history. |

The request timeout is 45 seconds, to allow for a cold start on Render. To change how often it pings, edit the Cron Trigger expression in the dashboard.

## Troubleshooting

- **Page says "Down" right after a long idle period.** The service was asleep. Pinging wakes it, so the next check should be green.
- **"Awake, database unavailable".** The service is awake but has no database connection. AniServe keeps retrying in the background, so check `DATABASE_URL` and that the database is on.
- **No history bars.** The KV binding is missing or its variable name is not exactly `PING_LOG`.
- **Cron does not seem to fire.** Check **Settings → Trigger Events** shows the schedule, then look at the worker's **Logs** for `ping 200 in …ms` lines.
