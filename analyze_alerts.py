"""Analyze Washington Post alerts and produce JSON output files for the analysis dashboard."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import anthropic
    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

THIS_DIR   = Path(__file__).parent.absolute()
ALERTS_PATH = THIS_DIR / "alerts.json"
SITE_DIR   = THIS_DIR / "site"
CACHE_PATH = SITE_DIR / "analysis_cache.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STOP_WORDS = frozenset([
    "a", "about", "above", "after", "again", "against", "all", "am", "an",
    "and", "any", "are", "aren't", "as", "at", "be", "because", "been",
    "before", "being", "below", "between", "both", "but", "by", "can't",
    "cannot", "could", "couldn't", "did", "didn't", "do", "does", "doesn't",
    "doing", "don't", "down", "during", "each", "few", "for", "from",
    "further", "get", "got", "had", "hadn't", "has", "hasn't", "have",
    "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "here's", "hers", "herself", "him", "himself", "his", "how", "how's",
    "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't",
    "it", "it's", "its", "itself", "let's", "me", "more", "most", "mustn't",
    "my", "myself", "no", "nor", "not", "of", "off", "on", "once", "only",
    "or", "other", "ought", "our", "ours", "ourselves", "out", "over",
    "own", "s", "same", "shan't", "she", "she'd", "she'll", "she's",
    "should", "shouldn't", "so", "some", "such", "than", "that", "that's",
    "the", "their", "theirs", "them", "themselves", "then", "there",
    "there's", "these", "they", "they'd", "they'll", "they're", "they've",
    "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've",
    "were", "weren't", "what", "what's", "when", "when's", "where",
    "where's", "which", "while", "who", "who's", "whom", "why", "why's",
    "will", "with", "won't", "would", "wouldn't", "you", "you'd", "you'll",
    "you're", "you've", "your", "yours", "yourself", "yourselves", "said",
    "says", "new", "one", "two", "three", "also", "now", "just", "like",
    "may", "first", "last", "year", "years", "day", "days", "week", "weeks",
    "time", "times", "make", "made", "take", "taken", "use", "used",
])

URGENCY_VOCAB = frozenset([
    "breaking", "urgent", "developing", "alert", "emergency",
    "crisis", "exclusive", "confirmed", "warning", "immediately",
    "just in", "now", "update", "correction", "dead", "killed",
    "shoots", "shooting", "explosion", "attack", "arrest", "shot",
    "fire", "crash", "terror", "bomb", "violence", "death", "dies",
    "murdered", "convicted", "indicted", "impeached", "resigns",
])

ENTITY_BLOCKLIST = frozenset([
    "Record Answer", "Ask Sahaj", "Carolyn Hax", "The Trump",
    "Play Today", "How Many", "Right Answer", "The Record",
    "Today News", "This Week", "Last Week", "Next Week",
    "This Year", "Last Year", "Breaking News", "Post Ping",
    "Washington Post", "The Post",  # too generic for entity timeline
])

ENTITY_ALIASES: dict[str, str | None] = {
    "President Donald Trump": "Donald Trump",
    "President Trump": "Donald Trump",
    "Former President Trump": "Donald Trump",
    "Former President Donald Trump": "Donald Trump",
    "Mr Trump": "Donald Trump",
    "Donald J Trump": "Donald Trump",
    "President Joe Biden": "Joe Biden",
    "President Biden": "Joe Biden",
    "Former President Joe Biden": "Joe Biden",
    "Former President Biden": "Joe Biden",
    "Mr Biden": "Joe Biden",
    "Vice President Kamala Harris": "Kamala Harris",
    "Vice President Harris": "Kamala Harris",
    "Kennedy Jr": "Robert F. Kennedy Jr.",
    "RFK Jr": "Robert F. Kennedy Jr.",
    "Elon Musk": "Elon Musk",  # keep as-is
    "The Washington Post": None,  # too generic
    "The President": None,
    "The White House": "White House",
    "Supreme Court": "Supreme Court",
    "United States": None,
}

# Topics that produce formulaic/meaningless text analysis
FORMULAIC_TOPICS = frozenset(["news_quiz"])
EXCLUDE_FROM_ENTITIES = frozenset(["news_quiz", "advice"])
EXCLUDE_FROM_THREADS = frozenset(["news_quiz"])
EXCLUDE_FROM_DIGESTS = frozenset(["news_quiz", "advice"])


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_alerts() -> list[dict]:
    """Load alerts.json and return a flat list of normalized dicts."""
    with open(ALERTS_PATH) as f:
        raw = json.load(f)

    alerts = []
    for item in raw:
        custom = (
            item.get("notification", {})
                .get("ios", {})
                .get("extra", {})
                .get("custom", {})
        )
        ios_alert = (
            item.get("notification", {})
                .get("ios", {})
                .get("alert", {})
        )
        datetime_str = custom.get("datetime", "")
        if not datetime_str:
            continue
        try:
            dt = datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))
        except ValueError:
            continue

        # ISO week in format "2024-W09"
        week = dt.strftime("%G-W%V")
        year_month = dt.strftime("%Y-%m")

        alerts.append({
            "id":         item.get("airshipId", ""),
            "title":      ios_alert.get("title", ""),
            "body":       ios_alert.get("body", custom.get("text", "")),
            "text":       custom.get("text", ""),
            "topic":      custom.get("targetTopic", "unknown"),
            "dt":         dt,
            "url":        custom.get("contentURL") or "",
            "week":       week,
            "year_month": year_month,
        })

    return alerts


def load_cache() -> dict:
    """Load the analysis cache, or return empty structure if missing."""
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"digests": {}, "urgency": {}}


def save_cache(cache: dict) -> None:
    SITE_DIR.mkdir(exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f)


def write_json(path: Path, data: object) -> None:
    SITE_DIR.mkdir(exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, stripping punctuation."""
    return re.findall(r"\b[a-z]{3,}\b", text.lower())


