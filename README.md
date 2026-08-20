# ListMonk Pro Dashboard

A modern, dark-themed management dashboard and automation suite for your self-hosted [ListMonk](https://listmonk.app/) instance. Built with FastAPI and vanilla JavaScript.

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.141-green) ![Docker](https://img.shields.io/badge/Docker-Ready-blue) ![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Features

### Core Management
- **Subscribers** — Search, filter by list, create, edit, delete, blocklist, bulk blocklist, and bulk delete. Export all subscribers or individual subscriber profiles.
- **Lists** — Create and manage mailing lists with tags, type (`public`/`private`), and opt-in settings (`single`/`double`).
- **Campaigns** — Full campaign lifecycle management: create, edit, start, pause, resume, cancel, archive, delete, HTML preview, and test sends.
- **Templates** — Create, edit, and preview HTML email templates, with one-click default template assignment.
- **Bounces** — View and filter bounces by campaign or type (`hard`/`soft`), delete individual records, or delete filtered sets with parallel execution. Export bounce records with campaign attribution as CSV.
- **Unsubscribes** — Unified dashboard tracking both IMAP reply unsubscribes and direct link clicks with campaign grouping, per-campaign CSV export, and undo/reset capabilities.

### Analytics & Reporting
- **Overview Dashboard** — Summary metrics (subscribers, lists, campaigns) and interactive Chart.js visualizations.
- **Campaign Analytics** — Views, clicks, bounces, and link tracking over time with custom date range filtering.
- **Subscriber-Level Engagement Exports** — Download CSV exports of the exact subscribers who opened, clicked, or hard-bounced any campaign.
- **Campaign Comparison** — Side-by-side performance comparison charts across all campaigns.
- **Summary Exports** — One-click CSV export of all campaigns or raw analytics time-series data.

### Smart Bounce Handling & Ingestion
- **RFC 3463 DSN Classifier** — Accurately parses bounce DSNs into **hard** (invalid address, no such user, mailbox disabled) vs **soft** (mailbox full, greylist, reputation, rate limits).
- **Opener False-Positive Filtering** — Recipients who actually opened a campaign (`campaign_views`) are automatically excluded from hard bounce lists, metrics, and exports.
- **Hard Bounce Caching** — True hard bounce counts are calculated and cached in memory (refreshed every 5 minutes in the background), keeping campaign listing fast and accurate.
- **Automated IMAP Ingestion** — Scans your bounce mailbox every hour for unseen bounce notifications, attributes them to the correct campaign via headers or lists, and creates matching records in ListMonk.

### Dual-Source Unsubscribe Engine
- **IMAP Reply Monitor** — Scans your inbox hourly for unsubscribe keywords (`"Remove me"`, `"Unsubscribe me"`, `"Exclude me"`).
- **Quote-Stripping Filter** — Strips quoted reply history and template headers so footer text (e.g. *"Reply with 'Remove me'"*) never triggers false positives.
- **Link Unsubscribe Scanner** — Polls ListMonk lists for subscribers who clicked the direct unsubscribe link.
- **List-Aware Campaign Attribution** — Attributes unsubscribes to campaigns that actually targeted the subscriber's lists.
- **Automated Actions** — Automatically removes the contact from all lists and optionally blocklists them (toggleable in Settings).
- **Undo / Reset Flow** — Restore unsubscribed contacts back to their original lists, re-enable their status, and clear restored log entries in one click.

### CSV Converter & Direct Importer
ListMonk requires a specific CSV format (`email`, `name`, `attributes` as JSON). This built-in tool handles arbitrary CSV files:
1. **Upload** any CSV file.
2. **Auto-Detect & Map** columns visually (select email, name, and arbitrary attribute columns).
3. **Preview** converted rows in real-time.
4. **Download** the converted CSV or **import directly** into ListMonk with selected target lists and subscription mode.

### Settings & Automation
- **Campaign Scheduler** — Enforces daily sending windows (e.g., 8:00 AM – 8:00 PM EST) and allowed days (Mon–Fri, weekends). Running campaigns outside the window auto-pause, and auto-paused campaigns automatically resume when the window opens.
- **Auto-Unblock Protection** — Identifies engaged subscribers (who clicked links or opened campaigns) who were accidentally blocklisted by false bounces, re-enables them, and purges their bounce records. Runs every 6 hours or manually.
- **Persistent Data Store** — Runtime configuration and logs persist to `DATA_DIR` (backed by a named volume in Docker).

### Security
- HMAC-SHA256 session-based cookie authentication with automatic secret generation.
- All API endpoints and pages protected by authentication middleware.
- 7-day session lifetime with clean login/logout flow.

---

## Quick Start

### Option 1: Docker (Recommended)

```bash
git clone https://github.com/worthmindbd/Listmonk-Dashboard.git
cd Listmonk-Dashboard
cp .env.example .env
nano .env  # Configure your credentials
docker compose up -d --build
```

Open **http://localhost:8000** and log in.

### Option 2: Manual / Local Development

```bash
git clone https://github.com/worthmindbd/Listmonk-Dashboard.git
cd Listmonk-Dashboard
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env  # Configure your credentials
./start.sh
# Or manually:
# uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Configuration

Configure your environment in `.env` (or copy `.env.example`):

```env
# ListMonk API Connection
LISTMONK_URL=https://your-listmonk-instance.com
LISTMONK_USER=listmonk
LISTMONK_API_KEY=your-api-key-here

# Dashboard Login Credentials (MUST be changed from defaults)
DASHBOARD_USER=admin
DASHBOARD_PASS=changeme

# Session Signing Key (leave empty to auto-generate, or set a random 64-char hex string)
SESSION_SECRET=

# IMAP Settings for Unsubscribe Monitoring (Optional)
IMAP_HOST=mail.example.com
IMAP_PORT=993
IMAP_USER=your@email.com
IMAP_PASS=your-password
IMAP_USE_SSL=true

# IMAP Settings for Bounce Mailbox Monitoring (Optional)
BOUNCE_IMAP_HOST=mail.example.com
BOUNCE_IMAP_PORT=993
BOUNCE_IMAP_USER=bounces@yourdomain.com
BOUNCE_IMAP_PASS=your-password
BOUNCE_IMAP_USE_SSL=true

# Persistent Data Storage Directory (Optional: defaults to /data in Docker, repo root in local dev)
# DATA_DIR=/data
```

Generate a random session secret:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## Production Deployment (VPS with Nginx)

### 1. Clone & Configure
```bash
cd /opt
git clone https://github.com/worthmindbd/Listmonk-Dashboard.git
cd Listmonk-Dashboard
cp .env.example .env
nano .env  # Set your credentials and secure passwords
```

### 2. Start with Docker Compose
```bash
docker compose up -d --build
```

### 3. Multi-Instance Setup (Optional)
To run a secondary dashboard instance (e.g. for a second ListMonk installation) on port `8001`:
```bash
docker compose -f docker-compose.yml -f docker-compose.instance2.yml up -d --build
```

### 4. Nginx Reverse Proxy Configuration
```nginx
server {
    listen 80;
    server_name dash.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 5. SSL with Let's Encrypt / Certbot
```bash
sudo ln -s /etc/nginx/sites-available/listmonk-dashboard /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d dash.yourdomain.com
```

---

## Background Tasks

The dashboard runs five background loops started during application lifecycle:

| Background Task | Frequency | Description |
|-----------------|-----------|-------------|
| **Campaign Scheduler** | Every 60s | Checks running campaigns and pauses/resumes them based on the send window |
| **Hard Bounce Cache** | On startup + every 5 min | Computes true hard bounce counts per campaign, excluding openers |
| **IMAP & Link Unsubscribe Scan** | Every 1 hour | Scans inbox for reply keywords and polls ListMonk for link unsubscribes |
| **Bounce Ingestion** | Every 1 hour | Ingests and classifies unseen DSNs from the bounce IMAP mailbox |
| **Auto-Unblock Protection** | Every 6 hours | Scans for engaged blocklisted contacts, re-enables them, and purges bounce records |

---

## Project Structure

```
ListMonk-Dashboard/
  app/
    main.py                    # FastAPI application, auth middleware, background task lifespan
    auth.py                    # Session cookie creation, signing, and verification
    config.py                  # Settings loader and DATA_DIR path resolver
    routers/
      subscribers.py           # Subscriber CRUD, import status, bulk blocklist/delete, exports
      lists.py                 # List CRUD
      campaigns.py             # Campaign CRUD, running stats, analytics, viewer/clicker/bounce exports
      templates.py             # Template CRUD & default template selection
      bounces.py               # Bounce management, ingestion, filtered CSV exports, bulk deletions
      converter.py             # CSV detection, conversion, and direct ListMonk import
      unsubscribes.py          # Unsubscribe dashboard, campaign groups, manual scans, undo/reset
    services/
      listmonk_client.py       # Async HTTP client for ListMonk REST API (with paginate_all helper)
      csv_converter.py         # CSV converter transforming arbitrary columns to ListMonk schema
      export_service.py        # Memory-efficient streaming CSV generator
      auto_unblock.py          # Auto-unblock engaged contacts (clickers/openers) from blocklist
      campaign_scheduler.py    # Timezone-aware send window scheduler
      bounce_ingest.py         # RFC 3463 DSN classifier & IMAP bounce mailbox scanner
      bounce_filters.py        # Opener false-positive bounce filtering
      bounce_list.py           # Fast-path paginated bounce listing with opener exclusion
      hard_bounce_cache.py     # In-memory hard bounce counts cache per campaign
      opener_cache.py          # In-memory cache of campaign opener email sets with TTL
      imap_unsubscribe.py      # IMAP reply scanner for unsubscribe keywords with quote stripping
      link_unsubscribe.py      # ListMonk direct link unsubscribe scanner
      imap_helpers.py          # Shared IMAP & string escaping utilities
      unsubscribe_log.py       # Thread-safe persistent JSON state manager
  static/
    css/style.css              # Dark theme stylesheet
    js/
      api.js                   # Fetch API wrapper with 401 handling
      app.js                   # Single-Page Application router, bounces view, notifications
      analytics.js             # Campaign analytics charts and link click tracking
      campaigns.js             # Campaign management, status transitions, test sends
      charts.js                # Dashboard overview charts & summary cards
      converter.js             # CSV converter UI, column mapping, and import modal
      lists.js                 # Mailing list management
      settings.js              # Settings UI (scheduler, auto-unblock, unsubscribe toggles)
      subscribers.js           # Subscriber management, search, edit modal, bulk actions
      unsubscribes.js          # Campaign-grouped unsubscribe view, source badges, undo/reset
    favicon.png
    favicon.svg
  templates/
    index.html                 # Authenticated SPA shell
    login.html                 # Dark-themed login page
  tests/
    test_analytics.py          # Analytics endpoint & export tests
    test_bounce_classify.py    # RFC 3463 bounce classifier tests
    test_bounce_filters.py     # Opener exclusion & false positive filter tests
    test_link_unsubscribe.py   # Link unsubscribe scanner & dedup tests
  .github/workflows/
    deploy.yml                 # Automated SSH deploy on push to main
  docker-compose.yml           # Primary instance compose definition
  docker-compose.instance2.yml # Secondary instance compose override
  Dockerfile
  pytest.ini
  requirements.txt
  requirements-dev.txt
  start.sh
  .env.example
```

---

## API Reference

Once running, visit **http://localhost:8000/docs** for the interactive Swagger UI (requires login).

### Key Endpoints

| Category | Endpoint | Method | Description |
|----------|----------|--------|-------------|
| **Auth** | `/auth/login` | GET / POST | Login page / authenticate session |
| | `/auth/logout` | GET | End session & redirect |
| **Subscribers** | `GET /api/subscribers` | GET | List subscribers with search/filter |
| | `GET /api/subscribers/export-all` | GET | Export all subscribers as CSV |
| | `GET /api/subscribers/import/status` | GET | Check subscriber import status |
| | `GET /api/subscribers/import/logs` | GET | View subscriber import logs |
| | `GET /api/subscribers/{id}` | GET | Get subscriber details |
| | `GET /api/subscribers/{id}/export` | GET | Export subscriber profile JSON |
| | `GET /api/subscribers/{id}/bounces` | GET | Get bounces for subscriber |
| | `POST /api/subscribers` | POST | Create subscriber |
| | `PUT /api/subscribers/{id}` | PUT | Update subscriber |
| | `PUT /api/subscribers/{id}/blocklist` | PUT | Blocklist single subscriber |
| | `PUT /api/subscribers/blocklist` | PUT | Bulk blocklist subscribers by ID array |
| | `PUT /api/subscribers/lists` | PUT | Bulk modify list memberships |
| | `DELETE /api/subscribers/{id}` | DELETE | Delete single subscriber |
| | `DELETE /api/subscribers` | DELETE | Bulk delete subscribers |
| **Lists** | `GET /api/lists` | GET | List all mailing lists |
| | `POST /api/lists` | POST | Create mailing list |
| | `GET /api/lists/{id}` | GET | Get mailing list details |
| | `PUT /api/lists/{id}` | PUT | Update mailing list |
| | `DELETE /api/lists/{id}` | DELETE | Delete mailing list |
| **Campaigns** | `GET /api/campaigns` | GET | List all campaigns (with cached hard bounce count) |
| | `GET /api/campaigns/running/stats` | GET | Real-time stats for running campaigns |
| | `GET /api/campaigns/export-all` | GET | Export all campaigns summary as CSV |
| | `GET /api/campaigns/analytics/{type}` | GET | Campaign analytics (`views`, `clicks`, `bounces`, `links`) |
| | `GET /api/campaigns/analytics/{type}/export` | GET | Export campaign analytics as CSV |
| | `GET /api/campaigns/{id}` | GET | Get campaign details |
| | `GET /api/campaigns/{id}/preview` | GET | Preview campaign HTML rendering |
| | `GET /api/campaigns/{id}/subscribers/{type}` | GET | View who opened (`views`), clicked (`clicks`), or hard-bounced |
| | `GET /api/campaigns/{id}/subscribers/{type}/export` | GET | Export campaign viewers, clickers, or hard bounces as CSV |
| | `POST /api/campaigns` | POST | Create campaign |
| | `POST /api/campaigns/{id}/test` | POST | Send test email |
| | `PUT /api/campaigns/{id}` | PUT | Update campaign |
| | `PUT /api/campaigns/{id}/status` | PUT | Change status (`start`, `pause`, `cancel`) |
| | `PUT /api/campaigns/{id}/archive` | PUT | Archive campaign |
| | `DELETE /api/campaigns/{id}` | DELETE | Delete campaign |
| **Templates** | `GET /api/templates` | GET | List templates |
| | `POST /api/templates` | POST | Create template |
| | `GET /api/templates/{id}` | GET | Get template details |
| | `PUT /api/templates/{id}` | PUT | Update template |
| | `PUT /api/templates/{id}/default` | PUT | Set default template |
| | `DELETE /api/templates/{id}` | DELETE | Delete template |
| **Bounces** | `GET /api/bounces` | GET | List bounces (filtered by campaign or type) |
| | `POST /api/bounces/ingest` | POST | Manually trigger bounce mailbox IMAP ingestion |
| | `GET /api/bounces/export` | GET | Export bounce records as CSV |
| | `DELETE /api/bounces/{id}` | DELETE | Delete single bounce record |
| | `DELETE /api/bounces` | DELETE | Delete all bounces or filter-matched bounces |
| **CSV Converter** | `POST /api/converter/detect-columns` | POST | Upload CSV and detect columns & preview |
| | `POST /api/converter/convert` | POST | Convert CSV to ListMonk schema |
| | `POST /api/converter/convert-and-import` | POST | Convert and import directly into ListMonk lists |
| **Scheduler** | `GET /api/scheduler` | GET | Get send window schedule configuration & status |
| | `PUT /api/scheduler` | PUT | Update send window schedule settings |
| | `POST /api/scheduler/run` | POST | Manually trigger scheduler evaluation tick |
| **Auto-Unblock** | `GET /api/auto-unblock/status` | GET | Get count of blocklisted engaged contacts |
| | `POST /api/auto-unblock/run` | POST | Manually run auto-unblock process |
| **Unsubscribes** | `GET /api/unsubscribes` | GET | Paginated unsubscribe log |
| | `GET /api/unsubscribes/stats` | GET | Aggregate unsubscribe stats & source breakdown |
| | `GET /api/unsubscribes/settings` | GET | Get unsubscribe settings (e.g. blocklist toggle) |
| | `PUT /api/unsubscribes/settings` | PUT | Update unsubscribe settings |
| | `GET /api/unsubscribes/campaigns` | GET | Campaign-grouped unsubscribe summary |
| | `GET /api/unsubscribes/campaign/{id}` | GET | Records for a specific campaign |
| | `GET /api/unsubscribes/campaign/{id}/export` | GET | Export campaign unsubscribes as CSV |
| | `DELETE /api/unsubscribes/campaign/{id}` | DELETE | Delete unsubscribe records for a campaign |
| | `DELETE /api/unsubscribes/records` | DELETE | Delete specific records by email |
| | `GET /api/unsubscribes/export` | GET | Export all unsubscribe records as CSV |
| | `POST /api/unsubscribes/scan` | POST | Trigger combined IMAP + link unsubscribe scan |
| | `GET /api/unsubscribes/imap-status` | GET | Check IMAP connection health |
| | `DELETE /api/unsubscribes/clear` | DELETE | Clear all unsubscribe log records |
| | `POST /api/unsubscribes/reset` | POST | Undo/reset all unsubscribes & restore memberships |

---

## Testing

Run the test suite with Pytest:

```bash
# Activate virtual environment
source venv/bin/activate

# Run all unit and integration tests
pytest -v
```

---

## Requirements & Prerequisites

- **Python 3.10+** (or Docker)
- A running **ListMonk** instance with REST API access
- **Individual Subscriber Tracking** must be enabled in ListMonk:
  - Go to **ListMonk Settings > Privacy > Individual subscriber tracking** → Turn **ON**
  - *Required for subscriber-level exports (views, clicks, bounces), opener false-positive filtering, and Auto-Unblock Protection.*

---

## Developed by

[WorthMind](https://worthmind.net/)
