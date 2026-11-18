"""EventFlow dashboard (Streamlit UI). Analysis logic lives in the `eventflow` package."""

import os

import pandas as pd
import plotly.express as px
import streamlit as st

from eventflow import __version__ as APP_VERSION
from eventflow import ingest, storage
from eventflow.analytics import (
    PRIOR_WEIGHT,
    build_event_stats,
    rating_sentiment_correlation,
    returning_share,
    segment_stats,
    top_keywords,
)
from eventflow.processing import SIMULATED, process
from eventflow.simulation import DEFAULT_EVENTS, PROFILES, generate_records
from eventflow.storage import FIRESTORE_READ_LIMIT

# --- 1. PAGE CONFIGURATION ---
st.set_page_config(page_title="EventFlow: Event Feedback Analytics Dashboard", layout="wide")

# Custom CSS for a professional "Analyst" look
st.markdown("""
    <style>
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border: 1px solid #eee; }
    .insight-card { padding: 20px; border-radius: 12px; margin-bottom: 20px; border-left: 5px solid; min-height: 120px; }
    .success-card { background-color: #d1e7dd; border-left-color: #0f5132; color: #0f5132; }
    .warning-card { background-color: #fff3cd; border-left-color: #856404; color: #856404; }
    .error-card { background-color: #f8d7da; border-left-color: #842029; color: #842029; }
    .info-card { background-color: #e3f2fd; border-left-color: #1976d2; color: #1976d2; }
    .source-badge { padding: 8px 12px; border-radius: 8px; font-size: 0.85rem; font-weight: bold; display: block; margin-bottom: 20px; text-align: center; }
    .cloud-source { background-color: #d1e7dd; color: #0f5132; border: 1px solid #badbcc; }
    .local-source { background-color: #fff3cd; color: #664d03; border: 1px solid #ffecb5; }
    /* Let column rows wrap instead of squeezing (e.g. the five summary cards on a narrow window) */
    div[data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] { min-width: min(170px, 100%); }
    </style>
    """, unsafe_allow_html=True)

st.title("📊 EventFlow: Event Feedback Analytics Dashboard")


# --- 2. ADMIN MODE ---
# The simulator wipes Firestore, so it is only shown when explicitly enabled. A deployed copy stays read-only.
def _admin_mode():
    if os.environ.get("EVENTFLOW_ADMIN", "").strip().lower() in ("1", "true", "yes"):
        return True
    try:
        return bool(st.secrets.get("admin", False))
    except Exception:
        return False  # no secrets file


ADMIN_MODE = _admin_mode()
COLLECTION = storage.collection_name(st.secrets)
UPLOAD = "Upload your own file"


# --- 3. DATA LOADING ---
@st.cache_data(ttl=300)
def get_processed_data(source_preference):
    df, source_label, error_msg, truncated = pd.DataFrame(), "None", None, False

    if source_preference in ("Cloud (Auto-Fallback)", "Firebase Cloud Only"):
        ok, msg = storage.init_firebase(st.secrets)
        if ok:
            try:
                df, truncated = storage.fetch_firestore(COLLECTION)
                if not df.empty:
                    source_label = "Firebase Cloud"
            except Exception as e:
                error_msg = str(e)
        else:
            error_msg = msg

    if source_preference == "Local CSV Only" or (df.empty and source_preference == "Cloud (Auto-Fallback)"):
        df, truncated = storage.load_csv(), False
        if not df.empty:
            source_label = "Local CSV Fallback"

    if df.empty:
        return df, source_label, error_msg, truncated
    return process(df), source_label, error_msg, truncated


def push_simulated_data(num_records, event_names, profile="Realistic"):
    ok, msg = storage.init_firebase(st.secrets)
    if not ok:
        st.error(f"Firebase Connection Failed: {msg}")
        return False
    with st.status(f"🛠️ Simulation Engine ({profile})...", expanded=True) as status:
        try:
            records = generate_records(num_records, event_names, profile)
            # Same records (duplicates included) go to both Firestore and the CSV fallback.
            storage.replace_firestore(records, COLLECTION, progress=status.write)
            storage.save_csv(records)
            status.update(label="✅ Noise-Injected Pipeline Refreshed!", state="complete")
            return True
        except Exception as e:
            status.update(label="❌ Regeneration failed", state="error")
            st.error(f"Push Error: {e}")
            return False


@st.cache_data(show_spinner="Reading your file...")
def read_upload(data, filename):
    return ingest.read_table(data, filename)


@st.cache_data(show_spinner="Checking your data and scoring comments...")
def prepare_upload(data, filename, mapping_items, rating_scale):
    clean, report = ingest.prepare_upload(read_upload(data, filename), dict(mapping_items), rating_scale)
    return (process(clean) if report.ok else clean), report