def content_tokens(text: str) -> list[str]:
    """Tokens excluding stop words."""
    return [t for t in tokenize(text) if t not in STOP_WORDS]


def is_week_complete(week_label: str) -> bool:
    """True if the week ended more than 7 days ago (safe to cache forever)."""
    year_s, week_s = week_label.split("-W")
    # Sunday (0) of that ISO week = end of week
    week_end = datetime.strptime(f"{year_s}-W{week_s}-0", "%G-W%V-%w")
    return (datetime.utcnow() - week_end).days > 7


# ---------------------------------------------------------------------------
# Computational analysis functions
# ---------------------------------------------------------------------------

def compute_temporal_heatmap(alerts: list[dict]) -> dict:
    """Build a 7x24 matrix of alert counts (overall and per topic)."""
    overall = [[0] * 24 for _ in range(7)]
    by_topic: dict[str, list[list[int]]] = {}

    for a in alerts:
        dow = a["dt"].weekday()   # 0=Monday
        hour = a["dt"].hour
        overall[dow][hour] += 1
        topic = a["topic"]
        if topic not in by_topic:
            by_topic[topic] = [[0] * 24 for _ in range(7)]
        by_topic[topic][dow][hour] += 1

    return {"overall": overall, "by_topic": by_topic}


def compute_tfidf(alerts: list[dict]) -> dict:
    """Compute TF-IDF top 50 terms per topic, treating each topic as one document."""
    # Aggregate text per topic
    topic_texts: dict[str, list[str]] = defaultdict(list)
    for a in alerts:
        topic_texts[a["topic"]].append(a["body"] + " " + a["text"])

    topics = list(topic_texts.keys())
    n_topics = len(topics)

    # Build per-topic token counts
    topic_counts: dict[str, Counter] = {}
    for topic, texts in topic_texts.items():
        combined = " ".join(texts)
        topic_counts[topic] = Counter(content_tokens(combined))

    # IDF: how many topics contain each word
    doc_freq: Counter = Counter()
    for counts in topic_counts.values():
        for word in counts:
            doc_freq[word] += 1

    # Global top 100
    global_counter: Counter = Counter()
    for counts in topic_counts.values():
        global_counter.update(counts)
    global_top = [{"word": w, "count": c} for w, c in global_counter.most_common(100)]

    by_topic = {}
    for topic in topics:
        counts = topic_counts[topic]
        total = sum(counts.values())
        if total == 0:
            continue

        if topic in FORMULAIC_TOPICS:
            by_topic[topic] = {"formulaic": True, "terms": []}
            continue

        n_alerts = len([a for a in alerts if a["topic"] == topic])
        if n_alerts < 10:
            by_topic[topic] = {"too_few_alerts": True, "terms": []}
            continue

        scored = []
        for word, cnt in counts.items():
            tf  = cnt / total
            idf = math.log(n_topics / doc_freq[word]) if doc_freq[word] else 0
            scored.append({"word": word, "score": round(tf * idf, 6), "count": cnt})

        scored.sort(key=lambda x: x["score"], reverse=True)
        by_topic[topic] = {"terms": scored[:50]}

    return {"by_topic": by_topic, "global_top": global_top}


