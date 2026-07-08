"""Tests for the sitemap scraper's meta-fetch cap (Trendy production incident:
1400+ page sitemaps with per-page meta fetches took 20-40+ min and left
permanently 'running' pipeline rows when Streamlit Cloud recycled mid-run)."""
from trendy.db import Portal, PublishedArticle
from trendy.sources import sitemap as sitemap_mod


def _mock_urls(monkeypatch, urls):
    monkeypatch.setattr(sitemap_mod, "_iter_sitemap_urls", lambda sitemap_url: iter(urls))


def _mock_meta(monkeypatch):
    calls = []

    def _fake_extract_meta(url):
        calls.append(url)
        return {"title": f"Title for {url}", "h1": "H1", "meta_description": "desc"}

    monkeypatch.setattr(sitemap_mod, "_extract_meta", _fake_extract_meta)
    monkeypatch.setattr(sitemap_mod.time, "sleep", lambda *_: None)
    return calls


def test_refresh_sitemap_caps_meta_fetches(db_session, monkeypatch):
    portal = Portal(key="big", name="Big Portal", url="https://big.example.com")
    db_session.add(portal)
    db_session.flush()

    urls = [f"https://big.example.com/article-{i}/" for i in range(20)]
    _mock_urls(monkeypatch, urls)
    calls = _mock_meta(monkeypatch)

    upserted = sitemap_mod.refresh_sitemap(portal, db_session, fetch_meta=True, max_meta_fetches=5)

    assert upserted == 20
    assert len(calls) == 5

    articles = db_session.query(PublishedArticle).filter_by(portal_id=portal.id).all()
    assert len(articles) == 20
    with_title = [a for a in articles if a.title]
    without_title = [a for a in articles if not a.title]
    assert len(with_title) == 5
    assert len(without_title) == 15
    # Every URL is stored regardless of whether it got meta — coverage matching
    # must see the full sitemap immediately, not just the meta-enriched slice.
    assert {a.url for a in articles} == set(urls)
    # Pages without fetched meta still get a usable slug (derived from the URL path).
    assert all(a.slug_normalized for a in without_title)


def test_refresh_sitemap_backfills_missing_meta_on_next_run(db_session, monkeypatch):
    """A page stored without meta (because the cap was hit) should still be a
    candidate for meta fetch on a later call — origin of the "backfills over
    subsequent runs" guarantee."""
    portal = Portal(key="big2", name="Big Portal 2", url="https://big2.example.com")
    db_session.add(portal)
    db_session.flush()

    urls = ["https://big2.example.com/a/", "https://big2.example.com/b/"]
    _mock_urls(monkeypatch, urls)
    _mock_meta(monkeypatch)

    sitemap_mod.refresh_sitemap(portal, db_session, fetch_meta=True, max_meta_fetches=1)
    articles = {a.url: a for a in db_session.query(PublishedArticle).filter_by(portal_id=portal.id).all()}
    assert articles["https://big2.example.com/a/"].title
    assert not articles["https://big2.example.com/b/"].title

    calls = _mock_meta(monkeypatch)  # fresh call tracker
    sitemap_mod.refresh_sitemap(portal, db_session, fetch_meta=True, max_meta_fetches=1)

    # Only the page still missing a title gets fetched this time.
    assert calls == ["https://big2.example.com/b/"]
    articles = {a.url: a for a in db_session.query(PublishedArticle).filter_by(portal_id=portal.id).all()}
    assert articles["https://big2.example.com/b/"].title
