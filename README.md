# EventFlow

A Streamlit dashboard for event feedback. It loads registration, attendance, star-rating and comment data
from Firebase Firestore (or a local CSV), scores each comment with VADER sentiment analysis, checks whether
the comment's mood matches its star rating, and charts the results per event.

## What's new in v2

- **Realistic simulation:** each simulated event has its own fixed attendance rate, typical rating and date, and
  people can register for several events. (In v1 every event was statistically identical.)
- **Rankings that account for sample size:** Top Performer and Critical Drop-off use averages adjusted for how
  many responses each event has, and event averages show 95% confidence intervals (also as error bars on the
  Performance Matrix).
- **Adjustable matrix thresholds:** fixed targets or event medians, set in the sidebar.
- **Event Drill-down tab:** rating distribution, most-mentioned words in positive and negative comments, and
  the responses whose rating and comment disagree, downloadable as CSV.
- **Read-limit warning** when Firestore holds more records than the dashboard reads (5,000).

v1.0 is tagged in git as `v1.0`.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

With no Firebase key configured, the app falls back to `eventflow_simulated_data.csv`
(choose **Local CSV Only** in the sidebar to use it directly).

## Firebase setup (optional)

The service-account key must stay **outside** this folder. The app looks for it in this order:

1. A `[firebase]` table in `.streamlit/secrets.toml` (the fields of the key JSON as TOML keys). This is the
   option to use on Streamlit Community Cloud.
2. The file path in the `EVENTFLOW_FIREBASE_KEY` environment variable.
3. `~/.config/eventflow/serviceAccountKey.json`

The app reads and writes the `Registrations_Feedback` collection.
**Wipe & Regenerate Data** in the sidebar deletes that whole collection before writing new simulated records.

## Files

| File | Purpose |
|---|---|
| `app.py` | The dashboard: data loading, cleaning, sentiment scoring, charts, and the Firestore simulator |
| `data_generator.py` | Standalone script that writes a simulated CSV without Firebase (`python3 data_generator.py`) |
| `eventflow_simulated_data.csv` | Sample simulated data used as the offline fallback |
