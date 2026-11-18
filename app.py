import streamlit as st
import pandas as pd
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import plotly.express as px
import os
import random
import uuid
import json
import re
import string
from collections import Counter
from datetime import datetime, timedelta, timezone

APP_VERSION = "2.0"

# --- 1. PAGE CONFIGURATION ---
st.set_page_config(page_title="EventFlow v2: Event Feedback Analytics Dashboard", layout="wide")

# Custom CSS for a professional "Analyst" look
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border: 1px solid #eee; }
    .insight-card { padding: 20px; border-radius: 12px; margin-bottom: 20px; border-left: 5px solid; min-height: 120px; }
    .success-card { background-color: #d1e7dd; border-left-color: #0f5132; color: #0f5132; }
    .warning-card { background-color: #fff3cd; border-left-color: #856404; color: #856404; }
    .error-card { background-color: #f8d7da; border-left-color: #842029; color: #842029; }
    .info-card { background-color: #e3f2fd; border-left-color: #1976d2; color: #1976d2; }
    .source-badge { padding: 8px 12px; border-radius: 8px; font-size: 0.85rem; font-weight: bold; display: block; margin-bottom: 20px; text-align: center; }
    .cloud-source { background-color: #d1e7dd; color: #0f5132; border: 1px solid #badbcc; }
    .local-source { background-color: #fff3cd; color: #664d03; border: 1px solid #ffecb5; }
    </style>
    """, unsafe_allow_html=True)


# --- 2. FIREBASE INITIALIZATION ---
# The service-account key must never live inside the project folder. It is loaded from, in order:
#   1. Streamlit secrets: a [firebase] table in .streamlit/secrets.toml (or the Streamlit Cloud secrets UI)
#   2. The file named by the EVENTFLOW_FIREBASE_KEY environment variable
#   3. ~/.config/eventflow/serviceAccountKey.json
DEFAULT_KEY_PATH = os.path.expanduser("~/.config/eventflow/serviceAccountKey.json")


def _load_firebase_credentials():
    try:
        if "firebase" in st.secrets:
            return credentials.Certificate(dict(st.secrets["firebase"])), None
    except Exception:
        pass  # No secrets.toml present; fall through to file-based lookup.

    key_path = os.environ.get("EVENTFLOW_FIREBASE_KEY", DEFAULT_KEY_PATH)
    if not os.path.exists(key_path):
        return None, (f"No Firebase key found. Add a [firebase] section to .streamlit/secrets.toml, "
                      f"set EVENTFLOW_FIREBASE_KEY, or place the key at {DEFAULT_KEY_PATH}.")
    if os.path.getsize(key_path) == 0:
        return None, f"Key file '{key_path}' is empty."
    with open(key_path, 'r') as f:
        json.load(f)
    return credentials.Certificate(key_path), None


def init_firebase():
    if not firebase_admin._apps:
        try:
            cred, err = _load_firebase_credentials()
            if err:
                return False, err
            firebase_admin.initialize_app(cred)
            return True, "Success"
        except Exception as e:
            return False, str(e)
    return True, "Already Initialized"


# --- 3. ENHANCED NOISY DATA SIMULATION ---
# Whole-word match so e.g. "Career Fair" is not tagged Technical because "fair" contains "ai".
TECH_PATTERN = re.compile(r"\b(tech\w*|ai|hack\w*|workshop\w*)\b")


MIXED_BAND = 0.5  # |VADER compound| below this counts as consistent with a 3-star rating


def classify_event(event_name):
    return "Technical" if TECH_PATTERN.search(str(event_name).lower()) else "General"


TEMPLATES = {
    5: ["Outstanding organization!", "The speaker was phenomenal.", "Exceeded all expectations.",
        "Best event ever."],
    4: ["Very good session, well worth it.", "Solid content and networking.", "Learned skills.", "Well organized."],
    3: ["It was okay, basic.", "Average experience.", "Good topics, poor management.", "Decent."],
    2: ["Disorganized, crowded.", "Content didn't match.", "Waste of time.", "Hard to follow."],
    1: ["Total disaster.", "Worst event.", "No coordination.", "Left early."]
}


def make_event_profiles(event_names, profile="Realistic", today=None):
    """Fixes each event's attendance probability, typical rating and date ONCE.

    (v1 re-drew these for every record, so all events were statistically identical and any
    difference between them on the dashboard was pure noise.)
    """
    today = today or datetime.now(timezone.utc).date()
    profiles = {}
    for name in event_names:
        if profile == "Positive":
            att_p, base_r = random.uniform(0.85, 0.95), random.uniform(4.1, 4.8)
        elif profile == "Critical":
            att_p, base_r = random.uniform(0.35, 0.6), random.uniform(1.6, 2.6)
        else:
            att_p, base_r = random.uniform(0.45, 0.95), random.uniform(2.0, 4.6)
        profiles[name] = {
            "att_p": att_p,
            "base_r": base_r,
            "date": (today - timedelta(days=random.randint(7, 120))).isoformat(),
        }
    return profiles


def generate_records(num_records, event_names, profile="Realistic"):
    """Builds simulated records (no I/O). Duplicates are injected as repeated dicts."""
    profiles = make_event_profiles(event_names, profile)
    # A shared pool of people, so some register for several events (enables returning-attendee stats).
    user_pool = [str(uuid.uuid4())[:8] for _ in range(max(10, int(num_records * 0.7)))]
    used_pairs = set()
    records = []
    for _ in range(num_records):
        event = random.choice(event_names)
        att_p, base_r = profiles[event]["att_p"], profiles[event]["base_r"]

        # One registration per person per event; fall back to a new person if the pool is exhausted.
        user_id = next((u for u in random.sample(user_pool, min(5, len(user_pool)))
                        if (u, event) not in used_pairs), str(uuid.uuid4())[:8])
        used_pairs.add((user_id, event))

        attended = np.random.choice([True, False], p=[att_p, 1 - att_p])
        rating, feedback = None, None

        if attended:
            # [NOISE] 5% chance of missing rating even if attended
            if random.random() > 0.05:
                # Round to nearest star (int() truncation biased every rating downward)
                rating = int(np.clip(np.rint(np.random.normal(base_r, 0.8)), 1, 5))

            # [NOISE] 10% chance of "Mismatch" (High rating, Negative text or vice-versa)
            is_mismatch = random.random() < 0.1
            sim_rating = rating if rating else 3
            if is_mismatch:
                mismatch_rating = 1 if sim_rating >= 4 else 5
                feedback = random.choice(TEMPLATES[mismatch_rating])
            else:
                feedback = random.choice(TEMPLATES[sim_rating])

        data = {
            "User_ID": user_id,
            "Event_Name": event,
            "Event_Type": classify_event(event),
            "Event_Date": profiles[event]["date"],
            "Registered": True,
            "Attended": bool(attended),
            "Rating": rating,
            "Feedback_Text": feedback,
        }

        # [NOISE] Add occasional duplicates (2% chance)
        records.append(data)
        if random.random() < 0.02:
            records.append(dict(data))
    return records


def push_simulated_data(num_records, event_names, profile="Realistic"):
    success, msg = init_firebase()
    if not success:
        st.error(f"Firebase Connection Failed: {msg}")
        return False
    db = firestore.client()

    with st.status(f"🛠️ Simulation Engine ({profile})...", expanded=True) as status:
        try:
            status.write("Purging Cloud Collection...")
            collection_ref = db.collection('Registrations_Feedback')
            docs = collection_ref.limit(500).get()
            while len(docs) > 0:
                batch = db.batch()
                for doc in docs: batch.delete(doc.reference)
                batch.commit()
                docs = collection_ref.limit(500).get()

            status.write(f"Generating {num_records} noisy records...")
            records = generate_records(num_records, event_names, profile)

            # Same records (duplicates included) go to both Firestore and the CSV fallback.
            batch = db.batch()
            for i, data in enumerate(records):
                batch.set(collection_ref.document(), {**data, "Timestamp": firestore.SERVER_TIMESTAMP})
                if (i + 1) % 400 == 0:
                    batch.commit()
                    batch = db.batch()
            batch.commit()

            # The SERVER_TIMESTAMP sentinel can't be serialised to CSV; record the real write time instead.
            now = datetime.now(timezone.utc).isoformat()
            pd.DataFrame([{**r, "Timestamp": now} for r in records]).to_csv("eventflow_simulated_data.csv",
                                                                           index=False)
            status.update(label="✅ Noise-Injected Pipeline Refreshed!", state="complete")
            return True
        except Exception as e:
            status.update(label="❌ Regeneration failed", state="error")
            st.error(f"Push Error: {e}")
            return False


# --- 4. DATA EXTRACTION, CLEANING & FEATURE ENGINEERING ---
FIRESTORE_READ_LIMIT = 5000  # newest N records are read; the dashboard warns if more exist


@st.cache_data(ttl=300)
def get_processed_data(source_preference):
    df, source_label, error_msg, truncated = pd.DataFrame(), "None", None, False

    if source_preference in ["Cloud (Auto-Fallback)", "Firebase Cloud Only"]:
        success, init_msg = init_firebase()
        if success:
            try:
                db = firestore.client()
                # Ask for one extra document so we can tell whether the limit cut anything off.
                docs = db.collection('Registrations_Feedback').order_by(
                    'Timestamp', direction=firestore.Query.DESCENDING).limit(FIRESTORE_READ_LIMIT + 1).get()
                if len(docs) > FIRESTORE_READ_LIMIT:
                    truncated, docs = True, docs[:FIRESTORE_READ_LIMIT]
                if docs:
                    df = pd.DataFrame([d.to_dict() for d in docs])
                    source_label = "Firebase Cloud"
            except Exception as e:
                error_msg = str(e)
        else:
            error_msg = init_msg

    if df.empty or source_preference == "Local CSV Only":
        if os.path.exists("eventflow_simulated_data.csv"):
            df = pd.read_csv("eventflow_simulated_data.csv")
            source_label = "Local CSV Fallback"

    if df.empty: return df, source_label, error_msg, truncated

    # --- STEP 4.0: SCHEMA NORMALISATION ---
    if 'Event_Date' in df.columns:  # added in v2; older data has no event dates
        df['Event_Date'] = pd.to_datetime(df['Event_Date'], errors='coerce')
    # Event_Type is derived from the name; recompute it so older data (missing the column,
    # or mislabelled by the old substring rule) is classified consistently.
    df['Event_Type'] = df['Event_Name'].apply(classify_event)
    if 'Timestamp' not in df.columns:
        df['Timestamp'] = pd.NaT
    # Old CSVs hold the literal "Sentinel: ..." string; coerce those to NaT.
    df['Timestamp'] = pd.to_datetime(df['Timestamp'], errors='coerce', utc=True)
    if df['Attended'].dtype == object:
        df['Attended'] = df['Attended'].astype(str).str.lower().eq('true')

    # --- STEP 4.1: DATA CLEANING ---
    # A. Duplicate Removal
    df = df.drop_duplicates(subset=['User_ID', 'Event_Name', 'Feedback_Text'])

    # B. Explicit Null Handling
    # Ratings are only expected if Attended is True
    df.loc[(df['Attended'] == False), 'Rating'] = np.nan
    # Fill missing Feedback_Text with a placeholder for analysis
    df['Feedback_Text'] = df['Feedback_Text'].fillna("No Comment Provided")

    # C. Text Preprocessing (Normalization for NLP)
    def clean_text(text):
        text = str(text).lower()
        text = re.sub(f"[{re.escape(string.punctuation)}]", "", text)  # Remove punctuation
        text = text.strip()
        return text

    df['Cleaned_Feedback'] = df['Feedback_Text'].apply(clean_text)

    # --- STEP 4.2: FEATURE ENGINEERING ---
    df['Rating'] = pd.to_numeric(df['Rating'], errors='coerce')
    analyzer = SentimentIntensityAnalyzer()

    def analyze_row(row):
        # No comment = nothing to score. NaN (not 0) keeps these rows out of sentiment averages.
        if row['Cleaned_Feedback'] == "no comment provided": return np.nan, "No Comment", np.nan
        # VADER is scored on the ORIGINAL text: it uses punctuation ("!!") and capitalisation
        # as intensity cues, so the normalised Cleaned_Feedback would weaken its scores.
        score = analyzer.polarity_scores(str(row['Feedback_Text']))['compound']
        label = "Positive" if score >= 0.05 else ("Negative" if score <= -0.05 else "Neutral")
        rating = row['Rating']
        # Consistency Logic (Rating vs Sentiment). Rows without a rating can't be checked,
        # so they are NaN (excluded from averages) rather than counted as mismatches.
        if pd.isna(rating):
            return score, label, np.nan
        if rating >= 4:
            aligned = score >= 0.05
        elif rating <= 2:
            aligned = score <= -0.05
        else:
            # A 3-star review is "mixed": anything short of strongly polar text is consistent.
            # The +/-0.05 neutral band alone is too narrow for real comments.
            aligned = abs(score) < MIXED_BAND
        return score, label, int(aligned)

    res = df.apply(analyze_row, axis=1)
    df['Senti_Score'] = [r[0] for r in res]
    df['Sentiment'] = [r[1] for r in res]
    df['NLP_Alignment'] = [r[2] for r in res]

    return df, source_label, error_msg, truncated


# --- 4b. EVENT-LEVEL STATISTICS ---
PRIOR_WEIGHT = 20  # pseudo-observations that pull small-sample averages toward the overall average
Z95 = 1.96

STOPWORDS = set("""a an the and or but of to in on at for with from by is was were be been are it its this that
these those i me my we our you your he she they them their very so too just really much more most no not nor
dont didnt wasnt isnt couldnt would could should will can had has have do did does all any some than then
there here what which who when where how also well bit quite""".split())


def wilson_interval(successes, n, z=Z95):
    """95% confidence interval for a proportion; behaves well for small n and rates near 0 or 1."""
    if n == 0:
        return np.nan, np.nan
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def build_event_stats(df):
    """Per-event metrics, with sample sizes, confidence intervals and small-sample-adjusted rankings."""
    keys = ['Event_Name', 'Event_Type']
    attendees = df[df['Attended'] == True]
    stats = df.groupby(keys).agg(Total_Reg=('Attended', 'count'), Total_Att=('Attended', 'sum')).join(
        attendees.groupby(keys).agg(N_Ratings=('Rating', 'count'), Avg_Rating=('Rating', 'mean'),
                                    Rating_Std=('Rating', 'std'), Rating_Skew=('Rating', 'skew'),
                                    Avg_Sentiment=('Senti_Score', 'mean'), NLP_Match=('NLP_Alignment', 'mean'))
    ).reset_index()
    if 'Event_Date' in df.columns:
        stats = stats.merge(df.groupby('Event_Name')['Event_Date'].min().reset_index(), on='Event_Name')

    stats['N_Ratings'] = stats['N_Ratings'].fillna(0).astype(int)
    stats['Total_Att'] = stats['Total_Att'].astype(int)
    dropped = stats['Total_Reg'] - stats['Total_Att']
    stats['Drop_off_Rate'] = dropped / stats['Total_Reg']

    # Confidence intervals: normal approximation for mean rating, Wilson for drop-off rate.
    stats['Rating_CI'] = Z95 * stats['Rating_Std'] / np.sqrt(stats['N_Ratings'].where(stats['N_Ratings'] > 0))
    bounds = [wilson_interval(k, n) for k, n in zip(dropped, stats['Total_Reg'])]
    stats['Drop_CI_Low'] = [b[0] for b in bounds]
    stats['Drop_CI_High'] = [b[1] for b in bounds]

    # Bayesian (shrunk) averages for ranking: an event with few responses is pulled toward the overall
    # average, so 3 five-star ratings can't outrank 300 ratings averaging 4.8.
    overall_rating = attendees['Rating'].mean()
    rating_sum = stats['Avg_Rating'].fillna(0) * stats['N_Ratings']
    stats['Adj_Rating'] = (rating_sum + PRIOR_WEIGHT * overall_rating) / (stats['N_Ratings'] + PRIOR_WEIGHT)
    overall_drop = dropped.sum() / stats['Total_Reg'].sum()
    stats['Adj_Drop_off'] = (dropped + PRIOR_WEIGHT * overall_drop) / (stats['Total_Reg'] + PRIOR_WEIGHT)
    return stats


def top_keywords(texts, n=8):
    words = Counter(w for t in texts for w in str(t).split() if len(w) > 2 and w not in STOPWORDS)
    return pd.DataFrame(words.most_common(n), columns=['Keyword', 'Mentions'])


# --- 5. SIDEBAR ---
st.sidebar.header("🛡️ System Management")
st.sidebar.caption(f"EventFlow v{APP_VERSION}")
if 'source_pref' not in st.session_state:
    st.session_state.source_pref = "Cloud (Auto-Fallback)"

st.session_state.source_pref = st.sidebar.radio("Active Pipeline Selection",
                                                ["Cloud (Auto-Fallback)", "Firebase Cloud Only", "Local CSV Only"])

df, data_source, last_error, was_truncated = get_processed_data(st.session_state.source_pref)

badge_class = "cloud-source" if "Cloud" in data_source else "local-source"
st.sidebar.markdown(f'<div class="source-badge {badge_class}">Hub: {data_source}</div>', unsafe_allow_html=True)

with st.sidebar.expander("🎯 Matrix Thresholds", expanded=False):
    threshold_mode = st.radio("Quadrant lines", ["Fixed targets", "Event medians"],
                              help="Event medians split the events into halves on each axis.")
    rating_target = st.slider("Satisfaction target (stars)", 1.0, 5.0, 3.5, 0.1,
                              disabled=threshold_mode != "Fixed targets")
    drop_target = st.slider("Drop-off threshold (%)", 0, 100, 30, 5,
                            disabled=threshold_mode != "Fixed targets") / 100

with st.sidebar.expander("⚙️ Simulation Config", expanded=False):
    num_to_gen = st.slider("Total Records", 100, 2000, 600)
    event_list_raw = st.text_area("Events List", "AI Workshop, Cloud Hackathon, Music Fest, Startup Pitch, Career Fair")
    sim_profile = st.selectbox("Sentiment Logic", ["Realistic", "Positive", "Critical"])
    st.warning("This permanently deletes **every document** in the Firestore `Registrations_Feedback` "
               "collection and overwrites the local CSV. It cannot be undone.")
    confirm_wipe = st.checkbox("I understand, delete the existing data")
    if st.button("🚀 Wipe & Regenerate Data", disabled=not confirm_wipe):
        events = [x.strip() for x in event_list_raw.split(",") if x.strip()]
        if not events:
            st.error("Enter at least one event name.")
        # Only rerun on success, otherwise the rerun would immediately hide the error message.
        elif push_simulated_data(num_to_gen, events, sim_profile):
            st.cache_data.clear()
            st.rerun()

# --- 6. EXECUTIVE INSIGHT PANEL ---
if not df.empty:
    st.title("📊 EventFlow: Event Feedback Analytics Dashboard")

    if was_truncated:
        st.warning(f"Showing only the newest {FIRESTORE_READ_LIMIT:,} records. Firestore holds more, so "
                   f"totals and averages may not cover everything.")

    event_stats = build_event_stats(df)
    has_dates = 'Event_Date' in event_stats.columns

    if threshold_mode == "Event medians":
        rating_target = float(event_stats['Avg_Rating'].median())
        drop_target = float(event_stats['Drop_off_Rate'].median())

    ranked = event_stats.dropna(subset=['Adj_Rating'])
    top_event = ranked.loc[ranked['Adj_Rating'].idxmax()] if not ranked.empty else None
    worst_event = event_stats.loc[event_stats['Adj_Drop_off'].idxmax()]

    events_per_person = df.groupby('User_ID')['Event_Name'].nunique()
    returning_pct = (events_per_person > 1).mean() * 100

    st.markdown("### 🔦 Executive Summary")
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        if top_event is not None:
            st.markdown(
                f'<div class="insight-card success-card"><b>🏆 Top Performer</b><br>{top_event["Event_Name"]}<br>'
                f'<small>Rating: {top_event["Avg_Rating"]:.2f}/5 from {top_event["N_Ratings"]} ratings</small></div>',
                unsafe_allow_html=True)
        else:
            st.markdown('<div class="insight-card info-card"><b>🏆 Top Performer</b><br>No ratings yet</div>',
                        unsafe_allow_html=True)
    with c2:
        st.markdown(
            f'<div class="insight-card info-card"><b>📝 Total Records</b><br>{len(df)} Entries<br><small>After duplicate removal</small></div>',
            unsafe_allow_html=True)
    with c3:
        st.markdown(
            f'<div class="insight-card info-card"><b>👥 Participation</b><br>{int(event_stats["Total_Att"].sum())} Attendees<br>'
            f'<small>Across {len(event_stats)} events · {returning_pct:.0f}% of people signed up for 2+</small></div>',
            unsafe_allow_html=True)
    with c4:
        st.markdown(
            f'<div class="insight-card error-card"><b>🚨 Critical Drop-off</b><br>{worst_event["Event_Name"]}<br>'
            f'<small>Drop rate: {worst_event["Drop_off_Rate"] * 100:.1f}% '
            f'(95% CI {worst_event["Drop_CI_Low"] * 100:.0f}–{worst_event["Drop_CI_High"] * 100:.0f}%)</small></div>',
            unsafe_allow_html=True)
    with c5:
        avg_align = df[df['Attended'] == True]['NLP_Alignment'].mean() * 100
        st.markdown(
            f'<div class="insight-card {"success-card" if avg_align > 80 else "warning-card"}"><b>🔍 Logic Integrity</b><br>Rating vs Mood<br><small>{avg_align:.1f}% Consistency</small></div>',
            unsafe_allow_html=True)
    st.caption(f"Top Performer and Critical Drop-off are ranked on averages adjusted for sample size "
               f"(each event is blended with {PRIOR_WEIGHT} responses' worth of the overall average), "
               f"so events with only a few responses can't top the rankings by chance.")

    # TABS
    tab1, tab2, tab_drill, tab3, tab4, tab5 = st.tabs([
        "🎯 Performance Matrix",
        "📉 Student Consistency",
        "🔎 Event Drill-down",
        "🧠 AI Mood Validation",
        "🏗️ Tech vs. General",
        "📑 Pipeline Journey"
    ])

    with tab1:
        st.subheader("Decision Support: Quadrant Logic")
        col_chart, col_text = st.columns([2, 1])

        with col_chart:
            plot_df = event_stats.assign(
                Drop_Err_Plus=event_stats['Drop_CI_High'] - event_stats['Drop_off_Rate'],
                Drop_Err_Minus=event_stats['Drop_off_Rate'] - event_stats['Drop_CI_Low'])
            fig_quad = px.scatter(plot_df, x="Drop_off_Rate", y="Avg_Rating", size="Total_Reg", color="Event_Name",
                                  text="Event_Name", title="Investment vs. Satisfaction Matrix",
                                  error_y="Rating_CI", error_x="Drop_Err_Plus", error_x_minus="Drop_Err_Minus",
                                  hover_data={"N_Ratings": True, "Total_Reg": True, "Drop_Err_Plus": False,
                                              "Drop_Err_Minus": False, "Rating_CI": ":.2f"},
                                  labels={"Drop_off_Rate": "Drop-off Rate (1 - Attendance)",
                                          "Avg_Rating": "Avg Rating", "N_Ratings": "Ratings",
                                          "Total_Reg": "Registrations", "Rating_CI": "Rating ± (95%)"},
                                  height=500, template="plotly_white")
            fig_quad.update_xaxes(tickformat=".0%")
            fig_quad.add_hline(y=rating_target, line_dash="dash", line_color="gray")
            fig_quad.add_vline(x=drop_target, line_dash="dash", line_color="gray")
            st.plotly_chart(fig_quad, width="stretch")

        with col_text:
            st.markdown(f"""
            **How to read this matrix:**
            We group events into four "Performance Zones" to help management make decisions:

            - **🌟 Stars (Top Left):** Low drop-off and high satisfaction. Keep doing exactly this!
            - **⚠️ Mass Outreach (Bottom Left):** Low drop-off but poor execution. The marketing worked, but the event failed to deliver.
            - **💎 Hidden Gems (Top Right):** High drop-off, but those who stayed loved it. Needs better targeted marketing to the right audience next time.
            - **🛑 The Risks (Bottom Right):** High drop-off and low satisfaction. Urgent re-evaluation needed.

            *Dashed lines ({threshold_mode.lower()}): {rating_target:.2f} stars and {drop_target:.0%} drop-off.
            Change them under **Matrix Thresholds** in the sidebar.*

            *Error bars are 95% confidence intervals. When an event's bars cross a dashed line, its zone is
            uncertain: more responses are needed to say which side it is on.*
            """)

    with tab2:
        st.subheader("Statistical Signals & Correlation")

        c_stats, c_corr = st.columns([3, 2])

        with c_stats:
            st.write("**Event Performance & Consistency**")

            # Rename columns ONLY for the visual display, keeping the backend intact
            table_cols = (['Event_Date'] if has_dates else []) + [
                'Event_Name', 'N_Ratings', 'Avg_Rating', 'Rating_CI', 'Adj_Rating', 'Rating_Std', 'Rating_Skew',
                'Drop_off_Rate']
            styled_df = event_stats[table_cols].sort_values('Adj_Rating', ascending=False).rename(
                columns={
                    'Event_Date': 'Date',
                    'N_Ratings': 'Ratings (n)',
                    'Avg_Rating': 'Average Rating',
                    'Rating_CI': '± 95% CI',
                    'Adj_Rating': 'Adjusted Rating',
                    'Rating_Std': 'Consistency (StdDev)',
                    'Rating_Skew': 'Opinion Bias',
                    'Drop_off_Rate': 'Drop-off Risk'
                })
            st.dataframe(styled_df.round(2), hide_index=True,
                         column_config={"Date": st.column_config.DateColumn(format="D MMM YYYY")})
            st.caption("**Adjusted Rating** blends each event's average with the overall average, weighted by how "
                       "many ratings it has. Events with few ratings move toward the middle; well-rated events "
                       "with many responses barely change. The table is sorted by it.")

            # Add explanatory boxes below the table
            st.info("""
            **Consistency (Standard Deviation):**
            - **Low (Good):** Most attendees agreed on their rating. The experience was stable.
            - **High (Warning):** Attendees had wildly different opinions. This suggests a "hit or miss" event.
            """)
            st.info("""
            **Opinion Bias (Skewness):**
            - **Negative:** Most were happy, but a few very unhappy attendees pulled the average down.
            - **Positive:** Most were unhappy, but a few "super fans" inflated the score.
            """)

        with c_corr:
            st.write("**Correlation: Rating vs Sentiment (per response)**")
            # Computed over individual rated comments. Correlating the per-event averages would
            # use only one point per event, far too few for a meaningful coefficient.
            scored = df[(df['Attended'] == True) & df['Rating'].notna() & df['Senti_Score'].notna()]
            if len(scored) >= 10:
                # Spearman = Pearson on ranks; suits a 1-5 ordinal rating (no SciPy needed).
                spearman = scored['Rating'].rank().corr(scored['Senti_Score'].rank())
                pearson = scored['Rating'].corr(scored['Senti_Score'])
                m1, m2 = st.columns(2)
                m1.metric("Spearman ρ", f"{spearman:.3f}",
                          help="Rank correlation between star rating and comment sentiment. "
                               "+1 = sentiment always rises with the rating; 0 = no relationship.")
                m2.metric("Pearson r", f"{pearson:.3f}", help="Linear correlation, shown for reference.")
                st.caption(f"Based on {len(scored):,} individual rated comments.")
                fig_corr = px.box(scored.assign(Rating=scored['Rating'].astype(int)), x="Rating", y="Senti_Score",
                                  title="Comment Sentiment by Star Rating",
                                  labels={"Rating": "Star rating", "Senti_Score": "VADER compound score"},
                                  category_orders={"Rating": [1, 2, 3, 4, 5]})
                st.plotly_chart(fig_corr, width="stretch")
            else:
                st.info("Not enough rated comments to compute a correlation (need at least 10).")

    with tab_drill:
        st.subheader("🔎 Event Drill-down")
        event_choice = st.selectbox("Choose an event", event_stats.sort_values('Event_Name')['Event_Name'])
        ev = event_stats.set_index('Event_Name').loc[event_choice]
        edf = df[df['Event_Name'] == event_choice]
        e_att = edf[edf['Attended'] == True]

        if has_dates and pd.notna(ev['Event_Date']):
            st.caption(f"{ev['Event_Type']} event · held {ev['Event_Date']:%d %b %Y}")
        else:
            st.caption(f"{ev['Event_Type']} event")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Registrations", f"{int(ev['Total_Reg'])}")
        m2.metric("Attendance", f"{1 - ev['Drop_off_Rate']:.0%}",
                  help=f"95% CI for drop-off: {ev['Drop_CI_Low']:.0%}–{ev['Drop_CI_High']:.0%}")
        if pd.notna(ev['Avg_Rating']):
            # CI goes in the label: the value box truncates long text in narrow columns.
            ci_txt = f" (± {ev['Rating_CI']:.2f})" if pd.notna(ev['Rating_CI']) else ""
            m3.metric(f"Avg rating{ci_txt}", f"{ev['Avg_Rating']:.2f}",
                      help=f"From {ev['N_Ratings']} ratings; ± is the 95% confidence interval.")
        else:
            m3.metric("Avg rating", "–")
        m4.metric("Rating vs mood match", f"{ev['NLP_Match']:.0%}" if pd.notna(ev['NLP_Match']) else "–")

        d_left, d_right = st.columns(2)
        with d_left:
            dist = e_att['Rating'].dropna().astype(int).value_counts().reindex(range(1, 6), fill_value=0)
            st.plotly_chart(px.bar(x=dist.index, y=dist.values, title="Rating Distribution",
                                   labels={"x": "Stars", "y": "Responses"}), width="stretch")
        with d_right:
            st.write("**What people mention**")
            k_pos, k_neg = st.columns(2)
            with k_pos:
                st.caption("In positive comments")
                st.dataframe(top_keywords(e_att.loc[e_att['Sentiment'] == "Positive", 'Cleaned_Feedback']),
                             hide_index=True, width="stretch")
            with k_neg:
                st.caption("In negative comments")
                st.dataframe(top_keywords(e_att.loc[e_att['Sentiment'] == "Negative", 'Cleaned_Feedback']),
                             hide_index=True, width="stretch")

        mismatches = e_att[e_att['NLP_Alignment'] == 0][
            ['User_ID', 'Rating', 'Feedback_Text', 'Senti_Score', 'Sentiment']].sort_values('Rating', ascending=False)
        st.write(f"**Responses to read by hand: {len(mismatches)}** (star rating and comment disagree)")
        if mismatches.empty:
            st.success("Every rated comment for this event agrees with its star rating.")
        else:
            st.dataframe(mismatches.rename(columns={'Feedback_Text': 'Comment', 'Senti_Score': 'Sentiment score'}),
                         hide_index=True, width="stretch")
            st.download_button("⬇️ Download these responses (CSV)", mismatches.to_csv(index=False),
                               file_name=f"{event_choice.lower().replace(' ', '_')}_mismatches.csv",
                               mime="text/csv")

    with tab3:
        st.subheader("AI Mood Validation (Consistency Check)")

        st.write("""
        **How we validate the data:**
        We use a Natural Language Processing (NLP) AI model called VADER to read the attendees' written comments. We then compare the "emotional mood" of their writing with the numerical "star rating" they clicked.

        A response counts as consistent when a 4–5 star rating comes with positive text, a 1–2 star rating with
        negative text, or a 3 star rating with text that isn't strongly positive or negative. Responses with no
        rating or no comment are left out.

        A high match rate suggests ratings and comments tell the same story; a low one flags responses worth reading
        by hand. It is a sanity check, not proof: VADER misreads sarcasm and domain-specific wording.
        The **Event Drill-down** tab lists the mismatched responses for each event.
        """)
        st.caption("On data from the built-in simulator, about 10% of comments are deliberately written to "
                   "contradict their rating, so the match rate there mostly reflects that injected noise.")

        col_pie, col_bar = st.columns(2)
        with col_pie:
            sent_dist = df[df['Attended'] == True]['Sentiment'].value_counts()
            st.plotly_chart(
                px.pie(names=sent_dist.index, values=sent_dist.values, hole=0.5, title="Polarity Distribution"),
                width="stretch")
        with col_bar:
            fig_align = px.bar(event_stats, x='Event_Name', y='NLP_Match', color='NLP_Match',
                               title="Logic Match Rate (%)", color_continuous_scale='RdYlGn')
            st.plotly_chart(fig_align, width="stretch")

    with tab4:
        st.subheader("🏗️ Segment Analysis: Technical vs. General")
        st.markdown(
            "This compares how we execute highly technical workshops versus general networking events, helping accurately allocate future budgets and resources.")

        # Aggregate from individual rows so each response counts equally
        # (averaging the per-event averages would over-weight small events).
        attendees = df[df['Attended'] == True]
        seg_stats = df.groupby('Event_Type').agg(Total_Reg=('Attended', 'count'),
                                                 Total_Att=('Attended', 'sum')).join(
            attendees.groupby('Event_Type').agg(Avg_Rating=('Rating', 'mean'),
                                                Avg_Sentiment=('Senti_Score', 'mean'))).reset_index()
        seg_stats['Agg_Engagement'] = (seg_stats['Total_Att'] / seg_stats['Total_Reg']) * 100

        c_seg1, c_seg2 = st.columns(2)
        with c_seg1:
            st.write("**Engagement per Category**")
            st.plotly_chart(
                px.bar(seg_stats, x='Event_Type', y='Agg_Engagement', color='Event_Type', title="Engagement % by Type"),
                width="stretch")
        with c_seg2:
            st.write("**Sentiment per Category**")
            st.plotly_chart(
                px.bar(seg_stats, x='Event_Type', y='Avg_Sentiment', color='Event_Type', title="Mood by Type"),
                width="stretch")

    with tab5:
        st.subheader("📊 Data Science Pipeline & Methodology")

        st.markdown(f"""
        ### The Technical Journey of Your Data
        Every time the dashboard loads, the data goes through these steps:

        **1. Ingestion (and optional simulation)**
        Records are read from the **Firebase Firestore** collection `Registrations_Feedback` (newest
        {FIRESTORE_READ_LIMIT:,}, with a warning if more exist), falling back to a local CSV if Firestore is
        unavailable. The sidebar simulator can replace that data with synthetic records. Each simulated event gets
        its own fixed attendance rate, typical rating and date, and people can register for several events. The
        simulator deliberately adds noise: missing ratings, comments that contradict their rating, and duplicate rows.

        **2. Cleaning**
        Exact duplicates (same user, event and comment) are removed. Ratings from people who did not attend are
        discarded, missing comments are marked "No Comment Provided", and event types are re-derived from event names.

        **3. Sentiment scoring**
        **VADER**, a rule-based sentiment model, gives each comment a compound score from −1 (very negative) to +1
        (very positive). It reads the original text, since punctuation and capitals affect its scores. Responses
        without a comment are not scored.

        **4. Feature engineering**
        Per event: registrations, attendance, **drop-off rate** (share of registrants who didn't attend), average
        rating, and average sentiment. Per response: an **alignment flag** recording whether the comment's sentiment
        agrees with the star rating.

        **5. Statistics**
        Every event average comes with its sample size and a **95% confidence interval** (Wilson interval for
        drop-off). Rankings use **adjusted averages** that blend each event with {PRIOR_WEIGHT} responses' worth of
        the overall average, so small events can't win or lose by chance. **Standard deviation** shows how much
        attendees disagreed; **skewness** shows whether a minority of unusually high or low ratings is pulling the
        average. A **rank correlation** over individual responses measures how closely comment sentiment tracks
        star ratings.

        **6. Drill-down**
        For each event: the rating distribution, the most-mentioned words in positive and negative comments, and
        the responses whose rating and comment disagree, downloadable as CSV.

        *Limitations:* the data is simulated, so the results show how the pipeline behaves rather than real attendee
        opinion. Comparisons between event types rest on a handful of events, and VADER can misread sarcasm.
        """)

        st.info("**Tech Stack:** Python 3, Streamlit, Firebase Admin SDK, VADER, pandas, Plotly.")

        if last_error: st.error(f"Last Pipeline Alert: {last_error}")

    with st.expander("🔍 View Processed Data (Deduplicated & Preprocessed)"):
        st.dataframe(df.sort_values(by='Timestamp', ascending=False) if 'Timestamp' in df.columns else df,
                     width="stretch")

else:
    st.warning("⚠️ No data available. Use 'Regenerate Data' in sidebar to start.")
