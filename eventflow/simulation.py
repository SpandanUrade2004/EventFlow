"""Synthetic event registration and feedback data, with deliberate noise to exercise the cleaning steps."""

import random
import re
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np

DEFAULT_EVENTS = ["AI Workshop", "Cloud Hackathon", "Music Fest", "Startup Pitch", "Career Fair"]
PROFILES = ["Realistic", "Positive", "Critical"]

# Whole-word match so e.g. "Career Fair" is not tagged Technical because "fair" contains "ai".
TECH_PATTERN = re.compile(r"\b(tech\w*|ai|hack\w*|workshop\w*)\b")

TEMPLATES = {
    5: ["Outstanding organization!", "The speaker was phenomenal.", "Exceeded all expectations.",
        "Best event ever."],
    4: ["Very good session, well worth it.", "Solid content and networking.", "Learned skills.", "Well organized."],
    3: ["It was okay, basic.", "Average experience.", "Good topics, poor management.", "Decent."],
    2: ["Disorganized, crowded.", "Content didn't match.", "Waste of time.", "Hard to follow."],
    1: ["Total disaster.", "Worst event.", "No coordination.", "Left early."],
}

MISSING_RATING_RATE = 0.05  # attendees who leave no star rating
MISMATCH_RATE = 0.10        # comments written to contradict the rating
DUPLICATE_RATE = 0.02       # records submitted twice


def classify_event(event_name):
    """'Technical' if the name contains a tech keyword as a whole word, else 'General'."""
    return "Technical" if TECH_PATTERN.search(str(event_name).lower()) else "General"


def make_event_profiles(event_names, profile="Realistic", today=None):
    """Fixes each event's attendance probability, typical rating and date once.

    Drawing these per event (not per record) is what makes events genuinely differ from each other.
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


def generate_records(num_records, event_names=None, profile="Realistic", seed=None):
    """Builds simulated registration records (no I/O). Injected duplicates appear as repeated dicts.

    Pass `seed` for reproducible output.
    """
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)
    event_names = list(event_names or DEFAULT_EVENTS)
    profiles = make_event_profiles(event_names, profile)
    # A shared pool of people, so some register for several events (enables returning-attendee stats).
    user_pool = [str(uuid.UUID(int=random.getrandbits(128)))[:8] for _ in range(max(10, int(num_records * 0.7)))]
    used_pairs = set()
    records = []
    for _ in range(num_records):
        event = random.choice(event_names)
        att_p, base_r = profiles[event]["att_p"], profiles[event]["base_r"]

        # One registration per person per event; fall back to a new person if no sampled one is free.
        user_id = next((u for u in random.sample(user_pool, min(5, len(user_pool)))
                        if (u, event) not in used_pairs), str(uuid.UUID(int=random.getrandbits(128)))[:8])
        used_pairs.add((user_id, event))

        attended = bool(np.random.choice([True, False], p=[att_p, 1 - att_p]))
        rating, feedback = None, None
        if attended:
            if random.random() > MISSING_RATING_RATE:
                rating = int(np.clip(np.rint(np.random.normal(base_r, 0.8)), 1, 5))
            sim_rating = rating if rating else 3
            if random.random() < MISMATCH_RATE:
                feedback = random.choice(TEMPLATES[1 if sim_rating >= 4 else 5])
            else:
                feedback = random.choice(TEMPLATES[sim_rating])

        data = {
            "User_ID": user_id,
            "Event_Name": event,
            "Event_Type": classify_event(event),
            "Event_Date": profiles[event]["date"],
            "Registered": True,
            "Attended": attended,
            "Rating": rating,
            "Feedback_Text": feedback,
            "Source": "simulated",
        }
        records.append(data)
        if random.random() < DUPLICATE_RATE:
            records.append(dict(data))
    return records
