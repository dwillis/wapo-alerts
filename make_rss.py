"""Create an RSS feed with the latest alerts."""
from __future__ import annotations

import csv
from operator import itemgetter
from pathlib import Path

import dateutil.parser
from feedgen.entry import FeedEntry
from feedgen.feed import FeedGenerator

# Set directories we'll use
THIS_DIR = Path(__file__).parent.absolute()


def main():
    """Create an RSS feed with the latest alerts."""
    # Get data
    with open(THIS_DIR / "alerts.csv") as f:
        data = list(csv.DictReader(f))

    # Parse dates
    for r in data:
        r["discovered"] = dateutil.parser.isoparse(r["datetime"])

    # Sort reverse chronological
    sorted_data = sorted(
        data,
        key=itemgetter("datetime"),
        reverse=True,
    )

    # Create feed
    feed = FeedGenerator()
    feed.title("Latest alerts from washingtonpost.com")
    feed.link(href="https://github.com/dwillis/wapo-alerts")
    feed.description("An unofficial feed created by Derek Willis.")
    for r in sorted_data[:50]:
        entry = FeedEntry()
        entry.id(r["airshipId"])
        entry.title(r["alert_body"])
        entry.published(r["datetime"])
        entry.description(r["text"])
        feed.add_entry(entry, order="append")

    # Writet it out
    feed.rss_file(THIS_DIR / "site" / "latest.rss", pretty=True)


if __name__ == "__main__":
    main()