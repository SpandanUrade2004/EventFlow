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
from datetime import datetime, timezone

# --- 1. PAGE CONFIGURATION ---
st.set_page_config(page_title="EventFlow: Event Feedback Analytics Dashboard", layout="wide")

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


def generate_records(num_records, event_names, profile="Realistic"):
    """Builds simulated records (no I/O). Duplicates are injected as repeated dicts."""
    records = []
    for _ in range(num_records):
        event = random.choice(event_names)
        att_p = random.uniform(0.4, 0.95) if profile == "Realistic" else (
            0.90 if profile == "Positive" else 0.45)
        base_r = random.uniform(1.5, 4.8) if profile == "Realistic" else (4.5 if profile == "Positive" else 2.0)

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
            "User_ID": str(uuid.uuid4())[:8],
            "Event_Name": event,
            "Event_Type": classify_event(event),
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
        return
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
            status.update(label=f"✅ Noise-Injected Pipeline Refreshed!", state="complete")
        except Exception as e:
            st.error(f"Push Error: {e}")


# --- 4. DATA EXTRACTION, CLEANING & FEATURE ENGINEERING ---
@st.cache_data(ttl=300)
def get_processed_data(source_preference):
    df, source_label, error_msg = pd.DataFrame(), "None", None

    if source_preference in ["Cloud (Auto-Fallback)", "Firebase Cloud Only"]:
        success, init_msg = init_firebase()
        if success:
            try:
                db = firestore.client()
                docs = db.collection('Registrations_Feedback').order_by('Timestamp',
                                                                        direction=firestore.Query.DESCENDING).limit(
                    2000).get()
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

    if df.empty: return df, source_label, error_msg

    # --- STEP 4.0: SCHEMA NORMALISATION ---
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
        text = row['Cleaned_Feedback']
        if text == "no comment provided": return 0, "Neutral", np.nan
        score = analyzer.polarity_scores(text)['compound']
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

    return df, source_label, error_msg


# --- 5. SIDEBAR ---
st.sidebar.header("🛡️ System Management")
if 'source_pref' not in st.session_state:
    st.session_state.source_pref = "Cloud (Auto-Fallback)"

st.session_state.source_pref = st.sidebar.radio("Active Pipeline Selection",
                                                ["Cloud (Auto-Fallback)", "Firebase Cloud Only", "Local CSV Only"])

df, data_source, last_error = get_processed_data(st.session_state.source_pref)

badge_class = "cloud-source" if "Cloud" in data_source else "local-source"
st.sidebar.markdown(f'<div class="source-badge {badge_class}">Hub: {data_source}</div>', unsafe_allow_html=True)

with st.sidebar.expander("⚙️ Simulation Config", expanded=False):
    num_to_gen = st.slider("Total Records", 100, 2000, 600)
    event_list_raw = st.text_area("Events List", "AI Workshop, Cloud Hackathon, Music Fest, Startup Pitch, Career Fair")
    sim_profile = st.selectbox("Sentiment Logic", ["Realistic", "Positive", "Critical"])
    if st.button("🚀 Wipe & Regenerate Data"):
        push_simulated_data(num_to_gen, [x.strip() for x in event_list_raw.split(",")], sim_profile)
        st.cache_data.clear()
        st.rerun()

