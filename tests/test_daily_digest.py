import unittest
from datetime import date
from zoneinfo import ZoneInfo

from trendradar.daily_digest import DigestItem, parse_github_trending_html, sort_digest_items


class DailyDigestTests(unittest.TestCase):
    def test_parse_github_trending_html_creates_evidence_bound_project_items(self):
        html = """
        <article class="Box-row">
          <h2><a href="/acme/ai-medic">
            <span>acme</span> / ai-medic
          </a></h2>
          <p class="col-9 color-fg-muted my-1 pr-4">
            Open source AI assistant for biomedical engineering papers.
          </p>
          <span itemprop="programmingLanguage">Python</span>
          <span>123 stars today</span>
        </article>
        <article class="Box-row">
          <h2><a href="/sponsors/example">Sponsor page</a></h2>
          <p class="col-9 color-fg-muted my-1 pr-4">Not a repository.</p>
        </article>
        """
        source = {"id": "github-trending", "name": "GitHub Trending", "category": "github"}

        items = parse_github_trending_html(html, source, date(2026, 4, 30), ZoneInfo("America/Los_Angeles"), 10)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "acme/ai-medic")
        self.assertEqual(items[0].source_category, "github")
        self.assertEqual(items[0].matched_topics, ["GitHub 热点项目"])
        self.assertIn("Open source AI assistant", items[0].summary)
        self.assertIn("Python", items[0].summary)
        self.assertIn("123 stars today", items[0].summary)

    def test_sort_digest_items_respects_topic_priority_before_score(self):
        github_item = DigestItem(
            title="owner/repo",
            source_id="github-trending",
            source_name="GitHub Trending",
            source_category="github",
            url="https://github.com/owner/repo",
            published_at="2026-04-30T00:00:00-07:00",
            summary="A repo",
            matched_topics=["GitHub 热点项目"],
            score=1,
        )
        ai_item = DigestItem(
            title="AI funding news",
            source_id="tech",
            source_name="Tech Source",
            source_category="tech",
            url="https://example.com/ai",
            published_at="2026-04-30T12:00:00-07:00",
            summary="AI AI AI AI AI",
            matched_topics=["AI 与前沿模型"],
            score=8,
        )
        topics = [
            {"name": "GitHub 热点项目", "priority": 1},
            {"name": "AI 与前沿模型", "priority": 2},
        ]

        sorted_items = sort_digest_items([ai_item, github_item], topics)

        self.assertEqual(sorted_items[0].title, "owner/repo")


if __name__ == "__main__":
    unittest.main()