def upload_flow(uploaded):
    """Column matching and validation for an uploaded file. Returns the analysis-ready frame (or empty)."""
    data = uploaded.getvalue()
    try:
        raw = read_upload(data, uploaded.name)
    except Exception as e:
        st.error(f"Couldn't read **{uploaded.name}**: {e}")
        return pd.DataFrame()
    if raw.empty:
        st.error(f"**{uploaded.name}** has no data rows.")
        return pd.DataFrame()

    guess = ingest.guess_mapping(raw.columns)
    options = [ingest.NOT_IN_FILE] + list(raw.columns)
    file_key = f"{uploaded.name}-{len(data)}"  # new file -> fresh widget defaults

    with st.expander(f"📥 Your data: **{uploaded.name}** ({len(raw):,} rows). Check how the columns were matched",
                     expanded=True):
        st.caption("Your columns were matched automatically. Correct any that are wrong. * = required. "
                   "Columns you don't map are ignored.")
        mapping, grid = {}, st.columns(2)
        for i, f in enumerate(ingest.FIELDS):
            default = guess.get(f.name, ingest.NOT_IN_FILE)
            choice = grid[i % 2].selectbox(f.label + (" *" if f.required else ""), options,
                                           index=options.index(default), help=f.help,
                                           key=f"map-{file_key}-{f.name}")
            if choice != ingest.NOT_IN_FILE:
                mapping[f.name] = choice
        scale_default = ingest.guess_rating_scale(raw, mapping.get("Rating"))
        rating_scale = st.radio("Ratings are out of", [5, 10], index=[5, 10].index(scale_default), horizontal=True,
                                key=f"scale-{file_key}", disabled="Rating" not in mapping)
        st.write("**Preview** (first 5 rows of your file)")
        st.dataframe(raw.head(5), hide_index=True, width="stretch")

    used = pd.Series(list(mapping.values()))
    if used.duplicated().any():
        st.error(f"Each column can only be matched once: {', '.join(used[used.duplicated()].unique())}.")
        return pd.DataFrame()

    df, report = prepare_upload(data, uploaded.name, tuple(sorted(mapping.items())), rating_scale)
    for msg in report.errors:
        st.error(msg)
    if report.ok:
        st.success(f"Analysing {report.rows_out:,} of {report.rows_in:,} rows from **{uploaded.name}**. "
                   "The file stays in this browser session and is not saved anywhere.")
    for msg in report.warnings:
        st.warning(msg)
    for msg in report.notes:
        st.info(msg)
    return df if report.ok else pd.DataFrame()


# --- 4. SIDEBAR ---
st.sidebar.header("🛡️ System Management")
st.sidebar.caption(f"EventFlow v{APP_VERSION}")
source_pref = st.sidebar.radio("Data source", ["Cloud (Auto-Fallback)", "Firebase Cloud Only", "Local CSV Only",
                                               UPLOAD], key="source_pref")

uploaded = None
if source_pref == UPLOAD:
    uploaded = st.sidebar.file_uploader("CSV or Excel file", type=["csv", "xlsx", "xls"],
                                        help="E.g. a Google Forms or Microsoft Forms export, or a registration "
                                             "spreadsheet. Processed in this session only.")
    st.sidebar.download_button("⬇️ Download template CSV", ingest.template_csv(), "eventflow_template.csv",
                               "text/csv", help="An example file showing the columns EventFlow understands.")
    df, data_source, last_error, was_truncated = pd.DataFrame(), "Upload", None, False
    if uploaded is not None:
        df = upload_flow(uploaded)
        data_source = f"Upload: {uploaded.name}"
else:
    df, data_source, last_error, was_truncated = get_processed_data(source_pref)

is_simulated = not df.empty and (df['Source'] == SIMULATED).any()
badge_class = "cloud-source" if "Cloud" in data_source else "local-source"
st.sidebar.markdown(f'<div class="source-badge {badge_class}">Hub: {data_source}</div>', unsafe_allow_html=True)

with st.sidebar.expander("🎯 Matrix Thresholds", expanded=False):
    threshold_mode = st.radio("Quadrant lines", ["Fixed targets", "Event medians"],
                              help="Event medians split the events into halves on each axis.")
    rating_target = st.slider("Satisfaction target (stars)", 1.0, 5.0, 3.5, 0.1,
                              disabled=threshold_mode != "Fixed targets")
    drop_target = st.slider("Drop-off threshold (%)", 0, 100, 30, 5,
                            disabled=threshold_mode != "Fixed targets") / 100

