"""Bring your own data: read a CSV/Excel export, map its columns onto EventFlow's, and validate it.

Typical sources are Google Forms / Microsoft Forms exports, registration spreadsheets or ticketing exports.
The user's column names don't need to match ours: `guess_mapping` proposes a mapping, the user confirms it,
and `prepare_upload` converts values (Yes/No, "4 stars", 1-10 scales, dates) and reports what it changed.
"""

import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

NOT_IN_FILE = "— not in my file —"


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    required: bool
    help: str
    aliases: tuple


# Order matters: earlier fields claim matching columns first.
FIELDS = [
    Field("Event_Name", "Event name", True, "Which event the row is about.",
          ("event name", "event", "event title", "session", "session name", "workshop", "program", "activity")),
    Field("Rating", "Star rating", False, "A number such as 1–5 (or 1–10). Text like '4 stars' is fine.",
          ("rating", "stars", "star rating", "overall rating", "score", "satisfaction", "rate")),
    Field("Feedback_Text", "Comment", False, "The attendee's written feedback.",
          ("feedback", "comment", "comments", "review", "remarks", "suggestions", "feedback text", "thoughts")),
    Field("Attended", "Attended?", False,
          "Yes/No, True/False, 1/0 or similar. Leave unmapped if every row is from someone who attended "
          "(e.g. a feedback form); drop-off then can't be measured.",
          ("attended", "attendance", "attend", "present", "checked in", "check in", "showed up")),
    Field("User_ID", "Person ID", False,
          "An ID per person (anonymous IDs are best). Used to remove duplicate submissions and count "
          "returning attendees. Leave unmapped to treat every row as a different person.",
          ("user id", "participant id", "attendee id", "registration id", "student id", "roll no", "id")),
    Field("Event_Type", "Event category", False, "Your own categories (e.g. Workshop, Sports). "
          "If unmapped, events are labelled Technical/General from their names.",
          ("event type", "category", "type", "track")),
    Field("Event_Date", "Event date", False, "When the event took place.",
          ("event date", "date", "held on")),
]
FIELD_BY_NAME = {f.name: f for f in FIELDS}

TRUE_WORDS = {"true", "yes", "y", "1", "attended", "present", "checked in", "x", "✓", "t"}
FALSE_WORDS = {"false", "no", "n", "0", "absent", "not attended", "no show", "no-show", "f"}


@dataclass
class Report:
    rows_in: int = 0
    rows_out: int = 0
    errors: list = field(default_factory=list)    # blocking: nothing can be analysed
    warnings: list = field(default_factory=list)  # data was changed or dropped
    notes: list = field(default_factory=list)     # informational

    @property
    def ok(self):
        return not self.errors


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def read_table(data, filename):
    """Reads CSV or Excel bytes into a DataFrame (first sheet for Excel)."""
    name = filename.lower()
    if name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(data))
    else:
        for encoding in ("utf-8-sig", "latin-1"):  # utf-8-sig also strips Excel's byte-order mark
            try:
                # sep=None sniffs commas, semicolons or tabs (common in European Excel exports)
                df = pd.read_csv(io.BytesIO(data), encoding=encoding, sep=None, engine="python")
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("Could not read the file as text. Is it a CSV?")
    df.columns = [str(c).strip() for c in df.columns]
    return df.dropna(how="all")  # spreadsheets often carry empty trailing rows


def guess_mapping(columns):
    """Proposes {field name: column}. Exact alias matches win; then whole-word alias matches inside a column
    name (so a form question like 'How would you rate the event?' still maps to Rating)."""
    normalised = {c: _norm(c) for c in columns}
    mapping, used = {}, set()
    for exact in (True, False):
        for f in FIELDS:
            if f.name in mapping:
                continue
            for alias in f.aliases:
                match = next((c for c, n in normalised.items() if c not in used and (
                    n == alias if exact else re.search(rf"\b{re.escape(alias)}\b", n))), None)
                if match is not None:
                    mapping[f.name] = match
                    used.add(match)
                    break
    return mapping


MISSING, UNKNOWN = "missing", "unknown"


def guess_rating_scale(raw, column):
    """10 if the column header mentions a 10-point scale or values go above 5, else 5."""
    if not column:
        return 5
    if re.search(r"\b(1\s*(-|to|–)\s*10|out of 10|/\s*10)\b", str(column).lower()):
        return 10
    values = raw[column].map(_parse_number)
    return 10 if values.max(skipna=True) > 5 else 5


