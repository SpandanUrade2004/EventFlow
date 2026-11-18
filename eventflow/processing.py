"""Cleaning and sentiment scoring: turns raw records into an analysis-ready DataFrame."""

import re
import string

import numpy as np
import pandas as pd
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from eventflow.simulation import classify_event

NO_COMMENT = "No Comment Provided"
MIXED_BAND = 0.5  # |VADER compound| below this counts as consistent with a 3-star rating
_PUNCT = re.compile(f"[{re.escape(string.punctuation)}]")
_analyzer = SentimentIntensityAnalyzer()


def clean_text(text):
    """Lowercase and strip punctuation (used for keyword counts, not for VADER)."""
    return _PUNCT.sub("", str(text).lower()).strip()


def sentiment_label(score):
    if pd.isna(score):
        return "No Comment"
    return "Positive" if score >= 0.05 else ("Negative" if score <= -0.05 else "Neutral")


def is_aligned(rating, score):
    """1 if the comment's sentiment agrees with the star rating, 0 if not, NaN if either is missing."""
    if pd.isna(rating) or pd.isna(score):
        return np.nan
    if rating >= 4:
        return int(score >= 0.05)
    if rating <= 2:
        return int(score <= -0.05)
    # A 3-star review is "mixed": anything short of strongly polar text is consistent.
    return int(abs(score) < MIXED_BAND)


def process(df):
    """Normalises schema, removes duplicates, handles nulls and adds sentiment features. Returns a new frame."""
    df = df.copy()

    # Schema normalisation: older data may lack columns or carry the old Event_Type rule.
    df['Event_Type'] = df['Event_Name'].apply(classify_event)
    if 'Event_Date' in df.columns:
        df['Event_Date'] = pd.to_datetime(df['Event_Date'], errors='coerce')
    if 'Timestamp' not in df.columns:
        df['Timestamp'] = pd.NaT
    # Old CSVs hold the literal "Sentinel: ..." string; coerce those to NaT.
    df['Timestamp'] = pd.to_datetime(df['Timestamp'], errors='coerce', utc=True)
    if df['Attended'].dtype == object:
        df['Attended'] = df['Attended'].astype(str).str.lower().eq('true')
    df['Attended'] = df['Attended'].astype(bool)

    # Cleaning
    df = df.drop_duplicates(subset=['User_ID', 'Event_Name', 'Feedback_Text']).copy()
    df['Rating'] = pd.to_numeric(df['Rating'], errors='coerce')
    df.loc[~df['Attended'], 'Rating'] = np.nan  # ratings only count from attendees
    has_comment = df['Feedback_Text'].notna() & (df['Feedback_Text'].astype(str).str.strip() != "")
    df['Feedback_Text'] = df['Feedback_Text'].where(has_comment, NO_COMMENT)
    df['Cleaned_Feedback'] = df['Feedback_Text'].apply(clean_text)

    # Sentiment: VADER reads the ORIGINAL text, since punctuation ("!!") and capitals are intensity cues.
    # Rows without a comment stay NaN so they don't drag averages toward zero.
    df['Senti_Score'] = [
        _analyzer.polarity_scores(str(t))['compound'] if c else np.nan
        for t, c in zip(df['Feedback_Text'], has_comment, strict=True)
    ]
    df['Sentiment'] = df['Senti_Score'].apply(sentiment_label)
    df['NLP_Alignment'] = [is_aligned(r, s) for r, s in zip(df['Rating'], df['Senti_Score'], strict=True)]
    return df
