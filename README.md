# EventFlow

[![Live demo](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://eventflow-dashboard.streamlit.app)
[![CI](https://github.com/SpandanUrade2004/EventFlow/actions/workflows/ci.yml/badge.svg)](https://github.com/SpandanUrade2004/EventFlow/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Event feedback analytics: which events are worth repeating, and can you trust the feedback?**

EventFlow is a Streamlit dashboard for event organisers. It takes registrations, attendance, star ratings and
written comments, scores each comment with sentiment analysis, checks whether the comment agrees with its star
rating, and ranks events in a way that accounts for how many responses each one has.

**Try it now: [eventflow-dashboard.streamlit.app](https://eventflow-dashboard.streamlit.app)**, with no installation.
(If nobody has opened it for a while, it takes a few seconds to wake up.)

Use it on **your own data** by uploading a CSV or Excel file (a Google Forms export works as-is), connect it to
**your own Firebase** project, or explore the bundled sample data with no setup at all.

![Executive summary](docs/screenshots/overview.png)

## Features

- **Executive summary:** top performer, attendance, worst drop-off and rating–comment consistency at a glance.
- **Performance Matrix:** drop-off rate vs satisfaction, with 95% confidence intervals as error bars and
  quadrant lines you can set in the sidebar (fixed targets or event medians).
- **Rankings that account for sample size:** an event with three 5-star ratings can't beat one with 300 ratings
  averaging 4.8. Rankings use averages shrunk toward the overall mean (a Bayesian average).
- **Event Drill-down:** rating distribution, the most-mentioned words in positive and negative comments, and the
  responses whose stars and comment disagree, downloadable as CSV.
- **Sentiment validation:** VADER sentiment scores, a per-response rank correlation between rating and sentiment,
  and a match rate per event.
- **Event category comparison:** your own categories, or Technical vs General when your data has none.
- **Upload your own data:** CSV or Excel, with automatic column matching, value conversion and a validation
  report.
- **Built-in data simulator** with deliberate noise (missing ratings, contradicting comments, duplicates) to
  exercise the cleaning steps. Each simulated event has its own attendance rate, typical rating and date.

![Performance Matrix](docs/screenshots/performance-matrix.png)

![Event Drill-down](docs/screenshots/drill-down.png)

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

With no Firebase key configured, the app uses the bundled sample data in `eventflow_simulated_data.csv`.

## Use your own data

1. Export your responses as **CSV or Excel**. Google Forms, Microsoft Forms, a registration spreadsheet or a
   ticketing export all work.
2. In the sidebar, set **Data source** to **Upload your own file** and choose the file.
3. Check the column matching. EventFlow matches your column names automatically (e.g. *"How would you rate the
   event? (1-10)"* → Star rating), and you can correct any match.

| EventFlow column | Needed? | Accepted values |
|---|---|---|
| Event name | **required** | any text |
| Star rating | rating and/or comment | numbers on a 1–5 or 1–10 scale; text like "4 stars" is fine |
| Comment | rating and/or comment | free text (English, see Limitations) |
| Attended? | optional | Yes/No, Y/N, True/False, 1/0, Present/Absent. Without it, drop-off can't be measured |
| Person ID | optional | anything unique per person; used for de-duplication and returning attendees |
| Event category | optional | your own categories; otherwise Technical/General is guessed from the name |
| Event date | optional | any common date format |

EventFlow then shows how many rows it used and why it skipped or changed any (blank event names, unrecognised
Attended values, ratings out of range). A ready-made example is in
[`docs/eventflow_template.csv`](docs/eventflow_template.csv), and it can also be downloaded from the sidebar.

**Privacy.** Uploaded files are analysed in your browser session only, and nothing is written to disk or the cloud
unless an admin chooses **Save upload to Firestore**. Prefer anonymous IDs (e.g. `p001`) over names or emails.
Keep real exports in a `data/` folder: it, and all `.xlsx`/`.xls` files, are git-ignored so they can't be committed
by accident.

## Use your own Firebase (optional)

Firebase is only needed if you want data stored in the cloud. **The app never uses anyone else's Firebase:**
credentials are never part of this repository, so a fresh copy uses your own project or none at all.

1. Create a project at [console.firebase.google.com](https://console.firebase.google.com) (Analytics can be off).
2. Go to **Build → Firestore Database → Create database** and choose **production mode** and a location near you.
   The app connects with an admin key, which isn't affected by security rules, so production mode keeps the
   database closed to everyone else.
3. Go to **Project settings → Service accounts → Generate new private key**. A JSON key file downloads.
4. Give the key to EventFlow in **one** of these ways. Never put it inside the project folder:
   - Move it to `~/.config/eventflow/serviceAccountKey.json`. This is the simplest option.
   - Set `EVENTFLOW_FIREBASE_KEY=/path/to/key.json`.
   - On Streamlit Cloud, paste the key's fields into the app's **Secrets** as a `[firebase]` table:

     ```toml
     [firebase]
     type = "service_account"
     project_id = "your-project-id"
     private_key_id = "..."
     private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
     client_email = "firebase-adminsdk-...@your-project-id.iam.gserviceaccount.com"
     # ...and the remaining fields from the JSON file
     ```

5. Fill it: upload a file and use **Save upload to Firestore** (admin mode), or generate sample data with the
   admin simulator.

The app reads the newest 5,000 records from the `Registrations_Feedback` collection and warns if there are more.
To use a different collection, set `EVENTFLOW_COLLECTION=my_collection` or `collection = "my_collection"` in
secrets.

## Admin tools: generating data

The data simulator is for admins only, so ordinary viewers never see it. There are two ways to use it, and both
use the same simulator.

**1. Admin panel in the dashboard.** Hidden by default. Turn it on with:

```bash
EVENTFLOW_ADMIN=1 streamlit run app.py
```

On Streamlit Cloud, add `admin = true` to the app's secrets instead, and leave it off on any public deployment.
The sidebar then shows **Simulation Config (admin)**. **Write to** has two choices:

- **Local CSV only** (the default) replaces the sample CSV and never touches Firestore.
- **Firestore + local CSV** **deletes the whole Firestore collection** before writing new records.

When an uploaded file is loaded, admins also get **Save upload to Firestore**, which replaces the collection with
the uploaded rows.

**2. Command line, with no Firebase needed:**

```bash
python data_generator.py --records 800 --profile Realistic --force
```

| Option | Default | Meaning |
|---|---|---|
| `--records` | 600 | registrations to simulate |
| `--events` | the 5 default events | comma-separated event names |
| `--profile` | `Realistic` | `Realistic`, `Positive` or `Critical` |
| `--seed` | random | set for reproducible data |
| `--output` | `eventflow_simulated_data.csv` | CSV to write |
| `--force` | off | needed to overwrite an existing file |

## Host your own copy online (free)

**Just want to use EventFlow?** Open the [live app](https://eventflow-dashboard.streamlit.app); nothing to install.
This section is for people who want **their own** online copy, for example connected to their own Firebase, with
admin tools for their team, or running a modified fork. It uses Streamlit Community Cloud, which is free.

1. Fork this repository (or push your copy) to GitHub. A **public** repo is simplest: unlimited free public apps.
2. Sign in at [share.streamlit.io](https://share.streamlit.io) with GitHub and click **Create app → Deploy a public
   app from GitHub**.
3. Choose the repository, branch `main` and main file `app.py`. Under **Advanced settings**, pick Python **3.12**.
4. Optional: in **Secrets**, add a `[firebase]` table (see above) to show your Firestore data, `collection = "..."`
   for a different collection, or `admin = true` for the admin tools. **Leave admin off on a public app.** Without
   secrets, visitors see the sample data and can upload their own files.
5. Click **Deploy**.

**Updating:** every `git push` to `main` redeploys the app automatically within a minute or so. Secrets live in the
app's settings on share.streamlit.io, not in git.

**Free-tier notes:** apps sleep after 12 hours without visitors and take a moment to wake up on the next visit, and
memory is modest (comfortable for exports up to tens of thousands of rows). Each visitor's uploaded file stays in
their own session.

## Development

```bash
pip install -r requirements-dev.txt
pytest          # unit tests for simulation, cleaning and statistics
ruff check .    # lint
```

GitHub Actions runs both on every push and pull request (`.github/workflows/ci.yml`).

## Project structure

| Path | Purpose |
|---|---|
| `app.py` | Streamlit UI: layout, charts and caching |
| `eventflow/simulation.py` | Synthetic data with per-event profiles and injected noise |
| `eventflow/ingest.py` | Upload support: reading CSV/Excel, column matching, value conversion, validation |
| `eventflow/processing.py` | Cleaning, deduplication, VADER scoring and rating–comment alignment |
| `eventflow/analytics.py` | Event statistics, confidence intervals, adjusted rankings, keywords |
| `eventflow/storage.py` | Firebase credentials, Firestore reads and writes, CSV I/O |
| `data_generator.py` | Admin CLI: writes a simulated CSV without Firebase |
| `tests/` | pytest suite |
| `eventflow_simulated_data.csv` | Bundled sample data (simulated) |
| `docs/eventflow_template.csv` | Example file for uploads |

## Limitations

- The bundled sample data is simulated, so it shows how the pipeline behaves, not real attendee opinion.
- VADER sentiment is rule-based and **English-only**. It can misread sarcasm, domain-specific wording or comments in
  other languages, and the rating–comment checks inherit those errors.
- With only a few events per category, category comparisons are indicative rather than conclusive.

## Versions

- **v2.1:** upload your own CSV/Excel data with column matching and validation, user-defined event categories,
  configurable Firestore collection, guide to using your own Firebase.
- **v2.0:** per-event simulation, sample-size-aware rankings with confidence intervals, drill-down, admin mode,
  `eventflow` package with tests and CI.
- **v1.0:** bug fixes, secure key loading, per-response correlation. Tagged `v1.0`.

## License

[MIT](LICENSE)
