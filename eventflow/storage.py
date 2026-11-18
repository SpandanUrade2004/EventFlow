"""Firestore and CSV access.

The service-account key must never live inside the project folder. It is loaded from, in order:
  1. A `firebase` table in Streamlit secrets (.streamlit/secrets.toml or the Streamlit Cloud secrets UI)
  2. The file named by the EVENTFLOW_FIREBASE_KEY environment variable
  3. ~/.config/eventflow/serviceAccountKey.json
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import firebase_admin
import pandas as pd
from firebase_admin import credentials, firestore

DEFAULT_COLLECTION = "Registrations_Feedback"
FIRESTORE_READ_LIMIT = 5000  # newest N records are read; callers are told if more exist
DEFAULT_KEY_PATH = os.path.expanduser("~/.config/eventflow/serviceAccountKey.json")
CSV_PATH = Path(__file__).resolve().parent.parent / "eventflow_simulated_data.csv"


def collection_name(secrets=None):
    """Firestore collection to use: EVENTFLOW_COLLECTION env var, then `collection` in secrets, else the default."""
    if os.environ.get("EVENTFLOW_COLLECTION"):
        return os.environ["EVENTFLOW_COLLECTION"]
    try:
        if secrets is not None and "collection" in secrets:
            return str(secrets["collection"])
    except Exception:
        pass
    return DEFAULT_COLLECTION


def load_credentials(secrets=None):
    """Returns (Certificate, None) or (None, error message). `secrets` is a mapping such as st.secrets."""
    try:
        if secrets is not None and "firebase" in secrets:
            return credentials.Certificate(dict(secrets["firebase"])), None
    except Exception:
        pass  # no secrets file; fall through to file-based lookup

    key_path = os.environ.get("EVENTFLOW_FIREBASE_KEY", DEFAULT_KEY_PATH)
    if not os.path.exists(key_path):
        return None, (f"No Firebase key found. Add a [firebase] section to .streamlit/secrets.toml, "
                      f"set EVENTFLOW_FIREBASE_KEY, or place the key at {DEFAULT_KEY_PATH}.")
    if os.path.getsize(key_path) == 0:
        return None, f"Key file '{key_path}' is empty."
    with open(key_path) as f:
        json.load(f)  # fail early with a clear error on malformed JSON
    return credentials.Certificate(key_path), None


def init_firebase(secrets=None):
    """Initialises the Firebase app once. Returns (ok, message)."""
    if firebase_admin._apps:
        return True, "Already Initialized"
    try:
        cred, err = load_credentials(secrets)
        if err:
            return False, err
        firebase_admin.initialize_app(cred)
        return True, "Success"
    except Exception as e:
        return False, str(e)


def fetch_firestore(collection=DEFAULT_COLLECTION, limit=FIRESTORE_READ_LIMIT):
    """Newest `limit` records as a DataFrame, plus whether more exist. Requires init_firebase()."""
    db = firestore.client()
    # Ask for one extra document so we can tell whether the limit cut anything off.
    docs = db.collection(collection).order_by(
        'Timestamp', direction=firestore.Query.DESCENDING).limit(limit + 1).get()
    truncated = len(docs) > limit
    return pd.DataFrame([d.to_dict() for d in docs[:limit]]), truncated


def replace_firestore(records, collection=DEFAULT_COLLECTION, progress=print):
    """Deletes every document in the collection, then writes `records`. Irreversible."""
    db = firestore.client()
    collection_ref = db.collection(collection)
    progress("Purging Cloud Collection...")
    docs = collection_ref.limit(500).get()
    while docs:
        batch = db.batch()
        for doc in docs:
            batch.delete(doc.reference)
        batch.commit()
        docs = collection_ref.limit(500).get()

    progress(f"Writing {len(records)} records...")
    batch = db.batch()
    for i, data in enumerate(records):
        batch.set(collection_ref.document(), {**data, "Timestamp": firestore.SERVER_TIMESTAMP})
        if (i + 1) % 400 == 0:
            batch.commit()
            batch = db.batch()
    batch.commit()


def save_csv(records, path=CSV_PATH):
    """Writes records to CSV with the current time (Firestore's SERVER_TIMESTAMP can't be serialised)."""
    now = datetime.now(timezone.utc).isoformat()
    pd.DataFrame([{**r, "Timestamp": now} for r in records]).to_csv(path, index=False)


def records_from_frame(df):
    """Turns an analysis-ready frame back into plain records for storage (drops derived columns)."""
    cols = ["User_ID", "Event_Name", "Event_Type", "Event_Date", "Registered", "Attended", "Rating",
            "Feedback_Text", "Source"]
    out = df[[c for c in cols if c in df.columns]].copy()
    if "Event_Date" in out:
        out["Event_Date"] = out["Event_Date"].map(lambda d: d.date().isoformat() if pd.notna(d) else None)
    if "Feedback_Text" in out:
        out["Feedback_Text"] = out["Feedback_Text"].where(out["Feedback_Text"] != "No Comment Provided", None)
    out = out.astype(object).where(out.notna(), None)  # NaN/NaT -> None, which Firestore stores as null
    return out.to_dict("records")


def load_csv(path=CSV_PATH):
    return pd.read_csv(path) if os.path.exists(path) else pd.DataFrame()
