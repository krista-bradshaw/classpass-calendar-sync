# ClassPass → iCloud Calendar Sync

Automatically syncs your ClassPass upcoming bookings to iCloud Calendar every night at 8pm. Adds new bookings, removes cancelled ones.

ClassPass has no public API, no iCal feed, and no OOTB integrations.... so I built this to automatically sync reservations to my calendar.

## How it works

1. GitHub Actions spins up a headless Chrome browser on a schedule
2. Logs into ClassPass and fetches your upcoming reservations via the internal API
3. Connects to iCloud Calendar via CalDAV
4. Adds new bookings and removes cancelled ones
5. Shuts down — your other calendar events are never touched

## Setup

### 1. Fork repo

### 2. Generate an Apple app-specific password

1. Go to [appleid.apple.com](https://appleid.apple.com)
2. Sign-In & Security → App-Specific Passwords
3. Generate one, label it `ClassPass Sync`
4. Save it somewhere safe

### 3. Add GitHub secrets

Go to your repo → Settings → Secrets and variables → Actions → New repository secret.

Add all five:

| Secret               | Value                                               |
| -------------------- | --------------------------------------------------- |
| `CLASSPASS_EMAIL`    | Your ClassPass login email                          |
| `CLASSPASS_PASSWORD` | Your ClassPass password                             |
| `APPLE_ID`           | Your iCloud email address                           |
| `APPLE_APP_PASSWORD` | The app-specific password from step 2               |
| `CALENDAR_NAME`      | Exact name of your iCloud calendar (e.g. `pilates`) |

### 4. Test it

Go to Actions → ClassPass Calendar Sync → Run workflow. Watch the logs. First run takes 3-4 minutes while it downloads the browser. Subsequent runs are faster thanks to caching.

## Schedule

Runs every night at 8pm UTC (8pm London time in winter, 9pm in summer).

To change the time, edit the cron in `.github/workflows/sync.yml`:

```yaml
- cron: '0 20 * * *' # 8pm UTC
```

## What gets synced

- **New bookings** → added to your iCloud calendar with class name, studio, time, and address
- **Cancelled bookings** → removed from your iCloud calendar automatically
- **Existing bookings** → skipped (no duplicates)

Only events created by this sync are ever touched. Your other calendar events are never affected.
