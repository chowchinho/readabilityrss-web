import pytest

from app.services import topic_classifier

def test_dropped_items_are_retried_not_silently_lost(monkeypatch):
    calls = []
    def fake_call(payload):
        calls.append([a["id"] for a in payload])
        if len(calls) == 1:
            return {"articles": [{"id": 1, "primary": "Travel", "secondary": [],
                                  "region": "Japan", "type": "Feature",
                                  "confidence": "high", "ai_summary": "s"}]}
        return {"articles": [{"id": 2, "primary": "Gaming", "secondary": [],
                              "region": "Taiwan", "type": "News",
                              "confidence": "high", "ai_summary": "s"}]}
    monkeypatch.setattr(topic_classifier, "_call_deepseek", fake_call)
    out = topic_classifier.tag_articles_batch(
        [{"id": 1, "title": "a", "body": "x", "feed_name": "F"},
         {"id": 2, "title": "b", "body": "y", "feed_name": "F"}])
    assert set(out) == {1, 2}
    assert calls[1] == [2], "second call must request only the missing id"

def test_request_pins_temperature_and_states_the_audience_rule(monkeypatch):
    captured = {}
    # Without a key the classifier logs a warning and returns before POSTing,
    # so the test would silently depend on a real key in backend/.env.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-not-a-real-secret")
    def fake_post(url, **kw):
        captured.update(kw["json"])
        raise RuntimeError("stop here — we only want the payload")
    monkeypatch.setattr(topic_classifier.requests, "post", fake_post)
    try:
        topic_classifier.tag_articles_batch(
            [{"id": 1, "title": "t", "body": "b", "feed_name": "F"}])
    except Exception:
        pass
    assert captured.get("temperature") == 0
    system = captured["messages"][0]["content"]
    assert "interest-community" in system
    assert "never in `primary`" in system or "never in primary" in system



# Every section-named feed in the live corpus as of 2026-08-10, with the publication
# a human would name. Regression guard for the "Hypebeast - Travel" class of error.
PUBLICATION_CASES = [
    ("Secret London - Food & Drink", "Secret London"),
    ("Secret London - Things to do", "Secret London"),
    ("Secret London - Top News", "Secret London"),
    ("My London - News", "My London"),
    ("FUNQ - OUTDOOR", "FUNQ"),
    ("FUNQ - LIFESTYLE", "FUNQ"),
    ("SCREEN FANDOM - 歐美娛樂癮迷！", "SCREEN FANDOM"),
    ("Hypebeast - Tech & gadgets", "Hypebeast"),
    ("Hypebeast - Travel", "Hypebeast"),
    ("CNX Software – Embedded Systems News", "CNX Software"),
    ("Engadget - Technology News & Expert Reviews", "Engadget"),
    ("オカルトラベル - 日本全国の心霊スポットと観光地を紹介しています。", "オカルトラベル"),
    ("Atlas Obscura - Latest Articles and Places", "Atlas Obscura"),
    ("Football | The Guardian", "The Guardian"),
    ("最新文章 | udn遊戲角落", "udn遊戲角落"),
    ("ROOMIE | 旅行", "ROOMIE"),
    ("電影 | 晞。觀影記事", "晞。觀影記事"),
    ("BBC News", "BBC News"),
    ("", ""),
]


@pytest.mark.parametrize("feed_name,expected", PUBLICATION_CASES)
def test_publication_name_strips_section_labels(feed_name, expected):
    assert topic_classifier.publication_name(feed_name) == expected


def test_tagging_request_disables_deliberation(monkeypatch):
    """Reasoning was ~7x the output cost and none of it is retained."""
    captured = {}

    def fake_post(url, **kw):
        captured.update(kw["json"])
        raise RuntimeError("payload captured")

    monkeypatch.setattr(topic_classifier.requests, "post", fake_post)
    monkeypatch.setattr(topic_classifier, "DEEPSEEK_API_KEY", "test-key")
    try:
        topic_classifier.tag_articles_batch(
            [{"id": 1, "title": "t", "body": "b", "feed_name": "F"}])
    except Exception:
        pass

    assert captured.get("thinking") == {"type": "disabled"}
    assert captured.get("model") == "deepseek-v4-flash"
    # Kept deliberately: re-enabling thinking must not reintroduce the truncation bug.
    assert captured.get("max_tokens") == 16384
