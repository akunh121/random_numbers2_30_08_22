# Local Lotto Stats Updater (Windows)

A small Python script that runs on a 24/7 Windows machine, downloads the latest
Israeli Lotto results directly from `pais.co.il` (which only allows Israeli IPs),
and pushes them straight to GitHub via the Contents API.

No `git` installation required — only Python 3.

## One-time setup

### 1. Get the script

Download or copy `update_local.py` and `update_local.bat` to a stable folder,
for example `C:\lotto-updater\`. Keep both files together.

### 2. Create a GitHub Personal Access Token

1. Open https://github.com/settings/tokens?type=beta
2. Click **Generate new token** (fine-grained)
3. Settings:
   - **Token name**: `lotto-updater`
   - **Expiration**: 1 year (set a reminder to renew)
   - **Repository access**: Only select repositories → choose
     `akunh121/random_numbers2_30_08_22`
   - **Repository permissions** → **Contents**: **Read and write**
4. Click **Generate token** and copy the value (starts with `github_pat_…`).
   You won't see it again.

### 3. Save the token next to the script

Create `config.json` next to `update_local.py` with this content:

```json
{
  "github_token": "github_pat_XXXXXXXXXXXXXXXXXXXXXXXXX"
}
```

(Alternatively, set the environment variable `LOTTO_GITHUB_TOKEN`.)

### 4. Test it manually

Open Command Prompt in that folder and run:

```cmd
python update_local.py
```

You should see something like:

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

If it shows `unchanged` for all files — that means GitHub already has the
latest, which is also a success.

### 5. Schedule it daily with Task Scheduler

1. Press **Win + R**, type `taskschd.msc`, Enter
2. Right-click **Task Scheduler Library** → **Create Basic Task**
3. **Name**: Lotto updater
4. **Trigger**: Daily → start time `07:30` (after the late draws are
   published)
5. **Action**: Start a program
6. **Program/script**: `C:\lotto-updater\update_local.bat`
   (the full path to the .bat file)
7. **Start in**: `C:\lotto-updater\` (folder containing the script)
8. Finish

Right-click the new task → **Run** to verify it works on demand. A
`update_local.log` file will appear next to the script with the full output of
each run.

## Troubleshooting

- **`GitHub API 401`** — token is wrong or expired. Regenerate.
- **`GitHub API 403`** — token doesn't have `Contents: write` on this
  repo, or repo access is misconfigured. Re-check step 2.
- **`urllib.error.URLError: <urlopen error [WinError 10060]>`** — pais
  blocked the request. Wait and retry; if persistent, your home connection
  may be temporarily flagged.
- **Hebrew shows as `?????`** in the log — cosmetic only, the data
  pushed to GitHub is correct UTF-8.
