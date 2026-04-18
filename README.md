# Livewire 🔴

A 24/7 satirical conspiracy wire service that runs in Ara's cloud.
Every 2 hours, an agent scans real news, picks two unrelated trending
items, and airs an obviously satirical red-string theory connecting them
via iMessage. Users don't prompt it — drops arrive unprompted, timed to
real news.

> **Delivery channel:** Ara's `linq_send_message` delivers to the single
> phone you paired at <https://app.ara.so/connect>. The subscriber list
> is real (SUBSCRIBE / STOP work and the count is surfaced) but every
> drop goes to that one paired number — think of it as the demo phone
> for the hackathon. True per-subscriber fan-out would need a Twilio /
> SES-style channel; that's the obvious next integration.

Built for the Ara Hackathon (virality track).

## Architecture

    app.py               reactive automation  (conspiracyyy-reply)
                         inbound iMessage commands: SUBSCRIBE, STOP,
                         MORE, LAST, RATE, RANDOM, "X + Y", HELP
    cron_scan.py         scheduled automation (conspiracyyy-wire)
                         runs every 2h via  ara deploy --cron "0 */2 * * *"
    tools/
      news_sources.py    stdlib-only fetchers for HN, Reddit, NYT/BBC RSS,
                         Wikipedia "In the news" — no API keys
      journal.py         file-journal de-dupe + drop history
      broadcast.py       subscriber list, cooldown, SMS body formatter
    server.py            local HTTP server for the landing page
                         /api/wire, /api/stats, /api/conspiracy,
                         /api/random, /api/send-imessage
    index.html           landing page: live wire terminal + subscribe CTA

## Setup

### 1. Install deps

    pip install -r requirements.txt

### 2. Secrets

Create `.env.local` (gitignored) next to `app.py`:

    ANTHROPIC_API_KEY=sk-ant-...
    ARA_API_KEY=ak_...          # from https://app.ara.so

After `ara deploy app.py` prints `ARA_APP_ID` and `ARA_RUNTIME_KEY`, add
them to `.env.local` so the landing page's send-to-friend button works:

    ARA_APP_ID=app_...
    ARA_RUNTIME_KEY=ak_app_...

### 3. Pair a phone

Go to <https://app.ara.so/connect>, pair your phone with iMessage or SMS.
That phone becomes the default route for `linq_send_message` in both
automations.

## Deploy

Deploy the reactive replyer:

    ara deploy app.py

Deploy the scheduled wire (broadcasts every 2 hours on the top of the
hour, UTC):

    ara deploy cron_scan.py --cron "0 */2 * * *"

You can also force a one-off drop for a demo:

    ara run cron_scan.py

Check logs:

    ara logs app.py
    ara logs cron_scan.py

## Run the landing page locally

    python server.py
    # open http://127.0.0.1:8787

The page polls `GET /api/wire` every 8s and shows new drops as they land
in the journal. Generating one from the "try it" section uses
`POST /api/conspiracy` and hits Anthropic directly (doesn't go through
Ara — that one's just for the visual demo).

## Subscribe flow

Text the paired phone number with any of:

    SUBSCRIBE   → you're on the list, expect a drop every 2h
    MORE        → fresh drop in ~20s (60s per-sender cooldown)
    LAST        → the most recent aired drop
    RATE        → red-string score of the most recent drop
    RANDOM      → a drop from our fallback celebrity/object pool
    "Zendaya + Olive Garden"
                → freeform connect; any two public figures/brands
    HELP        → command menu
    STOP        → unsubscribe

## Files & storage

- Journal: `/tmp/conspiracyyy_journal.json`
  Override with `CONSPIRACYYY_JOURNAL=/abs/path/file.json`
- Subscribers: `/tmp/conspiracyyy_subscribers.json`
  Override with `CONSPIRACYYY_SUBSCRIBERS=/abs/path/file.json`

Both files are shared between the reactive agent, the cron agent, and
the local server — so `/api/wire` on the landing page shows live drops
from the cron broadcaster.

> **Note:** `/tmp` is wiped on sandbox restart. If you want persistence
> across restarts, point `CONSPIRACYYY_JOURNAL` + `CONSPIRACYYY_SUBSCRIBERS`
> at a durable path.

## Demo script

1. Show the phone: iMessage thread has 3–4 drops from today referencing
   actual news — proves the 24/7 scan is working.
2. Judge texts `MORE` → fresh drop in <20s tied to a real headline.
3. Browser shows live wire terminal scrolling past drops with timestamps.
4. Trigger `ara run cron_scan.py` live → new drop appears on the phone
   and on the wire page simultaneously.
5. Close with: *"I deployed this 48h ago. It has been awake continuously
   since."*

## Ethics

The product is designed as **obviously satirical** — the absurdity is the
disclaimer. The system prompts hard-gate these rules:

- Only public figures / brands / places / cultural objects.
- Never private individuals.
- Never allege real crimes or real relationships.
- Every drop carries a disclaimer.

If the generator is given a private-person name, it refuses with a
playful "pick a celebrity instead" message.
