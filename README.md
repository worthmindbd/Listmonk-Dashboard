# ListMonk Pro Dashboard

A modern, dark-themed dashboard for managing your self-hosted [ListMonk](https://listmonk.app/) instance. Built with FastAPI and vanilla JavaScript.

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green) ![Docker](https://img.shields.io/badge/Docker-Ready-blue) ![License](https://img.shields.io/badge/License-MIT-yellow)

## Features

### Core Management
- **Subscribers** - Search, add, edit, delete, blocklist. Bulk export as CSV
- **Lists** - Create and manage lists with tags, type (public/private), optin settings
- **Campaigns** - Full control: create, edit, start, pause, cancel, preview, test send
- **Templates** - Create, edit, preview HTML templates
- **Bounces** - View and filter by campaign, delete individual or all, export as CSV
- **Unsubscribes** - IMAP-based auto-detection of unsubscribe requests from campaign replies

### Analytics
- **Dashboard** - Summary cards (subscribers, lists, campaigns) + performance charts
- **Campaign Analytics** - Per-campaign views, clicks, bounces, and link tracking over time
- **Subscriber-Level Exports** - Download CSV of exactly who opened, who clicked, and who bounced
- **Campaign Comparison** - Side-by-side bar chart of all campaigns

### CSV Converter
ListMonk requires a specific CSV format (`email`, `name`, `attributes` as JSON). This tool converts any CSV:

1. Upload your CSV
2. Map columns visually (select email, name, and attribute columns)
3. Preview the converted output
4. Download the converted CSV or import directly to ListMonk

### IMAP Unsubscribe Monitor
Automatically detects unsubscribe requests from campaign reply emails:
- Scans your IMAP inbox every **1 hour** for keywords: `"Remove me"`, `"Unsubscribe me"`, `"Exclude me"`
- Matches replies to the correct campaign by date
- Automatically **unsubscribes** the sender from all lists and **blocklists** them
- Campaign-grouped dashboard with per-campaign management (export, bulk delete, remove list)
- Uses IMAP `SINCE` filter to only scan emails from the latest campaign month (no wasted processing)
- Concurrent scan protection with asyncio lock
- Manual "Scan Now" button with loading state and timeout

### Settings (Campaign Scheduler + Auto-Unblock)
All automation is managed from the **Settings** page with tabbed UI:

**Campaign Scheduler** - ListMonk has no built-in send time window. This adds it:
- Set a daily send window (e.g., **8:00 AM - 8:00 PM EST**)
- Choose which days (Mon-Fri, weekends, etc.)
- Running campaigns **auto-pause** outside the window
- Auto-paused campaigns **auto-resume** when the window opens
- Manually paused campaigns are never touched
- Timezone-aware with visual timeline

**Auto-Unblock Protection** - Detects subscribers who clicked links but got blocklisted (from bounces) and automatically:
- Re-enables them (removes blocklist status)
- Deletes their false bounce records
- Runs every 6 hours in the background, or manually via Settings

### Security
- Session-based authentication with signed cookies
- Login required for all pages and API endpoints
- Credentials configured via environment variables
- Sessions expire after 7 days

## Quick Start

### Option 1: Docker (Recommended)

```bash
git clone https://github.com/worthmindbd/Listmonk-Dashboard.git
cd Listmonk-Dashboard
cp .env.example .env
nano .env  # Fill in your credentials
docker compose up -d --build
```

Open **http://localhost:8000** and login.

### Option 2: Manual

```bash
git clone https://github.com/worthmindbd/Listmonk-Dashboard.git
cd Listmonk-Dashboard
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env  # Fill in your credentials
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Configuration

Create a `.env` file (or copy `.env.example`):

```env
# ListMonk API connection
LISTMONK_URL=https://your-listmonk-instance.com
LISTMONK_USER=listmonk
LISTMONK_API_KEY=your-api-key-here

# Dashboard login credentials
DASHBOARD_USER=admin
DASHBOARD_PASS=changeme

# Session signing key (leave empty to auto-generate, set for persistence across restarts)
SESSION_SECRET=

# IMAP settings for unsubscribe monitoring (optional)
IMAP_HOST=mail.example.com
IMAP_PORT=993
IMAP_USER=your@email.com
IMAP_PASS=your-password
IMAP_USE_SSL=true
```

Generate a session secret:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## Production Deployment (VPS with Nginx)

### 1. Clone and configure on your VPS

```bash
cd /opt
git clone https://github.com/worthmindbd/Listmonk-Dashboard.git
cd Listmonk-Dashboard
cp .env.example .env
nano .env  # Set your credentials and a strong password
```

### 2. Start with Docker

```bash
docker compose up -d --build
```

### 3. Nginx reverse proxy

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

### 4. SSL with Certbot

```bash
sudo ln -s /etc/nginx/sites-available/listmonk-dashboard /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d dash.yourdomain.com
```

## Project Structure

```
ListMonk-Dashboard/
  app/
    main.py                    # FastAPI app + auth middleware + background tasks
    auth.py                    # Session-based authentication
    config.py                  # .env settings loader
    routers/
      subscribers.py           # Subscriber CRUD + import/export
      lists.py                 # List CRUD
      campaigns.py             # Campaign CRUD + analytics + subscriber exports
      templates.py             # Template CRUD
      bounces.py               # Bounce management + export
      converter.py             # CSV converter endpoints
      unsubscribes.py          # Unsubscribe management + IMAP scan trigger
    services/
      listmonk_client.py       # Async API client for ListMonk
      csv_converter.py         # CSV to ListMonk format converter
      export_service.py        # CSV streaming export utility
      auto_unblock.py          # Auto-unblock blocklisted clickers
      campaign_scheduler.py    # Campaign send window scheduler
      imap_unsubscribe.py      # IMAP inbox scanner for unsubscribe detection
  static/
    css/style.css              # Dark theme styles
    js/
      api.js                   # Fetch wrapper with auth handling
      app.js                   # SPA router + shared components
      charts.js                # Dashboard home + Chart.js
      analytics.js             # Campaign analytics page
      subscribers.js           # Subscriber management
      campaigns.js             # Campaign management
      lists.js                 # List management
      settings.js              # Settings page (scheduler + auto-unblock)
      converter.js             # CSV converter UI
      unsubscribes.js          # Unsubscribe dashboard (campaign-grouped)
  templates/
    index.html                 # SPA shell (authenticated)
    login.html                 # Login page
  .github/workflows/
    deploy.yml                 # Auto-deploy on push to main
  start.sh                     # Dev server launcher
  Dockerfile
  docker-compose.yml
  .env.example
  requirements.txt
```

## API Documentation

Once running, visit **http://localhost:8000/docs** for the interactive Swagger UI (requires login).

Key endpoints:

| Endpoint | Description |
|----------|-------------|
| `POST /auth/login` | Authenticate and get session cookie |
| `GET /auth/logout` | End session |
| `GET /api/subscribers` | List subscribers with search/filter |
| `GET /api/subscribers/export-all` | Export all subscribers as CSV |
| `GET /api/campaigns` | List all campaigns |
| `GET /api/campaigns/{id}/subscribers/views/export` | Export who opened a campaign |
| `GET /api/campaigns/{id}/subscribers/clicks/export` | Export who clicked in a campaign |
| `GET /api/campaigns/{id}/subscribers/bounces/export` | Export who bounced in a campaign |
| `GET /api/campaigns/analytics/{type}` | Campaign analytics (views/clicks/bounces/links) |
| `GET /api/bounces` | List bounces with campaign filter |
| `GET /api/bounces/export` | Export bounce records as CSV |
| `POST /api/converter/convert` | Convert CSV to ListMonk format |
| `GET /api/scheduler` | Get scheduler config and status |
| `PUT /api/scheduler` | Update scheduler settings |
| `POST /api/auto-unblock/run` | Manually trigger auto-unblock |
| `GET /api/unsubscribes` | List unsubscribe records (paginated) |
| `GET /api/unsubscribes/campaigns` | Campaign-grouped unsubscribe summary |
| `GET /api/unsubscribes/campaign/{key}` | Records for a specific campaign |
| `POST /api/unsubscribes/scan` | Manually trigger IMAP scan |
| `GET /api/unsubscribes/export` | Export all unsubscribes as CSV |
| `GET /api/unsubscribes/imap-status` | Check IMAP connection status |

## Tech Stack

- **Backend**: Python, FastAPI, httpx (async HTTP), Jinja2
- **Frontend**: Vanilla JavaScript, Chart.js, CSS (no build step)
- **Deployment**: Docker, Nginx, Certbot
- **Architecture**: Monolithic - single process serves API + frontend + background tasks

## CI/CD Auto-Deploy

A GitHub Actions workflow is included (`.github/workflows/deploy.yml`) that auto-deploys on every push to `main`. Add these repository secrets:

| Secret | Value |
|--------|-------|
| `VPS_HOST` | Your VPS IP address |
| `VPS_USER` | SSH username (e.g., `root`) |
| `VPS_SSH_KEY` | SSH private key for authentication |

## Requirements

- Python 3.10+ (or Docker)
- A running ListMonk instance with API access
- **Individual subscriber tracking** must be **enabled** in ListMonk:
  - Go to **ListMonk Settings > Privacy > Individual subscriber tracking** → Turn **ON**
  - This is required for subscriber-level exports (who viewed, clicked, bounced) and Auto-Unblock Protection to work

## Developed by

[WorthMind](https://worthmind.net/)
