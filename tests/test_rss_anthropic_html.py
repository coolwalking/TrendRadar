# coding=utf-8

import unittest
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import _bootstrap  # noqa: E402

_bootstrap._ensure_pkg("trendradar")
sys.modules["trendradar"].__path__ = [os.path.join(_bootstrap.ROOT, "trendradar")]
from trendradar.crawler.rss.fetcher import RSSFeedConfig, RSSFetcher


ANTHROPIC_NEWS_HTML = """
<a href="/news/claude-opus-4-8">
  <h2>Introducing Claude Opus 4.8</h2> Product May 28, 2026
  An upgrade to our Opus class of models.
</a>
<a href="/news/services-track-partner-hub">
  Jun 3, 2026 Announcements Introducing the Services Track and Partner Hub
</a>
<a href="/news/claude-opus-4-8">
  May 28, 2026 Product Introducing Claude Opus 4.8
</a>
<a href="/news/claude-design-anthropic-labs">
  Product Apr 17, 2026 <h3>Introducing Claude Design by Anthropic Labs</h3>
  Today, we're launching Claude Design.
</a>
"""


ANTHROPIC_RESEARCH_HTML = """
<a href="/research/team/alignment">Alignment</a>
<a href="/research/natural-language-autoencoders">
  Natural Language Autoencoders: Turning Claude's thoughts into text
  Interpretability May 7, 2026
  AI models like Claude talk in words but think in numbers.
</a>
<a href="/research/coding-agents-social-sciences">
  May 27, 2026 Economic Research Coding agents in the social sciences
</a>
"""


class TestAnthropicHtmlFeed(unittest.TestCase):
    def make_fetcher(self):
        return RSSFetcher(feeds=[], request_interval=0)

    def test_parses_news_cards_and_deduplicates_featured_item(self):
        feed = RSSFeedConfig(
            id="anthropic-news-openrss",
            name="Anthropic News",
            url="https://www.anthropic.com/news",
            source_type="anthropic_html",
            link_prefixes=["/news/"],
        )

        items = self.make_fetcher()._parse_anthropic_html(ANTHROPIC_NEWS_HTML, feed)

        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].title, "Introducing Claude Opus 4.8")
        self.assertEqual(items[0].published_at, "2026-05-28")
        self.assertEqual(items[0].url, "https://www.anthropic.com/news/claude-opus-4-8")
        self.assertIn("Product", items[0].summary)
        self.assertEqual(items[1].title, "Introducing the Services Track and Partner Hub")
        self.assertEqual(items[1].published_at, "2026-06-03")
        self.assertEqual(items[2].title, "Introducing Claude Design by Anthropic Labs")

    def test_parses_research_cards_and_skips_team_links(self):
        feed = RSSFeedConfig(
            id="anthropic-research-openrss",
            name="Anthropic Research",
            url="https://www.anthropic.com/research",
            source_type="anthropic_html",
            link_prefixes=["/research/", "/news/"],
        )

        items = self.make_fetcher()._parse_anthropic_html(ANTHROPIC_RESEARCH_HTML, feed)

        self.assertEqual(len(items), 2)
        self.assertEqual(
            items[0].title,
            "Natural Language Autoencoders: Turning Claude's thoughts into text",
        )
        self.assertEqual(items[0].published_at, "2026-05-07")
        self.assertEqual(items[0].author, "Anthropic")
        self.assertEqual(items[1].title, "Coding agents in the social sciences")
        self.assertEqual(items[1].published_at, "2026-05-27")


if __name__ == "__main__":
    unittest.main()