if ADMIN_MODE:
    with st.sidebar.expander("⚙️ Simulation Config (admin)", expanded=False):
        num_to_gen = st.slider("Total Records", 100, 2000, 600)
        event_list_raw = st.text_area("Events List", ", ".join(DEFAULT_EVENTS))
        sim_profile = st.selectbox("Sentiment Logic", PROFILES)
        sim_target = st.radio("Write to", ["Local CSV only", "Firestore + local CSV"],
                              help="Local CSV only never touches Firestore (same as running data_generator.py).")
        if sim_target == "Firestore + local CSV":
            st.warning(f"This permanently deletes **every document** in the Firestore `{COLLECTION}` "
                       "collection and overwrites the local CSV. It cannot be undone.")
        else:
            st.info("Overwrites the local sample CSV. Firestore is not touched. "
                    "Switch the data source to **Local CSV Only** to view the result.")
        confirm_wipe = st.checkbox("I understand, replace the existing data")
        if st.button("🚀 Wipe & Regenerate Data", disabled=not confirm_wipe):
            events = [x.strip() for x in event_list_raw.split(",") if x.strip()]
            if not events:
                st.error("Enter at least one event name.")
            elif sim_target == "Local CSV only":
                storage.save_csv(generate_records(num_to_gen, events, sim_profile))
                st.cache_data.clear()
                st.rerun()
            # Only rerun on success, otherwise the rerun would immediately hide the error message.
            elif push_simulated_data(num_to_gen, events, sim_profile):
                st.cache_data.clear()
                st.rerun()
    if uploaded is not None and not df.empty:
        with st.sidebar.expander("☁️ Save upload to Firestore (admin)", expanded=False):
            st.warning(f"Replaces **every document** in the Firestore `{COLLECTION}` collection with the "
                       f"{len(df):,} uploaded rows. It cannot be undone.")
            confirm_save = st.checkbox("I understand, replace the Firestore data", key="confirm_save")
            if st.button("Save to Firestore", disabled=not confirm_save):
                ok, msg = storage.init_firebase(st.secrets)
                if not ok:
                    st.error(f"Firebase Connection Failed: {msg}")
                else:
                    with st.spinner("Writing to Firestore..."):
                        storage.replace_firestore(storage.records_from_frame(df), COLLECTION, progress=lambda m: None)
                    st.cache_data.clear()
                    st.success("Saved. Switch the data source to Cloud to see it.")
else:
    st.sidebar.caption("🔒 Read-only mode. The data simulator is hidden; set `EVENTFLOW_ADMIN=1` to enable it.")

