# ListMonk Dashboard

A modern, dark-themed dashboard for managing your self-hosted ListMonk instance. Built with FastAPI and vanilla JavaScript.

## Project Structure

```
Listmonk-Dashboard/
├── app/
│   ├── main.py          # FastAPI application entry point
│   ├── auth.py          # Authentication logic
│   ├── config.py        # Configuration management
│   ├── routers/         # API endpoints
│   │   ├── bounces.py
│   │   ├── campaigns.py
│   │   ├── converter.py
│   │   ├── lists.py
│   │   ├── subscribers.py
│   │   ├── templates.py
│   │   └── unsubscribes.py
│   ├── services/        # Business logic
│   │   ├── bounce_ingest.py
│   │   ├── csv_converter.py
│   │   ├── imap_unsubscribe.py
│   │   ├── link_unsubscribe.py
│   │   └── listmonk_client.py
│   └── models/          # (empty, uses Pydantic)
├── templates/           # HTML templates
├── static/              # Static assets
├── tests/              # Test files
└── docker-compose.yml  # Docker setup
```

## Technology Stack

- **Backend**: FastAPI (Python 3.10+)
- **Frontend**: Vanilla JavaScript (no framework)
- **API Client**: ListMonk REST API
- **Deployment**: Docker

## General Rules

- **Never make up file paths** — verify before editing
- **Never assume** — check current state before acting
- **Never skip tests** — verify changes work before reporting completion
- **Use the skills** — they exist for a reason, use them proactively
- **Think like a senior** — anticipate edge cases, consider long-term maintainability

## Workflow Requirements

1. **Before coding**: Understand the full scope, check existing patterns in the codebase
2. **During coding**: Use appropriate skill, follow established patterns, keep functions small
3. **After coding**: Test the feature manually, check for regressions, ensure type safety

## Testing Checklist

- [ ] Dev server runs without errors
- [ ] Login flow works
- [ ] Data loads from API
- [ ] No console errors
- [ ] Edge cases handled (empty data, API failures)