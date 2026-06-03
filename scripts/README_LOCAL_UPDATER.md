# Local Lotto Stats Updater (Windows)

A small Python daemon that runs on a 24/7 Windows machine. It schedules
**itself** for **Tuesday, Thursday and Saturday at 23:55** local time —
about two hours after each Israeli Lotto draw, when fresh results are
reliably published on `pais.co.il`. It downloads the latest CSV,
rebuilds the stats JSON, scrapes the current jackpot, and pushes all
three files straight to GitHub via the Contents API.

No `git` installation required — only Python 3.

## What runs when

The schedule is hard-coded in `update_local.py`:

```python
SCHEDULE_DAYS = {1, 3, 5}      # Tuesday, Thursday, Saturday
SCHEDULE_HOUR = 23
SCHEDULE_MIN = 55
```

The daemon stays alive forever, recomputes the next slot every cycle
(so it survives DST changes, system sleep/resume, and clock fixes),
sleeps until then, runs once, and goes back to sleep.

Task Scheduler is only used to **start** the daemon on boot. The
timing of the actual lotto refresh lives inside the script.

## One-time setup

### 1. Copy the files

Put these three files in a stable folder, e.g. `C:\lotto-updater\`:

- `update_local.py` — the daemon
- `update_local.bat` — auto-restart wrapper used by Task Scheduler
- `update_test.bat` — one-shot test wrapper for manual runs

### 2. Create a GitHub Personal Access Token

1. Open https://github.com/settings/tokens?type=beta
2. Click **Generate new token** (fine-grained).
3. Settings:
   - **Token name**: `lotto-updater`
   - **Expiration**: 1 year (set a calendar reminder to renew).
   - **Repository access**: Only select repositories →
     `akunh121/random_numbers2_30_08_22`.
   - **Repository permissions** → **Contents** → **Read and write**.
4. **Generate token** and copy the value (`github_pat_…`).
   You won't see it again.

### 3. Save the token next to the script

Create `config.json` next to `update_local.py`:

```json
{
  "github_token": "github_pat_XXXXXXXXXXXXXXXXXXXXXXXXX"
}
```

(Alternatively, set the environment variable `LOTTO_GITHUB_TOKEN`.)

### 4. Test it once manually

Double-click `update_test.bat`. A console window opens and you should
see something like:

```
Downloading CSV from pais.co.il ...
  got 181,600 bytes
Building stats ...
  2454 draws · latest #3931 on 02/06/2026
Scraping jackpot ...
  {'firstPrize': '40 מיליון', 'secondPrize': '750,000 ₪'}
Pushing to GitHub ...
  public/lotto-history.csv: pushed
  public/lotto-stats.json: pushed
  public/lotto-jackpot.json: pushed
Done. 3 file(s) changed.
```

If GitHub already has the latest, you'll see `unchanged` lines —
that's also a success. Close the window when done.

### 5. Launch the daemon on every boot

We want Windows to start `update_local.bat` automatically and keep it
running in the background.

1. Press **Win + R**, type `taskschd.msc`, Enter.
2. Right-click **Task Scheduler Library** → **Create Task**
   (not the "Basic" wizard).
3. **General** tab:
   - **Name**: `Lotto updater daemon`
   - Tick **Run whether user is logged on or not**.
   - Tick **Run with highest privileges**.
4. **Triggers** tab → **New**:
   - **Begin the task**: **At startup**
   - (optional) Delay task for: **2 minutes**, so the network is ready.
   - Enabled ✓ → OK.
5. **Actions** tab → **New**:
   - **Action**: Start a program.
   - **Program/script**: `C:\lotto-updater\update_local.bat`
   - **Start in (optional)**: `C:\lotto-updater\`
   - OK.
6. **Conditions** tab:
   - Untick **Start the task only if the computer is on AC power**
     (so it still runs on a laptop).
7. **Settings** tab:
   - Untick **Stop the task if it runs longer than** — the daemon is
     meant to live forever.
   - Tick **If the task fails, restart every 1 minute, attempt 3 times**.
8. OK → enter Windows password if prompted.

Right-click the new task → **Run** to start the daemon now without
waiting for a reboot. The console window will be hidden (because of
"Run whether user is logged on or not"), so monitor progress via:

```
C:\lotto-updater\update_local.log
```

The log shows the next scheduled run, every sleep cycle, and the full
result of every update.

## Verifying it works

Tail the log file from the same folder:

```cmd
powershell -command "Get-Content update_local.log -Wait -Tail 20"
```

You should see lines like:

```
Daemon started. Schedule (local time): שלישי / חמישי / שבת at 23:55.
Next run: Tue 2026-06-09 23:55 (in 50100s; sleeping 1800s)
Next run: Tue 2026-06-09 23:55 (in 48300s; sleeping 1800s)
…
=== 2026-06-09 23:55:00 — running scheduled update ===
Downloading CSV from pais.co.il ...
…
```

## Troubleshooting

- **`GitHub API 401`** — token is wrong or expired. Regenerate.
- **`GitHub API 403`** — token doesn't have `Contents: write` on this
  repo, or repo access is misconfigured. Re-check step 2.
- **`urllib.error.URLError: <urlopen error [WinError 10060]>`** —
  pais.co.il blocked the request. The daemon catches this and tries
  again at the next scheduled slot. If persistent, your home connection
  may be temporarily flagged.
- **Hebrew shows as `?????` in the log** — cosmetic only, the data
  pushed to GitHub is correct UTF-8.
- **Daemon not running after reboot** — open Task Scheduler, locate
  `Lotto updater daemon`, right-click → **Run**, and check
  **Last Run Result** in the **History** tab.

## Changing the schedule

Edit the three constants at the top of `update_local.py` and restart
the daemon (Task Scheduler → right-click → End → Run).
