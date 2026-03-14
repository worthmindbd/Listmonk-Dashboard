# ListMonk Pro Dashboard

A modern, dark-themed dashboard for managing your self-hosted [ListMonk](https://listmonk.app/) instance. Built with FastAPI and vanilla JavaScript.

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

## Features

### Core Management
- **Subscribers** - Search, add, edit, delete, blocklist. Bulk export as CSV
- **Lists** - Create and manage lists with tags, type (public/private), optin settings
- **Campaigns** - Full control: create, edit, start, pause, cancel, preview, test send
- **Templates** - Create, edit, preview HTML templates
- **Bounces** - View and filter by campaign, export as CSV

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

### Campaign Scheduler
ListMonk has no built-in send time window. This scheduler adds it:

- Set a daily send window (e.g., **8:00 AM - 8:00 PM EST**)
- Choose which days (Mon-Fri, weekends, etc.)
- Running campaigns **auto-pause** outside the window
- Auto-paused campaigns **auto-resume** when the window opens
- Manually paused campaigns are never touched
- Timezone-aware with visual timeline

### Auto-Unblock Protection
Detects subscribers who clicked links but got blocklisted (from bounces) and automatically:
- Re-enables them (removes blocklist status)
- Deletes their false bounce records
- Runs every 6 hours in the background, or manually via the dashboard

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/worthmindbd/Listmonk-Dashboard.git
cd Listmonk-Dashboard
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

Create a `.env` file in the project root:

```env
LISTMONK_URL=https://your-listmonk-instance.com
LISTMONK_USER=listmonk
LISTMONK_API_KEY=your-api-key-here
```

You can find your API credentials in ListMonk under **Settings > API**.

### 3. Run

```bash
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** in your browser.

For development with auto-reload:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Project Structure

```
ListMonk-Dashboard/
  app/
    main.py                    # FastAPI app + background tasks
    config.py                  # .env settings loader
    routers/
      subscribers.py           # Subscriber CRUD + import/export
      lists.py                 # List CRUD
      campaigns.py             # Campaign CRUD + analytics + subscriber exports
      templates.py             # Template CRUD
      bounces.py               # Bounce management + export
      converter.py             # CSV converter endpoints
    services/
      listmonk_client.py       # Async API client for ListMonk
      csv_converter.py         # CSV to ListMonk format converter
      export_service.py        # CSV streaming export utility
      auto_unblock.py          # Auto-unblock blocklisted clickers
      campaign_scheduler.py    # Campaign send window scheduler
  static/
    css/style.css              # Dark theme styles
    js/
      api.js                   # Fetch wrapper
      app.js                   # SPA router + shared components
      charts.js                # Dashboard home + Chart.js
      analytics.js             # Campaign analytics page
      subscribers.js           # Subscriber management
      campaigns.js             # Campaign management
      lists.js                 # List management
      scheduler.js             # Campaign scheduler UI
      converter.js             # CSV converter UI
  templates/
    index.html                 # SPA shell
  requirements.txt
  .env                         # Your ListMonk credentials (not committed)
  schedule.json                # Scheduler config (auto-generated)
```

## API Documentation

The dashboard exposes its own REST API. Once running, visit **http://localhost:8000/docs** for the interactive Swagger UI.

Key endpoints:

| Endpoint | Description |
|----------|-------------|
| `GET /api/subscribers` | List subscribers with search/filter |
| `GET /api/subscribers/export-all` | Export all subscribers as CSV |
| `GET /api/campaigns` | List all campaigns |
| `GET /api/campaigns/{id}/subscribers/views/export` | Export who opened a campaign |
| `GET /api/campaigns/{id}/subscribers/clicks/export` | Export who clicked in a campaign |
| `GET /api/campaigns/{id}/subscribers/bounces/export` | Export who bounced in a campaign |
| `GET /api/campaigns/analytics/{type}` | Campaign analytics (views/clicks/bounces/links) |
| `GET /api/bounces/export` | Export bounce records as CSV |
| `POST /api/converter/convert` | Convert CSV to ListMonk format |
| `GET /api/scheduler` | Get scheduler config and status |
| `PUT /api/scheduler` | Update scheduler settings |
| `POST /api/auto-unblock/run` | Manually trigger auto-unblock |

## Tech Stack

- **Backend**: Python, FastAPI, httpx (async HTTP), Jinja2
- **Frontend**: Vanilla JavaScript, Chart.js, CSS (no build step)
- **Architecture**: Monolithic - single process serves API + frontend
- **Why no React/Next.js?**: Simplicity. No build tools, no node_modules, single `pip install` and run. The FastAPI backend exposes clean JSON endpoints if you ever want to swap in a different frontend.

## Requirements

- Python 3.10+
- A running ListMonk instance with API access