# --- 6. EXECUTIVE INSIGHT PANEL ---
if not df.empty:
    st.title("📊 EventFlow: Event Feedback Analytics Dashboard")

    # Feature Engineering (Aggregate Level)
    event_stats = df.groupby(['Event_Name', 'Event_Type']).agg({
        'Attended': ['count', 'sum'],
        'Rating': ['mean', 'std', 'skew'],
        'Senti_Score': 'mean',
        'NLP_Alignment': 'mean'
    })
    event_stats.columns = ['Total_Reg', 'Total_Att', 'Avg_Rating', 'Rating_Std', 'Rating_Skew', 'Avg_Sentiment',
                           'NLP_Match']
    event_stats = event_stats.reset_index()

    # Explicit Engineered Metric: Drop-off Rate
    event_stats['Drop_off_Rate'] = 1 - (event_stats['Total_Att'] / event_stats['Total_Reg'])

    top_event = event_stats.loc[event_stats['Avg_Rating'].idxmax()]
    worst_event = event_stats.loc[event_stats['Drop_off_Rate'].idxmax()]

    st.markdown("### 🔦 Executive Summary")
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.markdown(
            f'<div class="insight-card success-card"><b>🏆 Top Performer</b><br>{top_event["Event_Name"]}<br><small>Rating: {top_event["Avg_Rating"]:.2f}/5</small></div>',
            unsafe_allow_html=True)
    with c2:
        st.markdown(
            f'<div class="insight-card info-card"><b>📝 Total Records</b><br>{len(df)} Entries<br><small>After duplicate removal</small></div>',
            unsafe_allow_html=True)
    with c3:
        st.markdown(
            f'<div class="insight-card info-card"><b>👥 Participation</b><br>{int(event_stats["Total_Att"].sum())} Attendees<br><small>Across {len(event_stats)} events</small></div>',
            unsafe_allow_html=True)
    with c4:
        st.markdown(
            f'<div class="insight-card error-card"><b>🚨 Critical Drop-off</b><br>{worst_event["Event_Name"]}<br><small>Drop rate: {worst_event["Drop_off_Rate"] * 100:.1f}%</small></div>',
            unsafe_allow_html=True)
    with c5:
        avg_align = df[df['Attended'] == True]['NLP_Alignment'].mean() * 100
        st.markdown(
            f'<div class="insight-card {"success-card" if avg_align > 80 else "warning-card"}"><b>🔍 Logic Integrity</b><br>Rating vs Mood<br><small>{avg_align:.1f}% Consistency</small></div>',
            unsafe_allow_html=True)

    # TABS
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🎯 Performance Matrix",
        "📉 Student Consistency",
        "🧠 AI Mood Validation",
        "🏗️ Tech vs. General",
        "📑 Pipeline Journey"
    ])

    with tab1:
        st.subheader("Decision Support: Quadrant Logic")
        col_chart, col_text = st.columns([2, 1])

        with col_chart:
            fig_quad = px.scatter(event_stats, x="Drop_off_Rate", y="Avg_Rating", size="Total_Reg", color="Event_Name",
                                  text="Event_Name", title="Investment vs. Satisfaction Matrix",
                                  labels={"Drop_off_Rate": "Drop-off Rate (1 - Attendance)",
                                          "Avg_Rating": "Avg Rating"},
                                  height=500, template="plotly_white")
            fig_quad.add_hline(y=3.5, line_dash="dash", line_color="gray")
            fig_quad.add_vline(x=0.3, line_dash="dash", line_color="gray")
            st.plotly_chart(fig_quad, width="stretch")

        with col_text:
            st.markdown("""
            **How to read this matrix:**
            We group events into four "Performance Zones" to help management make decisions:

            - **🌟 Stars (Bottom Left):** Low drop-off and high satisfaction. Keep doing exactly this!
            - **⚠️ Mass Outreach (Top Left):** Low drop-off but poor execution. The marketing worked, but the event failed to deliver.
            - **💎 Hidden Gems (Bottom Right):** High drop-off, but those who stayed loved it. Needs better targeted marketing to the right audience next time.
            - **🛑 The Risks (Top Right):** High drop-off and low satisfaction. Urgent re-evaluation needed.
            """)

    with tab2:
        st.subheader("Statistical Signals & Correlation")

        c_stats, c_corr = st.columns([3, 2])

        with c_stats:
            st.write("**Event Performance & Consistency**")

            # Rename columns ONLY for the visual display, keeping the backend intact
            styled_df = event_stats[['Event_Name', 'Avg_Rating', 'Rating_Std', 'Rating_Skew', 'Drop_off_Rate']].rename(
                columns={
                    'Avg_Rating': 'Average Rating',
                    'Rating_Std': 'Consistency (StdDev)',
                    'Rating_Skew': 'Opinion Bias',
                    'Drop_off_Rate': 'Drop-off Risk'
                })
            st.dataframe(styled_df.round(2), hide_index=True)

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
            st.write("**Correlation: Rating vs Sentiment**")
            # Analytical proof of NLP validity
            corr_val = event_stats[['Avg_Rating', 'Avg_Sentiment']].corr().iloc[0, 1]
            st.metric("Correlation Coefficient", f"{corr_val:.3f}",
                      help="Closer to 1 means the AI mood perfectly matches the star ratings.")

            # Scatter with trendline
            fig_corr = px.scatter(event_stats, x="Avg_Sentiment", y="Avg_Rating", trendline="ols",
                                  title="Validation: Mood vs. Rating")
            st.plotly_chart(fig_corr, width="stretch")

    with tab3:
        st.subheader("AI Mood Validation (Consistency Check)")

        st.write("""
        **How we validate the data:**
        We use a Natural Language Processing (NLP) AI model called VADER to read the attendees' written comments. We then compare the "emotional mood" of their writing with the numerical "star rating" they clicked. 

        If the match rate is high, it proves that our feedback system is capturing genuine sentiment rather than random clicks or rushed form submissions.
        """)

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

        seg_stats = event_stats.groupby('Event_Type').agg({
            'Total_Reg': 'sum',
            'Total_Att': 'sum',
            'Avg_Rating': 'mean',
            'Avg_Sentiment': 'mean'
        }).reset_index()
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

        st.markdown("""
        ### The Technical Journey of Your Data
        To transform raw student feedback into these insights, this dashboard utilizes a 5-step automated pipeline:

        **1. Cloud Ingestion & Simulation**
        We connect directly to **Firebase Firestore**. The pipeline includes an advanced simulation engine that actively injects "noise" (missing values, mismatched sentiments, and duplicate rows) to test the robustness of our data cleaning logic.

        **2. Intelligent Data Cleaning (Pre-Flight)**
        Raw data is automatically deduplicated. Text features are normalized (punctuation removal, lowercase casting), and explicit null-handling ensures ratings are only calculated against verified attendees.

        **3. Feature Engineering**
        We calculate advanced metrics like **'Drop-off Rate'** (measuring marketing reach vs. actual commitment) and build an **'NLP Alignment'** flag to validate data veracity.

        **4. Statistical Validation**
        We use advanced math to detect outliers. **Standard Deviation** measures experience stability, **Skewness** checks for highly biased minorities, and an **OLS Correlation Trendline** proves the mathematical relationship between structured ratings and unstructured text.

        **5. NLP Sentiment Engine**
        The VADER NLP model scans unstructured feedback, identifying emotional polarity and assigning a compound score to mathematically validate the user's emotional state against their given rating.
        """)

        st.info("**Tech Stack:** Python 3.x, Streamlit, Firebase SDK, VADER (NLP), Statsmodels (Trendlines), Plotly.")

        if last_error: st.error(f"Last Pipeline Alert: {last_error}")

    with st.expander("🔍 View Processed Data (Deduplicated & Preprocessed)"):
        st.dataframe(df.sort_values(by='Timestamp', ascending=False) if 'Timestamp' in df.columns else df,
                     width="stretch")

else:
    st.warning("⚠️ No data available. Use 'Regenerate Data' in sidebar to start.")