from datetime import UTC, datetime

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
    items = rss.parse_items(FEED, now=1_800_000_000.0)
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
    assert rss.parse_items(feed, now=1_800_000_000.0) == []


def test_item_with_a_malformed_point_is_skipped():
    feed = (
        '<rss version="2.0" xmlns:georss="http://www.georss.org/georss"><channel>'
        '<item><title>Bad point</title><guid isPermaLink="false">y</guid>'
        "<georss:point>not-a-point</georss:point></item></channel></rss>"
    )
    assert rss.parse_items(feed, now=1_800_000_000.0) == []


def test_falls_back_to_link_then_title_when_no_guid():
    feed = (
        '<rss version="2.0" xmlns:georss="http://www.georss.org/georss"><channel>'
        "<item><title>Only a link</title><link>http://example.com/a</link>"
        "<georss:point>1.0 2.0</georss:point></item>"
        "<item><title>Only a title</title>"
        "<georss:point>3.0 4.0</georss:point></item>"
        "</channel></rss>"
    )
    items = rss.parse_items(feed, now=1_800_000_000.0)
    assert items[0]["doc_id"] == "http://example.com/a"
    assert items[1]["doc_id"] == "Only a title"


def test_missing_pubdate_falls_back_to_the_given_now_not_wall_time():
    feed = (
        '<rss version="2.0" xmlns:georss="http://www.georss.org/georss"><channel>'
        '<item><title>No date</title><guid isPermaLink="false">z</guid>'
        "<georss:point>5.0 6.0</georss:point></item></channel></rss>"
    )
    item = rss.parse_items(feed, now=1_800_000_000.0)[0]
    assert item["ts"].tzinfo is not None
    assert item["ts"].timestamp() == 1_800_000_000.0  # the caller's `now`, so sim runs line up


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
    items = rss.parse_items(feed, now=1_800_000_000.0)
    assert [i["doc_id"] for i in items] == ["g0", "g1", "g2"]


# -- #259: Atom <entry> --------------------------------------------------------------------

ATOM = (
    '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:georss="http://www.georss.org/georss">'
    "<title>Area reports</title><id>urn:feed</id><updated>2026-11-08T06:00:00Z</updated>"
    "<entry><title>Report one</title><id>urn:entry:1</id>"
    '<link rel="alternate" href="https://example.org/1"/>'
    "<published>2026-11-08T05:00:00Z</published><summary>Something was reported.</summary>"
    "<georss:point>1.5 2.5</georss:point></entry>"
    '<entry><title>Report two</title><link href="https://example.org/2"/>'
    '<updated>2026-11-08T04:30:00+01:00</updated><content type="html">Body text.</content></entry>'
    "</feed>"
)


def test_atom_entries_are_parsed():
    items = rss.parse_items(ATOM, now=0.0, include_unlocated=True)
    assert len(items) == 2
    first, second = items
    assert first["doc_id"] == "urn:entry:1" and first["title"] == "Report one"
    assert first["description"] == "Something was reported."
    assert (first["lat"], first["lon"]) == (1.5, 2.5)
    assert first["ts"] == datetime(2026, 11, 8, 5, 0, tzinfo=UTC)
    assert second["doc_id"] == "https://example.org/2"  # no <id>: the link href
    assert second["description"] == "Body text." and second["lat"] is None
    assert second["ts"] == datetime(2026, 11, 8, 3, 30, tzinfo=UTC)  # normalized to UTC


def test_atom_without_points_keeps_only_located_entries_by_default():
    assert [i["doc_id"] for i in rss.parse_items(ATOM, now=0.0)] == ["urn:entry:1"]


def test_rss_link_is_the_doc_id_fallback():
    feed = "<rss><channel><item><title>T</title><link>https://example.org/a</link></item></channel></rss>"
    assert rss.parse_items(feed, now=0.0, include_unlocated=True)[0]["doc_id"] == "https://example.org/a"
