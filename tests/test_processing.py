import math

import pandas as pd
import pytest

from eventflow.processing import NO_COMMENT, clean_text, is_aligned, process


@pytest.mark.parametrize("rating, score, expected", [
    (5, 0.8, 1), (5, -0.6, 0),      # high rating needs positive text
    (1, -0.7, 1), (2, 0.4, 0),      # low rating needs negative text
    (3, 0.3, 1), (3, -0.4, 1),      # 3 stars: anything short of strongly polar is consistent
    (3, 0.9, 0),
])
def test_is_aligned(rating, score, expected):
    assert is_aligned(rating, score) == expected


@pytest.mark.parametrize("rating, score", [(float("nan"), 0.5), (4, float("nan"))])
def test_is_aligned_missing_values_are_excluded(rating, score):
    assert math.isnan(is_aligned(rating, score))


def test_clean_text():
    assert clean_text("  Great, LOVED it!! ") == "great loved it"


def _raw(**overrides):
    row = {"User_ID": "u1", "Event_Name": "Career Fair", "Event_Type": "Technical", "Registered": True,
           "Attended": True, "Rating": 5, "Feedback_Text": "Outstanding organization!",
           "Timestamp": "2026-11-18T00:00:00+00:00"}
    row.update(overrides)
    return row


def test_process_dedupes_and_reclassifies():
    df = process(pd.DataFrame([_raw(), _raw(), _raw(User_ID="u2")]))
    assert len(df) == 2
    assert (df["Event_Type"] == "General").all()  # stored "Technical" label is corrected


def test_process_drops_ratings_from_non_attendees():
    df = process(pd.DataFrame([_raw(Attended=False, Rating=4)]))
    assert df["Rating"].isna().all()


def test_missing_comment_is_not_scored():
    df = process(pd.DataFrame([_raw(Feedback_Text=None)]))
    row = df.iloc[0]
    assert row["Feedback_Text"] == NO_COMMENT
    assert math.isnan(row["Senti_Score"]) and math.isnan(row["NLP_Alignment"])
    assert row["Sentiment"] == "No Comment"


def test_old_sentinel_timestamps_become_nat():
    df = process(pd.DataFrame([_raw(Timestamp="Sentinel: Value used to set a document field...")]))
    assert df["Timestamp"].isna().all()


def test_string_booleans_from_csv_are_parsed():
    df = process(pd.DataFrame([_raw(Attended="True"), _raw(User_ID="u2", Attended="False")]))
    assert df["Attended"].tolist() == [True, False]