def extract_entities(alerts: list[dict]) -> dict:
    """Regex-based proper noun phrase extraction with alias normalization."""
    # Compile pattern once
    pattern = re.compile(r"(?<![.\w])[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+")

    entity_by_week: dict[str, Counter] = defaultdict(Counter)
    entity_total: Counter = Counter()

    for a in alerts:
        if a["topic"] in EXCLUDE_FROM_ENTITIES:
            continue
        text = (a["body"] or "") + " " + (a["text"] or "")
        for match in pattern.findall(text):
            # Apply alias map
            name = ENTITY_ALIASES.get(match, match)
            if name is None:
                continue
            # Apply blocklist
            if name in ENTITY_BLOCKLIST:
                continue
            # Skip very short or single-word (regex guarantees 2+ words, but check length)
            if len(name) < 5:
                continue
            entity_by_week[a["week"]][name] += 1
            entity_total[name] += 1

    # Keep top 75 by total mentions, minimum 5 occurrences
    top_names = [name for name, cnt in entity_total.most_common(75) if cnt >= 5]

    # Collect all weeks that appear
    all_weeks_set = set()
    for week_counts in entity_by_week.values():
        all_weeks_set.update(week_counts.keys())

    # Sort weeks chronologically
    weeks_ordered = sorted(all_weeks_set)

    top_entities = []
    for name in top_names:
        by_week = {}
        for week, counts in entity_by_week.items():
            if name in counts:
                by_week[week] = counts[name]
        top_entities.append({
            "name": name,
            "total": entity_total[name],
            "by_week": by_week,
        })

    return {"weeks_ordered": weeks_ordered, "top_entities": top_entities}


