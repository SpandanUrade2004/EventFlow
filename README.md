# EventFlow

A Streamlit dashboard for event feedback. It loads registration, attendance, star-rating and comment data
from Firebase Firestore (or a local CSV), scores each comment with VADER sentiment analysis, checks whether
the comment's mood matches its star rating, and charts the results per event.

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
