# TUTU Scraper — Complete Setup Guide

Complete this guide **once** from top to bottom and the scraper will run automatically on GitHub every 5 minutes, forever.

Estimated time: **15–20 minutes**

---

## CHECKLIST — Approve Everything At Once

Before starting, here is every permission and access grant you will need to approve. Nothing else will be required later.

| # | What | Where | Why |
|---|------|-------|-----|
| 1 | Create a Google Cloud project | console.cloud.google.com | To generate API credentials |
| 2 | Enable the Google Sheets API | Google Cloud Console | Allows the scraper to read/write your sheet |
| 3 | Create a Service Account | Google Cloud Console | A "bot identity" that can access your sheet |
| 4 | Download credentials.json | Google Cloud Console | The key the scraper uses to authenticate |
| 5 | Share your Google Sheet (Editor) with the service account email | Your Google Sheet | Gives the bot permission to write rows |
| 6 | Create a GitHub repository (Public) | github.com | Where the code lives and runs |
| 7 | Add `GOOGLE_CREDENTIALS_JSON` as a GitHub Secret | GitHub → Settings → Secrets | Gives GitHub Actions the Google credentials securely |
| 8 | Add `SPREADSHEET_ID` as a GitHub Secret | GitHub → Settings → Secrets | Tells the scraper which sheet to write to |

That's it — 8 approvals total, all described step by step below.

---

## STEP 1 — Create a Google Cloud Project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click the project dropdown at the top → **New Project**
3. Name it `tutu-scraper` → click **Create**
4. Make sure the new project is selected in the top dropdown

---

## STEP 2 — Enable the Google Sheets API

1. In Google Cloud Console, go to **APIs & Services → Library**
2. Search for `Google Sheets API`
3. Click it → click **Enable**

> ✅ You will see "API enabled" when done.

---

## STEP 3 — Create a Service Account & Download credentials.json

A Service Account is a "robot identity" — it has its own email address and can be granted access to your sheet.

1. Go to **APIs & Services → Credentials**
2. Click **+ Create Credentials → Service Account**
3. Name: `tutu-scraper-agent` → click **Create and Continue**
4. Role: skip (click **Continue** without selecting a role)
5. Click **Done**
6. You will now see the service account listed. Click its email address.
7. Go to the **Keys** tab → **Add Key → Create New Key → JSON → Create**
8. A `credentials.json` file downloads automatically to your computer.

> ✅ Keep this file safe. You will use it in Step 5.

> ⚠️ Never commit credentials.json to GitHub. The `.gitignore` already blocks it.

---

## STEP 4 — Share Your Google Sheet with the Service Account

1. Open your Google Sheet:
   [https://docs.google.com/spreadsheets/d/1GQBintbXyiXFEHmVAwrK467nJ8121GECe13kPUtOi-Y](https://docs.google.com/spreadsheets/d/1GQBintbXyiXFEHmVAwrK467nJ8121GECe13kPUtOi-Y)
2. Click the **Share** button (top right)
3. In the "Add people" field, paste the service account email address.
   - It looks like: `tutu-scraper-agent@tutu-scraper.iam.gserviceaccount.com`
   - Find the exact email in `credentials.json` under the key `"client_email"`
4. Set permission to **Editor**
5. Click **Send** (ignore the "This is a Google account" warning if it appears)

> ✅ The scraper can now write to your sheet.

---

## STEP 5 — Create the GitHub Repository

1. Go to [github.com](https://github.com) → **New repository**
2. Name: `tutu-scraper`
3. Set visibility to **Public** ← Important! (Public = unlimited free Actions minutes)
4. Click **Create repository**
5. Upload all the files from the `tutu_scraper/` folder to this repository.
   (Drag and drop the folder contents onto the GitHub page, or use `git push`)

> ✅ Your code is now on GitHub.

---

## STEP 6 — Convert credentials.json to a Base64 Secret

GitHub Secrets cannot store files — only text. We convert `credentials.json` to a single-line base64 string.

**On Mac or Linux**, open a terminal and run:
```bash
base64 -i credentials.json | tr -d '\n'
```

**On Windows (PowerShell)**, run:
```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("credentials.json"))
```

Copy the entire output string. You will paste it in the next step.

---

## STEP 7 — Add GitHub Secrets

Secrets are encrypted environment variables that GitHub Actions can read but no one else can see.

1. In your GitHub repository, go to **Settings → Secrets and variables → Actions**
2. Click **New repository secret** and add each secret below:

### Secret 1 — Google Credentials

| Field | Value |
|-------|-------|
| **Name** | `GOOGLE_CREDENTIALS_JSON` |
| **Value** | The base64 string you generated in Step 6 |

### Secret 2 — Spreadsheet ID

| Field | Value |
|-------|-------|
| **Name** | `SPREADSHEET_ID` |
| **Value** | `1GQBintbXyiXFEHmVAwrK467nJ8121GECe13kPUtOi-Y` |

> ✅ Both secrets are now stored. GitHub will inject them at runtime.

---

## STEP 8 — Verify the Workflow Runs

1. In your repository, go to the **Actions** tab
2. You should see the workflow `TUTU Scraper Agent` listed
3. Either wait up to 5 minutes for the first automatic run, or click **Run workflow → Run workflow** to trigger it immediately
4. Click the run → click the `scrape` job → watch the logs in real time

A successful run looks like:
```
▶ Running: Yad2
  ✓ Yad2: 47 listings fetched
▶ Running: Homeless
  ✓ Homeless: 12 listings fetched
▶ Running: Madlan
  ✓ Madlan: 0 listings fetched
Total fetched across all sources: 59
New rows written to Google Sheets: 59
Cycle complete in 14.3s.
```

> ✅ Check your Google Sheet — new rows should be appearing.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Actions tab shows no workflow | Make sure the file `.github/workflows/scraper.yml` exists in your repository |
| Run fails with `403 Forbidden` | The service account email doesn't have Editor access to the sheet — redo Step 4 |
| Run fails with `GOOGLE_CREDENTIALS_JSON decode error` | The base64 string wasn't copied fully — redo Steps 6 and 7 |
| `0 listings fetched` from Yad2 or Homeless | The site may have changed their API. Check the run logs for the specific error, then see the note in the scraper file about inspecting the browser Network tab |
| Workflow only runs every 10 min instead of 5 | GitHub occasionally batches close-interval cron jobs under high platform load — this is normal |
| Private repo running out of free minutes | Change the repository to Public (Settings → General → Danger Zone → Change visibility) |
