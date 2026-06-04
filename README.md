# TrendRadar

Information Environment Anomaly Monitoring & Public Opinion Radar Tool

[![License](https://img.shields.io/badge/license-GPL--3.0-blue.svg?style=flat-square)](LICENSE)
[![Version](https://img.shields.io/badge/version-v6.9.0-blue.svg)](https://github.com/carrot-peace/TrendRadar)

**English** | **[中文](README-CN.md)**

---

## What This Project Is

TrendRadar monitors anomaly signals across multi-platform information environments. It simultaneously ingests data from Chinese hot-list / social platforms and RSS / international media / official sources, classifies sources into evidence tiers (A / B / C / D), and identifies through programmatic rules:

- **Cross-layer resonance** — Social / trending platforms and primary or background sources appear simultaneously
- **High-heat unverified** — D-tier platforms show rising propagation, but lack A / B / C source corroboration
- **Chinese-only heat** — Internal warming within the Chinese information environment, but missing A / B international or official background sources
- **Silence gap** — A / B background sources carry information, but Chinese social platforms show weak response
- **Suppressed / background items** — Information present but below anomaly thresholds

## What This Project Is Not

- Not a news client or RSS reader
- Not a news product centered on newsletter or hot-topic digest consumption
- Not a fact-checking tool — it does not judge true or false
- Not a public opinion conclusion generator — it does not output "event has occurred"
- Not a system that lets AI directly judge truth or falsehood

## What Problem It Solves

Chinese internet hot-list platforms (Weibo, Douyin, Zhihu, etc.) carry early-stage propagation value, but their credibility is unstable. International media, official sources, technical communities, and financial sources provide background reference, but are naturally isolated from Chinese social platform information streams.

TrendRadar's value is placing these sources into a unified observation framework, examining whether "heat" and "evidence tier" are misaligned: a topic trending on D-tier platforms does not mean the event has been established; a topic with background information on A / B sources but silence on Chinese platforms is also a signal worth observing.

---

## Core Mechanisms

### Multi-Source Collection

Simultaneously crawls two categories of data sources:

- **Hot-list platforms**: Weibo, Baidu, Douyin, Zhihu, Bilibili, Toutiao, The Paper, Wall Street CN, CLS, iFeng, Tieba
- **RSS feeds**: Official blogs from OpenAI, Anthropic, Google AI; international media from Reuters, AP, BBC, NYT, Washington Post; financial sources like Yahoo Finance; technical communities like Hacker News

Data sourced from the [newsnow](https://github.com/ourongxing/newsnow) open-source project API.

### Source Tier Classification

Each data source is mapped to an evidence tier (defined in `config/source_tiers.yaml`):

| Tier | Meaning | Examples |
|------|---------|----------|
| **A** | Primary / official sources | OpenAI official blog, Anthropic research publications, Google DeepMind Blog |
| **B** | International media / background sources | Reuters, AP, BBC, NYT, Hacker News, Yahoo Finance |
| **C** | Chinese relatively serious information sources | The Paper, Wall Street CN, CLS, Zhihu, Baidu |
| **D** | High-timeliness low-credibility propagation platforms | Weibo, Douyin, Tieba, Bilibili |
| **unknown** | Unconfigured tier | — |

**Important: Tiers are source labels, not factuality judgments. D-tier heat only indicates that propagation is occurring, not that the event has been established.**

### Programmatic Evidence Classification

The program (`trendradar/ai/evidence.py`) performs the following steps for each topic group before AI involvement:

1. Aggregates which A / B / C / D sources the topic has hit, grouped by tier
2. Calculates D-tier heat (platform count, highest ranking)
3. Detects sentiment signals (strong emotion words in titles)
4. Assigns a unique `evidence_label` (verification status) based on rules
5. Allocates the topic group to a unique section (bucket)

**Verification status is uniquely determined by the program; AI may not alter it.**

### Restrained AI Writing

AI (optional, requires API Key configuration) has one sole task: based on the program-generated evidence summary, write restrained daily report text for each already-classified topic. AI does not:

- Introduce new facts, numbers, or persons not in the evidence summary
- Alter verification status or section allocation
- Rewrite D-tier propagation as factual events
- Output investment advice, action guidance, or trend predictions

### Reports & Push Notifications

Generates an Information Environment Anomaly Monitoring Daily Report (HTML), and can push to: Feishu, DingTalk, WeWork, Telegram, Email, Slack, Bark, ntfy, Generic Webhook.

---

## Anomaly Section Reference

| Section | Verification Status | Meaning |
|---------|-------------------|---------|
| **Cross-layer resonance** | Cross-layer resonance exists | D-tier has heat, and A / B background sources corroborate. Cross-layer source resonance exists, but not all D-tier claims are confirmed |
| **High-heat unverified** | High-heat unverified | Pure D-tier high heat, no A / B / C corroboration. Can only confirm propagation is occurring, cannot confirm the event has been established |
| **Chinese-only heat** | Chinese sources resonate (missing A / B background) | D-tier has heat and C serious sources corroborate, but missing A / B primary / international background sources. Internal warming in Chinese information environment; should not be directly treated as a factually significant event |
| **Silence gap** | Silence gap | A / B background sources carry information, but D-tier has no heat. Background sources have information, Chinese social platforms show no clear response |
| **Suppressed** | — | Information below anomaly thresholds (low heat, low-heat sentiment clusters, etc.), for dashboard counts only |

Additionally, the program identifies **sentiment signals** as secondary attribute annotations: when strong emotion words appear in titles, a `sentiment_flag` is set, but this does not constitute an independent section.

---

## AI Involvement Boundaries

This project has explicit boundaries for AI usage:

| Handled by Program | Handled by AI |
|--------------------|---------------|
| Source tier classification (A / B / C / D) | Readable expression |
| Verification status determination | Restrained summary / analysis text |
| Section allocation (bucketing) | One-sentence overview interpretation |
| Report skeleton & data statistics | — |
| Heat calculation & ranking tracking | — |

What AI cannot do:

1. Introduce new facts, numbers, or conclusions beyond the evidence summary
2. Alter verification status or move sections
3. Rewrite D-tier high-heat propagation as factual events
4. Provide guidance to investors / brands / the public
5. Make trend predictions or write grand narratives
6. sample_titles (representative propagation texts) are only "someone is saying / spreading this", not factual sources

---

## Quick Start

### Requirements

- Python >= 3.12
- Package manager: [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Local Run

```bash
# Clone repository
git clone https://github.com/carrot-peace/TrendRadar.git
cd TrendRadar

# Install dependencies with uv
uv sync

# Edit configuration (see "Configuration Entry Points" below)
cp config/config.yaml config/config.yaml.bak
# Edit config/config.yaml to configure data sources and push channels

# Run
uv run python -m trendradar
```

### Docker Deployment

```bash
cd docker
cp .env.example .env
# Edit .env to configure environment variables
docker compose up -d
```

### GitHub Actions Deployment

1. Fork this repository
2. Configure required environment variables in repository Settings → Secrets (push Webhooks, AI API Key, etc.)
3. Enable the scheduled task in `.github/workflows/crawler.yml`

> This deployment method requires remote storage, Secrets, and workflow configuration. Currently recommended for experienced users.

### AI Analysis (Optional)

AI analysis requires an API Key. If you do not use AI analysis, set `ai_analysis.enabled: false` in `config/config.yaml`, or disable via environment variable `AI_ANALYSIS_ENABLED=false`.

When enabling, configure in the `ai` section of `config/config.yaml`:

```yaml
ai:
  model: "deepseek/deepseek-v4-flash"  # Any model in LiteLLM format
  api_key: "your-api-key"
```

Supported models include DeepSeek, OpenAI, Gemini, Claude, Ollama, etc. See [LiteLLM docs](https://docs.litellm.ai/docs/providers).

### Push Channels (Optional)

Configure desired push channels in the `notification.channels` section of `config/config.yaml`. Supports multi-account (semicolon-separated) and multi-channel parallel delivery.

### Diagnostic Commands

```bash
# Environment health check
uv run python -m trendradar --doctor

# Test notification connectivity
uv run python -m trendradar --test-notification

# Show current schedule status
uv run python -m trendradar --show-schedule
```

---

## Configuration Entry Points

| File | Purpose |
|------|---------|
| `config/config.yaml` | Main config: data sources, report mode, push channels, AI model, storage, scheduling, etc. |
| `config/source_tiers.yaml` | Source tier mapping: platform / RSS feed to A / B / C / D tier assignment |
| `config/frequency_words.txt` | Keywords / topic groups: for keyword matching mode (`filter.method: keyword`) |
| `config/ai_interests.txt` | AI filter interest description: for AI smart filtering mode (`filter.method: ai`) |
| `config/ai_environment_report_prompt.txt` | AI prompt template for Information Environment Anomaly Monitoring Daily Report |
| `config/timeline.yaml` | Scheduling strategy: time period definitions, day plans, week mapping |
| `config/ai_analysis_prompt.txt` | AI prompt template for classic hot-topic analysis style (`report_style: classic`) |

Configuration files contain detailed comments. Visual config editor: https://sansan0.github.io/TrendRadar/

---

## Project Architecture

```
trendradar/
├── __main__.py          # Entry point: NewsAnalyzer orchestrates collect→analyze→push pipeline
├── context.py           # AppContext: dependency injection container, wraps config-related ops
├── core/                # Core logic
│   ├── config.py        #   Config parsing, multi-account management
│   ├── loader.py        #   Config file loading
│   ├── frequency.py     #   Keyword matching
│   ├── analyzer.py      #   Frequency statistics, weight calculation
│   ├── scheduler.py     #   Timeline scheduler
│   ├── source_tiers.py  #   Source tier resolver
│   ├── data.py          #   Data reading & new item detection
│   └── cdn.py           #   CDN multi-source fallback
├── crawler/             # Data collection
│   ├── fetcher.py       #   Hot-list platform crawler
│   └── rss/             #   RSS fetcher & parser
├── storage/             # Storage layer
│   ├── base.py          #   Data models (NewsItem, RSSItem)
│   ├── sqlite_mixin.py  #   SQLite storage mixin
│   ├── local.py         #   Local storage backend
│   ├── remote.py        #   Remote storage backend (S3-compatible)
│   └── manager.py       #   Storage manager
├── ai/                  # AI module
│   ├── analyzer.py      #   AI deep analysis
│   ├── evidence.py      #   Evidence summary construction & programmatic classification
│   ├── filter.py        #   AI smart filtering
│   ├── translator.py    #   AI translation
│   ├── formatter.py     #   AI analysis result formatting
│   └── client.py        #   LiteLLM client
├── report/              # Report generation
│   ├── html.py          #   HTML report rendering
│   ├── generator.py     #   Report generator
│   └── formatter.py     #   Title formatting
├── notification/        # Push notifications
│   ├── dispatcher.py    #   Multi-channel notification dispatcher
│   ├── senders.py       #   Per-channel send functions
│   ├── renderer.py      #   Notification content rendering
│   └── splitter.py      #   Message batch splitting
└── utils/               # Utilities
    ├── time.py          #   Time handling
    └── url.py           #   URL handling

mcp_server/              # MCP Server (FastMCP 2.0)
├── server.py            #   MCP tool server entry point
└── tools/               #   Data query, analytics, search MCP tools
```

---

## Disclaimer

This project outputs **information environment observation results**, not factual conclusions, investment advice, public safety judgments, or legal opinions.

- Source tiers (A / B / C / D) are source classification labels, not factuality judgments
- Verification status is generated by programmatic rules, expressing "source tier distribution characteristics", not "whether an event is real"
- AI-generated text is based on the program-provided evidence summary; AI does not introduce new facts beyond the evidence
- Users should independently assess information reliability and should not use this project's output as the sole basis for decisions

---

## Upstream Acknowledgments & License

This project is based on [sansan0/TrendRadar](https://github.com/sansan0/TrendRadar). Thanks to the original author for their open-source contribution.

Hot-list data source: [newsnow](https://github.com/ourongxing/newsnow) open-source project.

This project is licensed under [GPL-3.0](LICENSE).
