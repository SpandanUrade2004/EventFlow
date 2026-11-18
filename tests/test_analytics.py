import pandas as pd
import pytest

from eventflow.analytics import build_event_stats, shrunk_mean, top_keywords, wilson_interval
from eventflow.processing import process
from eventflow.simulation import generate_records


def test_wilson_interval_contains_rate_and_stays_in_bounds():
    low, high = wilson_interval(30, 100)
    assert 0 <= low < 0.30 < high <= 1
    assert wilson_interval(0, 10)[0] == 0.0
    assert wilson_interval(10, 10)[1] == 1.0


def test_wilson_interval_narrows_with_more_data():
    small = wilson_interval(3, 10)
    large = wilson_interval(300, 1000)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_shrunk_mean_pulls_small_samples_toward_prior():
    few = shrunk_mean(total=3 * 5.0, n=3, prior_mean=3.5, prior_weight=20)
    many = shrunk_mean(total=300 * 4.8, n=300, prior_mean=3.5, prior_weight=20)
    assert few == pytest.approx(3.7, abs=0.01)
    assert many > few  # 300 ratings at 4.8 outrank 3 ratings at 5.0


def _rows(event, n, rating, attended=True):
    return [{"User_ID": f"{event}-{i}", "Event_Name": event, "Attended": attended, "Rating": rating,
             "Feedback_Text": "Well organized."} for i in range(n)]


def test_adjusted_rating_ranks_large_sample_above_tiny_perfect_one():
    raw = _rows("Tiny", 3, 5) + _rows("Big", 300, 4.8) + _rows("Meh", 300, 3)
    stats = build_event_stats(process(pd.DataFrame(raw))).set_index("Event_Name")
    assert stats.loc["Tiny", "Avg_Rating"] > stats.loc["Big", "Avg_Rating"]
    assert stats["Adj_Rating"].idxmax() == "Big"


def test_event_stats_on_simulated_data():
    df = process(pd.DataFrame(generate_records(800, seed=11)))
    stats = build_event_stats(df)
    assert stats["Total_Reg"].sum() == len(df)
    assert (stats["Drop_CI_Low"] <= stats["Drop_off_Rate"]).all()
    assert (stats["Drop_off_Rate"] <= stats["Drop_CI_High"]).all()
    assert stats["Avg_Rating"].between(1, 5).all()


def test_top_keywords_skips_stopwords_and_short_words():
    kw = top_keywords(["the speaker was great", "great venue and a great speaker"])
    assert kw.iloc[0].tolist() == ["great", 3]
    assert not kw["Keyword"].isin(["the", "and", "was", "a"]).any()
