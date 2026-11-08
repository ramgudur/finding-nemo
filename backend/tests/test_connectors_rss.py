from app.connectors import rss

# The exact shape sim/campus_alert.py's scripted feed emits.
FEED = (
    '<rss version="2.0" xmlns:georss="http://www.georss.org/georss"><channel>'
    "<title>Local news (scripted demo feed)</title><link>http://localhost/</link>"
    "<description>Scripted.</description>"
    "<item><title>Alert near campus</title><description>Something happened.</description>"
    '<guid isPermaLink="false">campus-alert-scripted-1</guid>'
    "<pubDate>Sun, 08 Nov 2026 05:00:00 GMT</pubDate>"
    "<georss:point>32.825 -96.7225</georss:point></item>"
    "</channel></rss>"
)


def test_parses_the_scripted_feeds_exact_shape():
    items = rss.parse_items(FEED)
    assert len(items) == 1
    item = items[0]
    assert item["doc_id"] == "campus-alert-scripted-1"
    assert item["title"] == "Alert near campus"
    assert item["lat"] == 32.825 and item["lon"] == -96.7225
    assert item["ts"].tzinfo is not None


def test_item_with_no_georss_point_is_skipped():
    feed = (
        '<rss version="2.0"><channel><item><title>No location</title>'
        '<guid isPermaLink="false">x</guid></item></channel></rss>'
    )
    assert rss.parse_items(feed) == []


def test_item_with_a_malformed_point_is_skipped():
    feed = (
        '<rss version="2.0" xmlns:georss="http://www.georss.org/georss"><channel>'
        '<item><title>Bad point</title><guid isPermaLink="false">y</guid>'
        "<georss:point>not-a-point</georss:point></item></channel></rss>"
    )
    assert rss.parse_items(feed) == []


def test_falls_back_to_link_then_title_when_no_guid():
    feed = (
        '<rss version="2.0" xmlns:georss="http://www.georss.org/georss"><channel>'
        "<item><title>Only a link</title><link>http://example.com/a</link>"
        "<georss:point>1.0 2.0</georss:point></item>"
        "<item><title>Only a title</title>"
        "<georss:point>3.0 4.0</georss:point></item>"
        "</channel></rss>"
    )
    items = rss.parse_items(feed)
    assert items[0]["doc_id"] == "http://example.com/a"
    assert items[1]["doc_id"] == "Only a title"


def test_missing_pubdate_falls_back_to_now_but_stays_aware():
    feed = (
        '<rss version="2.0" xmlns:georss="http://www.georss.org/georss"><channel>'
        '<item><title>No date</title><guid isPermaLink="false">z</guid>'
        "<georss:point>5.0 6.0</georss:point></item></channel></rss>"
    )
    item = rss.parse_items(feed)[0]
    assert item["ts"].tzinfo is not None


def test_multiple_items_all_parse():
    feed = (
        '<rss version="2.0" xmlns:georss="http://www.georss.org/georss"><channel>'
        + "".join(
            f'<item><title>Item {i}</title><guid isPermaLink="false">g{i}</guid>'
            f"<pubDate>Sun, 08 Nov 2026 05:0{i}:00 GMT</pubDate>"
            f"<georss:point>{i}.0 {i}.0</georss:point></item>"
            for i in range(3)
        )
        + "</channel></rss>"
    )
    items = rss.parse_items(feed)
    assert [i["doc_id"] for i in items] == ["g0", "g1", "g2"]