def detect_news_waves(alerts: list[dict]) -> list[dict]:
    """Find clusters of 3+ breaking-news alerts within a 6-hour window sharing keywords."""
    breaking = sorted(
        [a for a in alerts if a["topic"] == "breaking-news"],
        key=lambda a: a["dt"],
    )
    window = timedelta(hours=6)
    waves = []
    i = 0

    while i < len(breaking):
        cluster = [breaking[i]]
        j = i + 1
        while j < len(breaking) and (breaking[j]["dt"] - breaking[i]["dt"]) <= window:
            cluster.append(breaking[j])
            j += 1

        if len(cluster) >= 3:
            # Find shared keywords (in 2+ alerts)
            all_kw: Counter = Counter()
            per_alert_kw = [
                set(t for t in content_tokens(a["body"]) if len(t) >= 4)
                for a in cluster
            ]
            for kw_set in per_alert_kw:
                all_kw.update(kw_set)
            shared = [kw for kw, cnt in all_kw.items() if cnt >= 2]

            if shared:
                waves.append({
                    "start": cluster[0]["dt"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end":   cluster[-1]["dt"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "count": len(cluster),
                    "keywords": shared[:8],
                    "alerts": [
                        {"id": a["id"], "body": a["body"],
                         "dt": a["dt"].strftime("%Y-%m-%dT%H:%M:%SZ")}
                        for a in cluster
                    ],
                })
                i = j
                continue
        i += 1

    return waves


def compute_urgency_scores(alerts: list[dict]) -> dict:
    """Rule-based urgency score 1-10 for each alert."""
    scores = {}
    for a in alerts:
        text = (a["body"] + " " + a["title"]).lower()
        score = 1
        if a["topic"] == "breaking-news":
            score += 3
        for word in URGENCY_VOCAB:
            if word in text:
                score += 1
        # ALL-CAPS 4+ char words (exclude known acronyms like FBI, CIA, etc.)
        caps = re.findall(r"\b[A-Z]{4,}\b", a["body"])
        score += len(caps)
        if a["topic"] in FORMULAIC_TOPICS:
            score = 1
        scores[a["id"]] = {"score": max(1, min(10, score)), "source": "computational"}
    return scores


def detect_story_threads(alerts: list[dict]) -> list[dict]:
    """Group alerts into story threads by URL or body similarity."""
    # Group by URL first (skip formulaic topics)
    url_groups: dict[str, list[dict]] = defaultdict(list)
    no_url: list[dict] = []

    for a in alerts:
        if a["topic"] in EXCLUDE_FROM_THREADS:
            continue
        if a["url"]:
            url_groups[a["url"]].append(a)
        else:
            no_url.append(a)

    threads = []

    # URL-based threads (2+ alerts sharing same URL)
    for url, group in url_groups.items():
        if len(group) < 2:
            continue
        sorted_group = sorted(group, key=lambda x: x["dt"])
        topics = list({a["topic"] for a in group})
        thread_id = hashlib.sha256(url.encode()).hexdigest()[:8]
        threads.append({
            "thread_id": thread_id,
            "alert_count": len(group),
            "date_range": [
                sorted_group[0]["dt"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                sorted_group[-1]["dt"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            ],
            "topics": topics,
            "url": url,
            "alerts": [
                {"id": a["id"], "body": a["body"],
                 "dt": a["dt"].strftime("%Y-%m-%dT%H:%M:%SZ"), "topic": a["topic"]}
                for a in sorted_group
            ],
        })

    # Similarity-based threads for no-URL alerts (limit to 72-hour window)
    # Sort by dt for efficiency
    no_url.sort(key=lambda a: a["dt"])
    used = set()
    sim_window = timedelta(hours=72)

    for i, a in enumerate(no_url):
        if a["id"] in used:
            continue
        kw_a = set(t for t in content_tokens(a["body"]) if len(t) >= 4)
        if not kw_a:
            continue
        cluster = [a]
        for j in range(i + 1, len(no_url)):
            b = no_url[j]
            if b["id"] in used:
                continue
            if (b["dt"] - a["dt"]) > sim_window:
                break
            kw_b = set(t for t in content_tokens(b["body"]) if len(t) >= 4)
            if not kw_b:
                continue
            union = len(kw_a | kw_b)
            if union == 0:
                continue
            jaccard = len(kw_a & kw_b) / union
            if jaccard > 0.45:
                cluster.append(b)
        if len(cluster) >= 2:
            for c in cluster:
                used.add(c["id"])
            sorted_cluster = sorted(cluster, key=lambda x: x["dt"])
            canonical = " ".join(a["body"] for a in cluster)
            thread_id = hashlib.sha256(canonical[:200].encode()).hexdigest()[:8]
            threads.append({
                "thread_id": thread_id,
                "alert_count": len(cluster),
                "date_range": [
                    sorted_cluster[0]["dt"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    sorted_cluster[-1]["dt"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                ],
                "topics": list({a["topic"] for a in cluster}),
                "url": None,
                "alerts": [
                    {"id": c["id"], "body": c["body"],
                     "dt": c["dt"].strftime("%Y-%m-%dT%H:%M:%SZ"), "topic": c["topic"]}
                    for c in sorted_cluster
                ],
            })

    # Sort threads by alert count descending
    threads.sort(key=lambda t: t["alert_count"], reverse=True)
    return threads


def compute_vocab_diversity(alerts: list[dict]) -> dict:
    """Per-topic type-token ratio and average body length."""
    topic_tokens: dict[str, list[str]] = defaultdict(list)
    topic_bodies: dict[str, list[str]] = defaultdict(list)

    for a in alerts:
        tokens = tokenize(a["body"])
        topic_tokens[a["topic"]].extend(tokens)
        topic_bodies[a["topic"]].append(a["body"])

    by_topic = {}
    for topic, tokens in topic_tokens.items():
        total = len(tokens)
        unique = len(set(tokens))
        bodies = topic_bodies[topic]
        avg_len = sum(len(b) for b in bodies) / len(bodies) if bodies else 0
        ttr = unique / total if total > 0 else 0
        by_topic[topic] = {
            "ttr":             round(ttr, 3),
            "total_tokens":    total,
            "unique_tokens":   unique,
            "alert_count":     len(bodies),
            "avg_body_length": round(avg_len, 1),
        }

    return {"by_topic": by_topic}


def compute_publishing_patterns(alerts: list[dict]) -> dict:
    """Monthly volume and topic mix over time."""
    monthly_volume: Counter = Counter()
    monthly_topics: dict[str, Counter] = defaultdict(Counter)

    for a in alerts:
        ym = a["year_month"]
        monthly_volume[ym] += 1
        monthly_topics[ym][a["topic"]] += 1

    monthly_topic_mix = {}
    for ym, topic_counts in monthly_topics.items():
        total = sum(topic_counts.values())
        monthly_topic_mix[ym] = {
            t: round(c / total, 3) for t, c in topic_counts.items()
        }

    return {
        "monthly_volume":    dict(sorted(monthly_volume.items())),
        "monthly_topic_mix": dict(sorted(monthly_topic_mix.items())),
    }


# ---------------------------------------------------------------------------
# Claude API functions
# ---------------------------------------------------------------------------

def call_weekly_digest(client, week_label: str, week_alerts: list[dict]) -> dict:
    """Call Claude to produce a structured digest for one week."""
    alert_lines = "\n".join(
        f"- [{a['topic']}] {a['body']}"
        for a in week_alerts
        if a["topic"] not in EXCLUDE_FROM_DIGESTS
    )
    if not alert_lines:
        return {"error": True, "reason": "no_content"}

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=900,
        system=(
            "You are a news analyst. Respond ONLY with valid JSON. "
            "No markdown fences, no explanation, no preamble."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"Analyze Washington Post SMS alerts from {week_label}.\n\n"
                f"Alerts:\n{alert_lines}\n\n"
                "Return this exact JSON structure:\n"
                '{\n'
                '  "top_stories": [\n'
                '    {"headline": "str", "summary": "2-3 sentences", "topics": ["str"], "alert_count": int}\n'
                '  ],\n'
                '  "week_theme": "One sentence characterizing this week\'s news",\n'
                '  "notable_entities": ["name"],\n'
                '  "cross_topic_stories": [{"story": "str", "topics": ["str"]}]\n'
                '}\n'
                "top_stories must have exactly 5 items (or fewer if fewer distinct stories exist). "
                "notable_entities: top 5 people/orgs mentioned."
            ),
        }],
    )

    raw = response.content[0].text.strip()
    # Strip accidental markdown fences
    raw = re.sub(r"^```json?\s*|\s*```$", "", raw, flags=re.DOTALL)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"  Warning: Could not parse Claude response for {week_label}")
        return {"error": True, "reason": "parse_failed", "raw": raw[:300]}


def call_urgency_batch(client, batch: list[dict]) -> dict:
    """Score a batch of alerts for urgency via Claude."""
    lines = "\n".join(f"{a['id']}: {a['body']}" for a in batch)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system="Rate news alert urgency 1-10. Respond ONLY with JSON.",
        messages=[{
            "role": "user",
            "content": (
                "Rate each alert's urgency (1=routine evergreen, "
                "10=immediate breaking life-safety news).\n"
                'Return JSON: {"alert_id": {"score": int, "reason": "word"}}\n\n'
                f"Alerts:\n{lines}"
            ),
        }],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```json?\s*|\s*```$", "", raw, flags=re.DOTALL)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def compute_weekly_digests(client, alerts: list[dict], cache: dict) -> dict:
    """Produce Claude digests for each ISO week, using cache to skip old completed weeks."""
    if "digests" not in cache:
        cache["digests"] = {}

    # Group alerts by week
    weekly: dict[str, list[dict]] = defaultdict(list)
    for a in alerts:
        weekly[a["week"]].append(a)

    for week_key in sorted(weekly.keys()):
        # Skip if cached and week is complete
        if week_key in cache["digests"] and is_week_complete(week_key):
            cached = cache["digests"][week_key]
            if isinstance(cached, dict) and not cached.get("error"):
                continue

        week_alerts = weekly[week_key]
        print(f"  Generating digest for {week_key} ({len(week_alerts)} alerts)...")
        try:
            result = call_weekly_digest(client, week_key, week_alerts)
            # Add metadata
            year_s, week_s = week_key.split("-W")
            week_start = datetime.strptime(f"{year_s}-W{week_s}-1", "%G-W%V-%w")
            week_end   = datetime.strptime(f"{year_s}-W{week_s}-0", "%G-W%V-%w")
            result["week_start"]   = week_start.strftime("%Y-%m-%d")
            result["week_end"]     = week_end.strftime("%Y-%m-%d")
            result["alert_count"]  = len(week_alerts)
            cache["digests"][week_key] = result
        except Exception as e:
            print(f"  Warning: Claude API error for {week_key}: {e}")
        time.sleep(0.5)

    return cache["digests"]


def compute_claude_urgency_subset(client, alerts: list[dict], cache: dict) -> dict:
    """Score a subset of breaking-news alerts via Claude."""
    if "urgency" not in cache:
        cache["urgency"] = {}

    cutoff = datetime.now(timezone.utc) - timedelta(weeks=4)
    candidates = [
        a for a in alerts
        if a["topic"] == "breaking-news"
        and a["dt"] >= cutoff
        and a["id"] not in cache["urgency"]
    ]

    # Process in batches of 30
    for i in range(0, len(candidates), 30):
        batch = candidates[i:i + 30]
        print(f"  Scoring urgency for batch of {len(batch)} alerts...")
        try:
            results = call_urgency_batch(client, batch)
            for alert_id, data in results.items():
                cache["urgency"][alert_id] = {
                    "score":  data.get("score", 5),
                    "reason": data.get("reason", ""),
                    "source": "claude",
                }
        except Exception as e:
            print(f"  Warning: Urgency batch error: {e}")
        time.sleep(0.5)

    return {
        aid: {"score": data["score"], "source": "claude"}
        for aid, data in cache["urgency"].items()
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Loading alerts...")
    alerts = load_alerts()
    print(f"  {len(alerts)} alerts loaded.")

    cache = load_cache()
    alerts_by_id = {a["id"]: a for a in alerts}

    # --- Computational passes (always run) ---
    print("Computing heatmap...")
    heatmap = compute_temporal_heatmap(alerts)

    print("Computing TF-IDF...")
    tfidf = compute_tfidf(alerts)

    print("Extracting entities...")
    entities = extract_entities(alerts)

    print("Detecting news waves...")
    waves = detect_news_waves(alerts)
    print(f"  Found {len(waves)} waves.")

    print("Computing urgency scores...")
    urgency = compute_urgency_scores(alerts)

    print("Detecting story threads...")
    threads = detect_story_threads(alerts)
    print(f"  Found {len(threads)} threads.")

    print("Computing vocab diversity...")
    vocab = compute_vocab_diversity(alerts)

    print("Computing publishing patterns...")
    patterns = compute_publishing_patterns(alerts)

    # --- Claude passes (conditional) ---
    digests: dict = cache.get("digests", {})
    if CLAUDE_AVAILABLE and os.getenv("ANTHROPIC_API_KEY"):
        print("Running Claude analysis...")
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        digests = compute_weekly_digests(client, alerts, cache)
        claude_urgency = compute_claude_urgency_subset(client, alerts, cache)
        urgency.update(claude_urgency)
        cache["last_run"] = datetime.now(timezone.utc).isoformat()
        save_cache(cache)
        print("  Claude analysis complete.")
    else:
        print("  Skipping Claude analysis (no API key or library).")

    # --- Write output files ---
    print("Writing output files...")

    # Only include urgency for breaking-news alerts and Claude-scored ones to keep file small
    urgency_out = {
        aid: v["score"]
        for aid, v in urgency.items()
        if v.get("source") == "claude" or alerts_by_id.get(aid, {}).get("topic") == "breaking-news"
    }

    # Trim alert bodies in waves/threads to keep file size down
    def trim_alert(a: dict, max_len: int = 120) -> dict:
        out = dict(a)
        if "body" in out and len(out["body"]) > max_len:
            out["body"] = out["body"][:max_len] + "…"
        return out

    waves_out = [
        {**w, "alerts": [trim_alert(a) for a in w["alerts"]]}
        for w in waves
    ]
    threads_out = [
        {**t, "alerts": [trim_alert(a) for a in t["alerts"]]}
        for t in threads[:150]
    ]

    write_json(SITE_DIR / "analysis_meta.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_alerts": len(alerts),
        "heatmap":      heatmap,
        "waves":        waves_out,
        "threads":      threads_out,
        "urgency":      urgency_out,
        "vocab":        vocab,
        "patterns":     patterns,
    })
    write_json(SITE_DIR / "analysis_tfidf.json",    tfidf)
    write_json(SITE_DIR / "analysis_entities.json", entities)
    write_json(SITE_DIR / "analysis_digests.json",  {"weeks": digests})

    print("Done.")


if __name__ == "__main__":
    main()
