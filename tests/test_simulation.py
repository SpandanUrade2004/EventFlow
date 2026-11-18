import pandas as pd
import pytest

from eventflow.simulation import DEFAULT_EVENTS, classify_event, generate_records


@pytest.mark.parametrize("name, expected", [
    ("AI Workshop", "Technical"),
    ("Cloud Hackathon", "Technical"),
    ("Tech Horizon Hackathon", "Technical"),
    ("Career Fair", "General"),  # "fair" contains "ai" but is not the word "ai"
    ("Music Fest", "General"),
    ("Startup Pitch", "General"),
])
def test_classify_event(name, expected):
    assert classify_event(name) == expected


def test_seed_makes_output_reproducible():
    assert generate_records(200, seed=7) == generate_records(200, seed=7)


def test_records_have_expected_shape():
    df = pd.DataFrame(generate_records(500, seed=1))
    assert set(df.columns) >= {"User_ID", "Event_Name", "Event_Type", "Event_Date", "Attended", "Rating",
                               "Feedback_Text"}
    assert set(df["Event_Name"]) <= set(DEFAULT_EVENTS)
    assert df["Rating"].dropna().between(1, 5).all()
    # Non-attendees never have a rating or comment
    absent = df[~df["Attended"]]
    assert absent["Rating"].isna().all() and absent["Feedback_Text"].isna().all()


def test_each_event_has_one_fixed_date():
    df = pd.DataFrame(generate_records(500, seed=2))
    assert (df.groupby("Event_Name")["Event_Date"].nunique() == 1).all()


def test_events_differ_from_each_other():
    """Per-event profiles: attendance should vary between events, not be identical noise."""
    df = pd.DataFrame(generate_records(3000, seed=3))
    attendance = df.groupby("Event_Name")["Attended"].mean()
    assert attendance.max() - attendance.min() > 0.1


def test_no_person_registers_twice_for_same_event():
    df = pd.DataFrame(generate_records(1000, seed=4)).drop_duplicates()  # ignore injected exact duplicates
    assert not df.duplicated(["User_ID", "Event_Name"]).any()


def test_some_people_attend_several_events():
    df = pd.DataFrame(generate_records(1000, seed=5))
    assert (df.groupby("User_ID")["Event_Name"].nunique() > 1).any()
