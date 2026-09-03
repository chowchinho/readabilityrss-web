import asyncio
import pytest
from app.database import Database


@pytest.fixture
def vote_db(tmp_path):
    db = Database(str(tmp_path / "votes.db"))
    asyncio.run(db.init())
    yield db
    asyncio.run(db.close())


LABELS = {
    "primary_topic": "Consumer Tech",
    "secondary_topics": ["Samsung", "Ringtone"],
    "region": "Global",
    "article_type": "News",
}


def test_upsert_creates_one_row(vote_db):
    asyncio.run(vote_db.upsert_article_vote(1, "show_more", LABELS))
    rows = asyncio.run(vote_db.get_article_votes())
    assert len(rows) == 1
    assert rows[0]["vote"] == "show_more"
    assert rows[0]["secondary_topics"] == ["Samsung", "Ringtone"]


def test_voting_twice_leaves_one_row(vote_db):
    asyncio.run(vote_db.upsert_article_vote(1, "show_more", LABELS))
    asyncio.run(vote_db.upsert_article_vote(1, "show_more", LABELS))
    assert len(asyncio.run(vote_db.get_article_votes())) == 1


def test_changing_vote_replaces_it(vote_db):
    asyncio.run(vote_db.upsert_article_vote(1, "show_more", LABELS))
    asyncio.run(vote_db.upsert_article_vote(1, "show_less", LABELS))
    rows = asyncio.run(vote_db.get_article_votes())
    assert len(rows) == 1
    assert rows[0]["vote"] == "show_less"


def test_delete_removes_the_row(vote_db):
    asyncio.run(vote_db.upsert_article_vote(1, "show_more", LABELS))
    asyncio.run(vote_db.delete_article_vote(1))
    assert asyncio.run(vote_db.get_article_votes()) == []


def test_get_single_vote(vote_db):
    assert asyncio.run(vote_db.get_article_vote(1)) is None
    asyncio.run(vote_db.upsert_article_vote(1, "show_less", LABELS))
    assert asyncio.run(vote_db.get_article_vote(1))["vote"] == "show_less"


def test_labels_survive_missing_values(vote_db):
    asyncio.run(vote_db.upsert_article_vote(2, "show_more", {}))
    row = asyncio.run(vote_db.get_article_vote(2))
    assert row["primary_topic"] is None
    assert row["secondary_topics"] == []


def test_behavioural_signals_exclude_votes_and_impressions(vote_db):
    asyncio.run(vote_db.insert_events([
        {"article_id": 1, "event_type": "open", "dwell_seconds": 30,
         "primary_topic": "Consumer Tech"},
        {"article_id": 1, "event_type": "impression", "primary_topic": "Consumer Tech"},
        {"article_id": 1, "event_type": "show_more", "primary_topic": "Consumer Tech"},
        {"article_id": 2, "event_type": "hover", "dwell_seconds": 5,
         "primary_topic": "Gaming"},
        {"article_id": 3, "event_type": "skip", "dwell_seconds": 0,
         "primary_topic": "Politics"},
        {"article_id": 4, "event_type": "read_no_vote", "primary_topic": "Politics"},
    ]))
    rows = asyncio.run(vote_db.get_behavioural_signals())
    kinds = sorted(r["event_type"] for r in rows)
    assert kinds == ["hover", "open", "read_no_vote", "skip"]


def test_impression_counts_accept_a_since_filter(vote_db):
    asyncio.run(vote_db.insert_events([
        {"article_id": 1, "event_type": "impression", "primary_topic": "Travel",
         "created_at": "2020-01-01 00:00:00"},
        {"article_id": 2, "event_type": "impression", "primary_topic": "Travel"},
    ]))
    assert asyncio.run(vote_db.get_tag_impression_counts())["Travel"] == 2
    recent = asyncio.run(vote_db.get_tag_impression_counts(since="2025-01-01 00:00:00"))
    assert recent["Travel"] == 1