# --- 5. EXECUTIVE INSIGHT PANEL ---
if not df.empty:
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

    returning_pct = returning_share(df) * 100

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
        avg_align = df[df['Attended']]['NLP_Alignment'].mean() * 100
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
        "🏗️ Event Categories",
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
            spearman, pearson, n_scored = rating_sentiment_correlation(df)
            if n_scored >= 10:
                m1, m2 = st.columns(2)
                m1.metric("Spearman ρ", f"{spearman:.3f}",
                          help="Rank correlation between star rating and comment sentiment. "
                               "+1 = sentiment always rises with the rating; 0 = no relationship.")
                m2.metric("Pearson r", f"{pearson:.3f}", help="Linear correlation, shown for reference.")
                st.caption(f"Based on {n_scored:,} individual rated comments.")
                scored = df[df['Attended'] & df['Rating'].notna() & df['Senti_Score'].notna()]
                fig_corr = px.box(scored.assign(Rating=scored['Rating'].astype(int)), x="Rating", y="Senti_Score",
                                  title="Comment Sentiment by Star Rating",
                                  labels={"Rating": "Star rating", "Senti_Score": "VADER compound score"},
                                  category_orders={"Rating": [1, 2, 3, 4, 5]})
                st.plotly_chart(fig_corr, width="stretch")
            else:
                st.info("Not enough rated comments to compute a correlation (need at least 10).")

    with tab_drill:
        st.subheader("🔎 Event Drill-down")
        st.write("""
        **What does this tab show?**
        The other tabs compare events with each other; this one looks inside a single event.
        Pick an event to see:

        - **Headline numbers:** registrations, attendance, average rating (± its 95% confidence interval) and how
          often ratings and comments agreed.
        - **Rating Distribution:** how many people gave each star rating. Two peaks (many 1s *and* many 5s) mean a
          "love it or hate it" event, even if the average looks ordinary.
        - **What people mention:** the most common words in positive and in negative comments, a quick way to see
          what attendees praised and what they complained about.
        - **Responses to read by hand:** comments that disagree with their star rating (e.g. 5 stars with a negative
          comment). These are often mis-clicks or mixed feelings, so they're worth reading before acting on the
          numbers. Download them as CSV to share with the event team.
        """)
        event_choice = st.selectbox("Choose an event", event_stats.sort_values('Event_Name')['Event_Name'])
        ev = event_stats.set_index('Event_Name').loc[event_choice]
        edf = df[df['Event_Name'] == event_choice]
        e_att = edf[edf['Attended']]

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
        if is_simulated:
            st.caption("On data from the built-in simulator, about 10% of comments are deliberately written to "
                   "contradict their rating, so the match rate there mostly reflects that injected noise.")

        col_pie, col_bar = st.columns(2)
        with col_pie:
            sent_dist = df[df['Attended']]['Sentiment'].value_counts()
            st.plotly_chart(
                px.pie(names=sent_dist.index, values=sent_dist.values, hole=0.5, title="Polarity Distribution"),
                width="stretch")
        with col_bar:
            fig_align = px.bar(event_stats, x='Event_Name', y='NLP_Match', color='NLP_Match',
                               title="Logic Match Rate (%)", color_continuous_scale='RdYlGn')
            st.plotly_chart(fig_align, width="stretch")

    with tab4:
        st.subheader("🏗️ Segment Analysis: Event Categories")
        st.markdown(
            "Compares event categories to help allocate future budgets and resources. Categories come from your "
            "data when it has them; otherwise events are labelled Technical or General from their names.")

        # Aggregated from individual rows so each response counts equally.
        seg_stats = segment_stats(df)

        c_seg1, c_seg2 = st.columns(2)
        with c_seg1:
            st.write("**Engagement per Category**")
            st.plotly_chart(
                px.bar(seg_stats, x='Event_Type', y='Agg_Engagement', color='Event_Type', title="Attendance % by Category"),
                width="stretch")
        with c_seg2:
            st.write("**Sentiment per Category**")
            st.plotly_chart(
                px.bar(seg_stats, x='Event_Type', y='Avg_Sentiment', color='Event_Type', title="Average Comment Sentiment by Category"),
                width="stretch")

    with tab5:
        st.subheader("📊 Data Science Pipeline & Methodology")

        st.markdown(f"""
        ### The Technical Journey of Your Data
        Every time the dashboard loads, the data goes through these steps:

        **1. Ingestion**
        Records come from one of three places: the **Firebase Firestore** collection `{COLLECTION}` (newest
        {FIRESTORE_READ_LIMIT:,}, with a warning if more exist), a local CSV, or **a file you upload** (CSV or
        Excel). Uploads are matched to EventFlow's columns, converted (Yes/No, "4 stars", 1–10 scales, dates) and
        validated, with a report of anything changed or skipped. Admins can also generate simulated data. Each simulated event gets
        its own fixed attendance rate, typical rating and date, and people can register for several events. The
        simulator deliberately adds noise: missing ratings, comments that contradict their rating, and duplicate rows.

        **2. Cleaning**
        Exact duplicates (same user, event and comment) are removed. Ratings from people who did not attend are
        discarded and missing comments are marked "No Comment Provided". Your own event categories are kept; events
        without one are labelled Technical or General from their names.

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

        *Limitations:* {"this data is simulated, so the results show how the pipeline behaves rather than real "
        "attendee opinion. " if is_simulated else ""}VADER only understands English and can misread sarcasm, and
        comparisons between event categories rest on however many events each category has.
        """)

        st.info("**Tech Stack:** Python 3, Streamlit, Firebase Admin SDK, VADER, pandas, Plotly.")

        if last_error:
            st.error(f"Last Pipeline Alert: {last_error}")

    with st.expander("🔍 View Processed Data (Deduplicated & Preprocessed)"):
        st.dataframe(df.sort_values(by='Timestamp', ascending=False) if 'Timestamp' in df.columns else df,
                     width="stretch")

elif source_pref == UPLOAD and uploaded is None:
    st.info("""
    ### 📥 Analyse your own event data
    Upload a **CSV or Excel** file in the sidebar, for example a Google Forms or Microsoft Forms export or a
    registration spreadsheet. Your column names don't need to match: you'll be shown how they were matched and
    can correct them.

    | Column | Needed? | Example |
    |---|---|---|
    | Event name | **required** | AI Workshop |
    | Star rating | rating and/or comment | 4, "4 stars", 8 (out of 10) |
    | Comment | rating and/or comment | "Great speaker, venue was cold" |
    | Attended? | optional | Yes / No |
    | Person ID | optional | p001 (anonymous IDs recommended) |
    | Event category | optional | Workshop |
    | Event date | optional | 2026-10-05 |

    Download the **template CSV** in the sidebar for an example. Your file is analysed in this session only and
    is not stored.
    """)
elif not (source_pref == UPLOAD and uploaded is not None):  # upload problems are reported by upload_flow
    st.warning("⚠️ No data available. Upload your own file (data source: **Upload your own file**), "
               "generate sample data with `python data_generator.py`, or enable admin mode "
               "(`EVENTFLOW_ADMIN=1`) and use Wipe & Regenerate in the sidebar.")
    if last_error:
        st.error(f"Last Pipeline Alert: {last_error}")
