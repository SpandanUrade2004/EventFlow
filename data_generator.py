import pandas as pd
import numpy as np
from faker import Faker
import random
import uuid
import re
from datetime import datetime, timezone

# Initialize Faker
fake = Faker()

# Define the events for our simulation
EVENTS = [
    "Tech Horizon Hackathon",
    "Annual Cultural Fest",
    "Alumni Networking Meet",
    "AI & Machine Learning Seminar",
    "Campus Career Fair"
]

# Custom feedback pools to make sentiment analysis realistic
FEEDBACK_POOLS = {
    "positive": [
        "Absolutely loved it! Great experience.",
        "Very well organized and insightful.",
        "The speakers were amazing. Learned a lot.",
        "Best event of the year so far!",
        "Really engaging and fun. Would highly recommend."
    ],
    "neutral": [
        "It was okay, nothing special.",
        "Decent event, but could be better organized.",
        "Average experience. The venue was a bit crowded.",
        "Fine, but I expected more hands-on activities.",
        "Started late, but overall it was fine."
    ],
    "negative": [
        "Terrible organization. Complete waste of time.",
        "Very boring and unengaging.",
        "Too crowded, couldn't hear the speakers.",
        "Poorly managed. I left early.",
        "Disappointing. Expected much better from the college."
    ]
}


TECH_PATTERN = re.compile(r"\b(tech\w*|ai|hack\w*|workshop\w*)\b")


def classify_event(event_name):
    """Same Technical/General rule as app1.py (whole-word match)."""
    return "Technical" if TECH_PATTERN.search(event_name.lower()) else "General"


def generate_feedback(rating):
    """Generates realistic text feedback based on the numerical rating."""
    if pd.isna(rating):
        return None

    if rating >= 4:
        base = random.choice(FEEDBACK_POOLS["positive"])
    elif rating == 3:
        base = random.choice(FEEDBACK_POOLS["neutral"])
    else:
        base = random.choice(FEEDBACK_POOLS["negative"])

    # Add a little random fake text to make them unique
    return f"{base} {fake.sentence(nb_words=4)}"


def generate_synthetic_data(num_records=500):
    """Generates a dataframe of synthetic event registration and feedback data."""
    print(f"Generating {num_records} synthetic records...")

    data = []
    timestamp = datetime.now(timezone.utc).isoformat()
    for _ in range(num_records):
        user_id = str(uuid.uuid4())[:8]  # Short unique ID
        event = random.choice(EVENTS)

        # Simulate Attendance (approx 75% of registered actually attend)
        attended = np.random.choice([True, False], p=[0.75, 0.25])

        # Simulate Ratings & Feedback (Only if attended)
        if attended:
            # Introduce a slight bias: Hackathons get higher ratings, Career Fairs get mixed
            if event == "Tech Horizon Hackathon":
                rating = np.random.choice([3, 4, 5], p=[0.1, 0.4, 0.5])
            elif event == "Campus Career Fair":
                rating = np.random.choice([1, 2, 3, 4, 5], p=[0.1, 0.2, 0.3, 0.2, 0.2])
            else:
                rating = np.random.choice([1, 2, 3, 4, 5], p=[0.05, 0.1, 0.2, 0.4, 0.25])

            feedback_text = generate_feedback(rating)
        else:
            rating = np.nan
            feedback_text = np.nan

        data.append({
            "User_ID": user_id,
            "Event_Name": event,
            "Event_Type": classify_event(event),
            "Registered": True,
            "Attended": attended,
            "Rating": rating,
            "Feedback_Text": feedback_text,
            "Timestamp": timestamp
        })

    df = pd.DataFrame(data)

    # Introduce a few missing values intentionally for the "Data Cleaning" phase
    num_missing = int(num_records * 0.02)  # 2% missing ratings
    missing_indices = np.random.choice(df[df['Attended'] == True].index, num_missing, replace=False)
    df.loc[missing_indices, 'Rating'] = np.nan

    return df


if __name__ == "__main__":
    # Generate the dataset
    df = generate_synthetic_data(600)

    # Save to CSV
    output_filename = "eventflow_simulated_data.csv"
    df.to_csv(output_filename, index=False)

    print(f"\nSuccess! Data generated and saved to '{output_filename}'.")
    print("\nDataset Preview:")
    print(df.head())
    print(f"\nTotal Records: {len(df)}")