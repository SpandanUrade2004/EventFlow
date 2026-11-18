"""Event-level statistics: sample sizes, confidence intervals, sample-size-adjusted rankings, keywords."""

from collections import Counter

import numpy as np
import pandas as pd

PRIOR_WEIGHT = 20  # pseudo-observations that pull small-sample averages toward the overall average
Z95 = 1.96

STOPWORDS = set("""a an the and or but of to in on at for with from by is was were be been are it its this that
these those i me my we our you your he she they them their very so too just really much more most no not nor
dont didnt wasnt isnt couldnt would could should will can had has have do did does all any some than then
there here what which who when where how also well bit quite
event events session""".split())  # last line: words every event comment shares


def wilson_interval(successes, n, z=Z95):
    """95% confidence interval for a proportion; behaves well for small n and rates near 0 or 1."""
    if n == 0:
        return np.nan, np.nan
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def shrunk_mean(total, n, prior_mean, prior_weight=PRIOR_WEIGHT):
    """Bayesian average: blends a group's mean with the overall mean, weighted by its sample size."""
    return (total + prior_weight * prior_mean) / (n + prior_weight)


def build_event_stats(df):
    """Per-event metrics, with sample sizes, confidence intervals and small-sample-adjusted rankings."""
    keys = ['Event_Name', 'Event_Type']
    attendees = df[df['Attended']]
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
    bounds = [wilson_interval(k, n) for k, n in zip(dropped, stats['Total_Reg'], strict=True)]
    stats['Drop_CI_Low'] = [b[0] for b in bounds]
    stats['Drop_CI_High'] = [b[1] for b in bounds]

    # Adjusted (shrunk) averages for ranking, so 3 five-star ratings can't outrank 300 averaging 4.8.
    stats['Adj_Rating'] = shrunk_mean(stats['Avg_Rating'].fillna(0) * stats['N_Ratings'], stats['N_Ratings'],
                                      attendees['Rating'].mean())
    stats['Adj_Drop_off'] = shrunk_mean(dropped, stats['Total_Reg'], dropped.sum() / stats['Total_Reg'].sum())
    return stats


def segment_stats(df):
    """Technical vs General, aggregated from rows so each response counts equally."""
    attendees = df[df['Attended']]
    seg = df.groupby('Event_Type').agg(Total_Reg=('Attended', 'count'), Total_Att=('Attended', 'sum')).join(
        attendees.groupby('Event_Type').agg(Avg_Rating=('Rating', 'mean'),
                                            Avg_Sentiment=('Senti_Score', 'mean'))).reset_index()
    seg['Agg_Engagement'] = seg['Total_Att'] / seg['Total_Reg'] * 100
    return seg


def rating_sentiment_correlation(df):
    """(spearman, pearson, n) over individual rated, commented responses. Spearman = Pearson on ranks."""
    scored = df[df['Attended'] & df['Rating'].notna() & df['Senti_Score'].notna()]
    if len(scored) < 10:
        return np.nan, np.nan, len(scored)
    spearman = scored['Rating'].rank().corr(scored['Senti_Score'].rank())
    return spearman, scored['Rating'].corr(scored['Senti_Score']), len(scored)


def returning_share(df):
    """Share of people who registered for more than one event."""
    return float((df.groupby('User_ID')['Event_Name'].nunique() > 1).mean())


def top_keywords(texts, n=8):
    words = Counter(w for t in texts for w in str(t).split() if len(w) > 2 and w not in STOPWORDS)
    return pd.DataFrame(words.most_common(n), columns=['Keyword', 'Mentions'])