def _parse_bool(value):
    """True/False, or MISSING (blank) / UNKNOWN (unrecognised text)."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return MISSING
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, float, np.number)):
        return {1: True, 0: False}.get(value, UNKNOWN)
    text = str(value).strip().lower()
    if not text:
        return MISSING
    if text in TRUE_WORDS or _norm(text) in TRUE_WORDS:
        return True
    if text in FALSE_WORDS or _norm(text) in FALSE_WORDS:
        return False
    return UNKNOWN


def _parse_number(value):
    if pd.isna(value):
        return np.nan
    if isinstance(value, (int, float, np.number)):
        return float(value)
    m = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(m.group()) if m else np.nan


def _examples(values, n=3):
    return ", ".join(f"'{v}'" for v in list(dict.fromkeys(map(str, values)))[:n])


def prepare_upload(raw, mapping, rating_scale=5):
    """Converts a user's table into EventFlow's schema. Returns (DataFrame, Report).

    `mapping` is {field name: column name}; unmapped optional fields are filled with sensible defaults.
    `rating_scale` is 5 or 10; 10-point ratings are halved onto the 1–5 scale.
    """
    report = Report(rows_in=len(raw))
    missing = [FIELD_BY_NAME[f].label for f in ("Event_Name",) if not mapping.get(f)]
    if missing:
        report.errors.append(f"Choose which column holds: {', '.join(missing)}.")
        return pd.DataFrame(), report
    if not mapping.get("Rating") and not mapping.get("Feedback_Text"):
        report.errors.append("Map at least one of Star rating or Comment, otherwise there is nothing to analyse.")
        return pd.DataFrame(), report

    df = pd.DataFrame({name: raw[col] for name, col in mapping.items() if col})

    # Event name: required per row
    df["Event_Name"] = df["Event_Name"].astype("string").str.strip()
    blank = df["Event_Name"].isna() | (df["Event_Name"] == "")
    if blank.any():
        report.warnings.append(f"Skipped {int(blank.sum())} rows with no event name.")
        df = df[~blank]

    # Attended
    if "Attended" in df:
        parsed = df["Attended"].map(_parse_bool)
        unknown, missing_att = parsed.eq(UNKNOWN), parsed.eq(MISSING)
        if unknown.any():
            report.warnings.append(f"Skipped {int(unknown.sum())} rows with an unrecognised Attended value "
                                   f"({_examples(df.loc[unknown, 'Attended'])}). Use Yes/No or True/False.")
        if missing_att.any():
            report.warnings.append(f"Skipped {int(missing_att.sum())} rows with no Attended value.")
        keep = ~(unknown | missing_att)
        df = df[keep].copy()
        df["Attended"] = parsed[keep].astype(bool)
    else:
        df["Attended"] = True
        report.notes.append("No attendance column: every row is treated as an attendee, so drop-off is 0%.")

    # Rating
    if "Rating" in df:
        original = df["Rating"]
        ratings = original.map(_parse_number)
        unparsed = ratings.isna() & original.notna() & (original.astype(str).str.strip() != "")
        if unparsed.any():
            report.warnings.append(f"Ignored {int(unparsed.sum())} ratings that aren't numbers "
                                   f"({_examples(original[unparsed])}).")
        if rating_scale == 10:
            ratings = ratings / 2
            report.notes.append("Ratings were out of 10 and have been halved onto the 1–5 scale.")
        elif ratings.max(skipna=True) > 5:
            report.warnings.append("Some ratings are above 5. If your scale is 1–10, choose 'out of 10'.")
        out_of_range = ratings.notna() & ~ratings.between(1, 5)
        if out_of_range.any():
            report.warnings.append(f"Ignored {int(out_of_range.sum())} ratings outside the 1–5 range.")
            ratings = ratings.where(~out_of_range)
        df["Rating"] = ratings
    else:
        df["Rating"] = np.nan
        report.notes.append("No rating column: rating-based charts will be empty.")

    # Comments
    if "Feedback_Text" in df:
        text = df["Feedback_Text"].astype("string").str.strip()
        # Plain None (not pd.NA) for blanks: pd.NA raises on truth tests further down the pipeline.
        df["Feedback_Text"] = [t if isinstance(t, str) and t else None for t in text]
    else:
        df["Feedback_Text"] = None
        report.notes.append("No comment column: sentiment analysis is skipped.")

    # Person ID
    if "User_ID" in df:
        ids = df["User_ID"].astype("string").str.strip()
        no_id = ids.isna() | (ids == "")
        df["User_ID"] = ids.where(~no_id, [f"row-{i}" for i in df.index])
        if no_id.any():
            report.notes.append(f"{int(no_id.sum())} rows had no ID and were treated as separate people.")
    else:
        df["User_ID"] = [f"row-{i}" for i in df.index]

    # Optional extras
    if "Event_Type" in df:
        df["Event_Type"] = df["Event_Type"].astype("string").str.strip().replace("", pd.NA)
    if "Event_Date" in df:
        dates = pd.to_datetime(df["Event_Date"], errors="coerce", dayfirst=False)
        if (dates.isna() & df["Event_Date"].notna()).any():
            report.warnings.append("Some event dates couldn't be read and were left blank.")
        df["Event_Date"] = dates

    for col in ("Event_Name", "User_ID", "Event_Type"):  # pandas "string" dtype -> plain objects downstream
        if col in df:
            df[col] = df[col].astype(object).where(df[col].notna(), None)
    df["Registered"] = True
    df["Timestamp"] = datetime.now(timezone.utc).isoformat()
    df["Source"] = "upload"
    report.rows_out = len(df)
    if df.empty:
        report.errors.append("No usable rows left after validation.")
    return df.reset_index(drop=True), report


def template_csv():
    """A small example file showing the expected columns."""
    rows = [
        ("p001", "AI Workshop", "Workshop", "2026-10-05", "Yes", 5, "Brilliant speaker, learned a lot!"),
        ("p002", "AI Workshop", "Workshop", "2026-10-05", "Yes", 3, "Good content but it started late."),
        ("p003", "AI Workshop", "Workshop", "2026-10-05", "No", "", ""),
        ("p001", "Music Fest", "Cultural", "2026-10-19", "Yes", 4, "Great vibe, venue was a bit crowded."),
        ("p004", "Music Fest", "Cultural", "2026-10-19", "Yes", 2, "Sound system kept failing."),
    ]
    cols = ["User_ID", "Event_Name", "Event_Type", "Event_Date", "Attended", "Rating", "Feedback_Text"]
    return pd.DataFrame(rows, columns=cols).to_csv(index=False)
