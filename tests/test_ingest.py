import io

import pandas as pd
import pytest

from eventflow import storage
from eventflow.ingest import guess_mapping, guess_rating_scale, prepare_upload, read_table, template_csv
from eventflow.processing import process

GOOGLE_FORM_COLUMNS = ["Timestamp", "Email Address", "Which event did you attend?",
                       "How would you rate the event? (1-10)", "Any comments or suggestions?", "Did you attend?"]


def test_guess_mapping_understands_form_questions():
    m = guess_mapping(GOOGLE_FORM_COLUMNS)
    assert m == {"Event_Name": "Which event did you attend?",
                 "Rating": "How would you rate the event? (1-10)",
                 "Feedback_Text": "Any comments or suggestions?",
                 "Attended": "Did you attend?"}


def test_guess_mapping_matches_template_exactly():
    raw = read_table(template_csv().encode(), "template.csv")
    m = guess_mapping(raw.columns)
    assert all(m[c] == c for c in ["Event_Name", "Rating", "Feedback_Text", "Attended", "User_ID",
                                   "Event_Type", "Event_Date"])


def test_guess_rating_scale():
    raw = pd.DataFrame({"How would you rate the event? (1-10)": [8], "Stars": [4], "Score": [9]})
    assert guess_rating_scale(raw, "How would you rate the event? (1-10)") == 10
    assert guess_rating_scale(raw, "Stars") == 5
    assert guess_rating_scale(raw, "Score") == 10  # values above 5
    assert guess_rating_scale(raw, None) == 5


def test_read_table_handles_semicolons_bom_and_blank_rows():
    data = "﻿Event;Stars\nAI Workshop;4\n;\n".encode()
    df = read_table(data, "export.csv")
    assert list(df.columns) == ["Event", "Stars"] and len(df) == 1


def test_read_table_reads_excel():
    buf = io.BytesIO()
    pd.DataFrame({"Event": ["Music Fest"], "Rating": [5]}).to_excel(buf, index=False)
    assert read_table(buf.getvalue(), "data.xlsx").iloc[0].tolist() == ["Music Fest", 5]


def _upload(rows, mapping, **kw):
    return prepare_upload(pd.DataFrame(rows), mapping, **kw)


def test_values_are_converted():
    rows = {"Event": ["A", "A", "B", "B"], "Came": ["Yes", "no", "Y", "1"],
            "Stars": ["4 stars", "", "5", 3], "Notes": ["Great!", None, "  ", "ok"]}
    df, report = _upload(rows, {"Event_Name": "Event", "Attended": "Came", "Rating": "Stars",
                                "Feedback_Text": "Notes"})
    assert report.ok and report.rows_out == 4
    assert df["Attended"].tolist() == [True, False, True, True]
    assert df["Rating"].tolist()[0] == 4 and pd.isna(df["Rating"].tolist()[1])
    assert df["Feedback_Text"].tolist() == ["Great!", None, None, "ok"]
    assert (df["Source"] == "upload").all()


def test_ten_point_scale_is_halved():
    df, report = _upload({"Event": ["A", "A"], "Score": [8, 10]}, {"Event_Name": "Event", "Rating": "Score"},
                         rating_scale=10)
    assert df["Rating"].tolist() == [4, 5]
    assert any("halved" in n for n in report.notes)


def test_bad_rows_are_skipped_and_reported():
    rows = {"Event": ["A", "", "A", "A"], "Came": ["yes", "yes", "maybe", None], "Stars": [9, 4, 4, 4]}
    df, report = _upload(rows, {"Event_Name": "Event", "Attended": "Came", "Rating": "Stars"})
    assert report.rows_in == 4 and report.rows_out == 1
    text = " ".join(report.warnings)
    assert "no event name" in text and "'maybe'" in text and "no Attended value" in text
    assert "outside the 1–5 range" in text  # the 9 on a 5-point scale
    assert pd.isna(df["Rating"].iloc[0])


def test_missing_required_columns_are_errors():
    _, report = _upload({"Stars": [4]}, {"Rating": "Stars"})
    assert not report.ok and "Event name" in report.errors[0]
    _, report = _upload({"Event": ["A"]}, {"Event_Name": "Event"})
    assert not report.ok and "rating or Comment" in report.errors[0]


def test_optional_columns_get_defaults():
    df, report = _upload({"Event": ["A", "A"], "Stars": [4, 5]}, {"Event_Name": "Event", "Rating": "Stars"})
    assert report.ok
    assert df["Attended"].all()
    assert df["User_ID"].nunique() == 2  # every row is its own person
    assert any("drop-off is 0%" in n for n in report.notes)


def test_users_own_event_categories_survive_processing():
    df, _ = _upload({"Event": ["Career Fair", "AI Talk"], "Kind": ["Networking", ""], "Stars": [4, 5]},
                    {"Event_Name": "Event", "Event_Type": "Kind", "Rating": "Stars"})
    processed = process(df).set_index("Event_Name")
    assert processed.loc["Career Fair", "Event_Type"] == "Networking"  # kept as given
    assert processed.loc["AI Talk", "Event_Type"] == "Technical"       # blank -> keyword rule


def test_template_round_trips_through_the_pipeline():
    raw = read_table(template_csv().encode(), "template.csv")
    df, report = prepare_upload(raw, guess_mapping(raw.columns))
    processed = process(df)
    assert report.ok and len(processed) == 5
    assert set(processed["Event_Type"]) == {"Workshop", "Cultural"}


def test_records_from_frame_is_storage_ready():
    raw = read_table(template_csv().encode(), "template.csv")
    df, _ = prepare_upload(raw, guess_mapping(raw.columns))
    records = storage.records_from_frame(process(df))
    absent = next(r for r in records if r["Attended"] is False)
    assert absent["Rating"] is None and absent["Feedback_Text"] is None
    assert records[0]["Event_Date"] == "2026-10-05"
    assert all(type(r["Attended"]) is bool for r in records)


@pytest.mark.parametrize("name, expected", [("custom", "custom")])
def test_collection_name_from_env(monkeypatch, name, expected):
    monkeypatch.setenv("EVENTFLOW_COLLECTION", name)
    assert storage.collection_name() == expected
    monkeypatch.delenv("EVENTFLOW_COLLECTION")
    assert storage.collection_name({"collection": "from_secrets"}) == "from_secrets"
    assert storage.collection_name() == storage.DEFAULT_COLLECTION
