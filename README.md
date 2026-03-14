# whatsapp-health-bot

## Survey Reminder Flow (PID + Qualtrics)

The app now supports:

- PID-based patient matching (`P01`, `P02`, ...)
- Multi-survey management from admin dashboard (add as many survey links as needed)
- Daily Qualtrics response sync per active survey (when Survey ID is provided)
- Daily Cantonese WhatsApp reminders to patients whose PID has not responded for each survey
- Mock seeding for `P01` to `P10` using shared phone `85252624849`

## Required Environment Variables

- `QUALTRICS_BASE_URL` (for example `https://yourdatacenterid.qualtrics.com`)
- `QUALTRICS_API_TOKEN`
- `QUALTRICS_SURVEY_ID` (used by fallback/default survey mode)
- `QUALTRICS_SURVEY_LINK` (used by fallback/default survey mode)
- `QUALTRICS_PID_FIELD` (optional global default, default `PID`)

In normal operation, add surveys in the dashboard Survey Center with:

- Survey title
- Survey link
- Optional Qualtrics Survey ID
- Optional PID field override
- Active/inactive toggle

## Scheduler

Survey reminder job runs daily at 09:00 (server local time):

- Load all active survey links
- Sync Qualtrics responses into local table for each survey that has a Qualtrics Survey ID
- Compare submitted PIDs with local patients for each survey
- Send Cantonese reminder with each survey link to non-responders only
- Avoid duplicate send in the same day using per-survey reminder logs

## Mock Data Seeding

Mock records are seeded idempotently as `P01` to `P10` with shared phone `85252624849`.

- Automatically during DB migration/startup
- Manually from dashboard via `Seed Mock P01-P10` button

