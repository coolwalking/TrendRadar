import unittest

from generate_digest import CATEGORIES, _filter_frontier_voice_candidates, select_prompt_candidates


def make_item(category, source, score, age="", region="西方", title=None):
    return {
        "category": category,
        "source": source,
        "score": score,
        "age": age,
        "region": region,
        "title": title or f"{category}-{source}-{score}",
        "summary": "",
        "url": "",
        "tag": "",
    }


class GenerateDigestPromptSelectionTests(unittest.TestCase):
    def test_select_prompt_candidates_caps_each_category(self):
        items = []
        for category in CATEGORIES:
            for i in range(12):
                items.append(make_item(category, f"{category}-source-{i % 4}", 100 - i))

        selected = select_prompt_candidates(items, per_category_limit=5)

        counts = {category: 0 for category in CATEGORIES}
        for item in selected:
            counts[item["category"]] += 1

        self.assertEqual(counts, {category: 5 for category in CATEGORIES})

    def test_select_prompt_candidates_preserves_chinese_candidates(self):
        category = "AI 领域"
        items = [
            make_item(category, "western-high", 100 - i, region="西方")
            for i in range(12)
        ]
        items.extend(
            make_item(category, f"china-{i}", 10 - i, region="中国")
            for i in range(3)
        )

        selected = select_prompt_candidates(items, per_category_limit=8)

        self.assertGreaterEqual(
            sum(1 for item in selected if item["region"] == "中国"),
            2,
        )

    def test_select_prompt_candidates_keeps_recent_items_ahead_of_stale_ties(self):
        category = "国际局势"
        recent = make_item(category, "recent", 50, age="[AGE: 2h]")
        stale = make_item(category, "stale", 50, age="[AGE: 9d]")

        selected = select_prompt_candidates([stale, recent], per_category_limit=1)

        self.assertEqual(selected[0]["source"], "recent")

    def test_filter_frontier_voice_candidates_drops_non_ai_noise(self):
        voices = [
            {
                "source": "theo-von",
                "title": "balcony theft",
                "summary": "A comedy short unrelated to technology.",
            },
            {
                "source": "web3-sky-city",
                "title": "谷歌最大的AI展示活动：值得关注的看点",
                "summary": "Gemini、智能体与 TPU 是核心信号。",
            },
            {
                "source": "acquired",
                "title": "Our episode on Vanguard is now live!",
                "summary": "A history of index funds and investor ownership.",
            },
            {
                "source": "all-in-pod",
                "title": "I am going to probably use $300M of Anthropic this year at Salesforce",
                "summary": "Marc Benioff discusses enterprise AI model spend.",
            },
        ]

        selected = _filter_frontier_voice_candidates(voices, max_per_source=2)
        titles = [item["title"] for item in selected]

        self.assertNotIn("balcony theft", titles)
        self.assertNotIn("Our episode on Vanguard is now live!", titles)
        self.assertIn("谷歌最大的AI展示活动：值得关注的看点", titles)
        self.assertIn("I am going to probably use $300M of Anthropic this year at Salesforce", titles)

    def test_filter_frontier_voice_candidates_limits_single_source(self):
        voices = [
            {"source": "web3-sky-city", "title": f"AI 访谈 {i}", "summary": "OpenAI and Gemini"}
            for i in range(3)
        ]
        voices.append({"source": "openai-blog", "title": "OpenAI model update", "summary": "New AI model"})

        selected = _filter_frontier_voice_candidates(voices, max_per_source=2)

        self.assertEqual(
            sum(1 for item in selected if item["source"] == "web3-sky-city"),
            2,
        )
        self.assertEqual(selected[-1]["source"], "openai-blog")

    def test_filter_frontier_voice_candidates_ignores_sponsor_ai_mentions(self):
        voices = [
            {
                "source": "theo-von",
                "title": "Mike Tyson (Live at the Wiltern)",
                "summary": "Sponsored by Perplexity AI. The episode is about boxing and childhood.",
            },
            {
                "source": "hard-fork",
                "title": "Are Amazon Employees Unnecessarily Using A.I. Agents?",
                "summary": "A discussion about workplace AI adoption.",
            },
        ]

        selected = _filter_frontier_voice_candidates(voices, max_per_source=2)

        self.assertEqual([item["source"] for item in selected], ["hard-fork"])


if __name__ == "__main__":
    unittest.main()
