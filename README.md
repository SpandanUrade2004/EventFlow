# EventFlow

**Event feedback analytics: which events are worth repeating, and can you trust the feedback?**

EventFlow is a Streamlit dashboard for event organisers. It takes registrations, attendance, star ratings and
written comments, scores each comment with sentiment analysis, checks whether the comment agrees with its star
rating, and ranks events in a way that accounts for how many responses each one has. It reads from Firebase
Firestore, or from a local CSV so it runs with no setup at all.

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
- **Technical vs General** event comparison.
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

## Firebase setup (optional)

The service-account key must stay **outside** this folder. The app looks for it in this order:

1. A `[firebase]` table in `.streamlit/secrets.toml` (the fields of the key JSON as TOML keys). Use this on
   Streamlit Community Cloud.
2. The file path in the `EVENTFLOW_FIREBASE_KEY` environment variable.
3. `~/.config/eventflow/serviceAccountKey.json`

The app reads the newest 5,000 records from the `Registrations_Feedback` collection and warns if there are more.

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
| `eventflow/processing.py` | Cleaning, deduplication, VADER scoring and rating–comment alignment |
| `eventflow/analytics.py` | Event statistics, confidence intervals, adjusted rankings, keywords |
| `eventflow/storage.py` | Firebase credentials, Firestore reads and writes, CSV I/O |
| `data_generator.py` | Admin CLI: writes a simulated CSV without Firebase |
| `tests/` | pytest suite |
| `eventflow_simulated_data.csv` | Bundled sample data (simulated) |

## Limitations

All bundled data is simulated, so the dashboard shows how the pipeline behaves rather than real attendee opinion.
VADER is a rule-based model and can misread sarcasm or domain-specific wording.

## Versions

- **v2.0:** per-event simulation, sample-size-aware rankings with confidence intervals, drill-down, admin mode,
  `eventflow` package with tests and CI.
- **v1.0:** bug fixes, secure key loading, per-response correlation. Tagged `v1.0`.

## License

[MIT](LICENSE)
