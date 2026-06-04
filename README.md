# TrendRadar

信息环境异常监测与舆情雷达工具

[![License](https://img.shields.io/badge/license-GPL--3.0-blue.svg?style=flat-square)](LICENSE)
[![Version](https://img.shields.io/badge/version-v6.9.0-blue.svg)](https://github.com/carrot-peace/TrendRadar)

**中文** | **[English](README-EN.md)**

---

## 这个项目是什么

TrendRadar 用于监测多平台信息环境中的异常信号。它同时接入中文热榜 / 社交平台和 RSS / 国际媒体 / 官方源，将来源划分为不同证据层级（A / B / C / D），并通过程序规则识别：

- **跨层呼应** — 社交 / 热搜平台与一手或背景源同时出现
- **高热待核实** — D 层平台传播升温，但缺少 A / B / C 来源呼应
- **中文独热** — 中文信息环境内部升温，但缺少 A / B 国际或官方背景源
- **沉默温差** — A / B 背景源有信息，但中文社交平台反应弱
- **已抑制 / 背景项** — 存在但未达到异常阈值的信息

## 这个项目不是什么

- 不是新闻客户端或 RSS 阅读器
- 不是 newsletter 或热点摘要推送工具
- 不是事实核查工具——它不判断真假
- 不是舆情结论生成器——它不输出"事件已发生"
- 不是让 AI 直接判断真假的系统

## 解决什么问题

中文互联网热榜平台（微博、抖音、知乎等）有早期传播价值，但可信度不稳定；国际媒体、官方源、技术社区、财经源可提供背景参照，但与中文社交平台的信息流天然隔离。

TrendRadar 的价值是把这些来源放进同一套观察框架里，看"热度"和"证据层级"是否错位：一个话题在 D 层平台高热，不代表事件已成立；一个话题在 A / B 源有背景信息但中文平台沉默，也是一种值得观察的信号。

---

## 核心机制

### 多源采集

同时抓取两类数据源：

- **热榜平台**：微博、百度、抖音、知乎、bilibili、今日头条、澎湃、华尔街见闻、财联社、凤凰网、贴吧
- **RSS 订阅源**：OpenAI、Anthropic、Google AI 等官方博客；Reuters、AP、BBC、NYT、Washington Post 等国际媒体；Yahoo Finance 等财经源；Hacker News 等技术社区

数据来源基于 [newsnow](https://github.com/ourongxing/newsnow) 开源项目的 API。

### 来源分层

每个数据源被映射到一个证据层级（定义在 `config/source_tiers.yaml`）：

| 层级 | 含义 | 示例 |
|------|------|------|
| **A** | 一手 / 官方来源 | OpenAI 官方博客、Anthropic 研究发布、Google DeepMind Blog |
| **B** | 国际媒体 / 背景源 | Reuters、AP、BBC、NYT、Hacker News、Yahoo Finance |
| **C** | 中文相对严肃信息源 | 澎湃、华尔街见闻、财联社、知乎、百度 |
| **D** | 高时效低可信传播平台 | 微博、抖音、贴吧、bilibili |
| **unknown** | 未配置层级 | — |

**重要：层级是来源标签，不是事实真伪判断。D 层高热只说明传播正在发生，不能说明事件已经成立。**

### 程序化证据归类

程序（`trendradar/ai/evidence.py`）在 AI 介入之前，对每个主题组执行以下步骤：

1. 按来源层级归集该主题命中了哪些 A / B / C / D 来源
2. 计算 D 层热度（平台数量、最高排名）
3. 检测情绪信号（标题中的强情绪词）
4. 根据规则裁定唯一的 `evidence_label`（验证状态）
5. 将主题组分配到唯一栏目（bucket）

**验证状态由程序唯一裁定，AI 不得更改。**

### AI 克制补写

AI（可选，需配置 API Key）的唯一任务是基于程序生成的 evidence summary，为每个已分栏的议题补写克制的日报文字。AI 不负责：

- 引入证据摘要中没有的新事实、新数字、新人物
- 更改验证状态或栏目归属
- 把 D 层传播改写成事实事件
- 输出投资建议、行动指南或趋势预测

### 报告与推送

生成信息环境异常监测日报（HTML 报告），并可推送到：飞书、钉钉、企业微信、Telegram、邮件、Slack、Bark、ntfy、通用 Webhook。

---

## 异常栏目说明

| 栏目 | 验证状态 | 含义 |
|------|----------|------|
| **跨层呼应** | 跨层有呼应 | D 层有热度，且 A / B 背景源有呼应。存在跨层来源呼应，但不代表所有 D 层说法均被证实 |
| **高热待核实** | 高热待核实 | 纯 D 层高热，无 A / B / C 任何呼应。当前仅能确认传播正在发生，不能确认事件已经成立 |
| **中文独热** | 中文源呼应(缺A/B背景) | D 层有热度且有 C 严肃源呼应，但缺 A / B 一手 / 国际背景源。中文信息环境内部升温，不宜直接视为事实性重大事件 |
| **沉默温差** | 沉默温差 | A / B 背景源有信息，但 D 层无热度。背景源有信息，中文社交平台未明显响应 |
| **已抑制** | — | 未达异常阈值的信息（低热、低热情绪聚集等），仅供盘面计数 |

此外，程序还会识别**情绪信号**作为二级属性标注：当标题中出现强情绪词时标记 `sentiment_flag`，但不作为独立栏目。

---

## AI 介入边界

本项目对 AI 的使用有明确边界：

| 由程序负责 | 由 AI 负责 |
|------------|------------|
| 来源分层（A / B / C / D） | 可读化表达 |
| 验证状态裁定 | 克制的 summary / analysis 文字 |
| 栏目归属（分桶） | 盘面概览的一句话解读 |
| 报告骨架与数据统计 | — |
| 热度计算与排名追踪 | — |

AI 不能做的事情：

1. 不引入证据外的新事实、新数字、新结论
2. 不更改验证状态或移动栏目
3. 不把 D 层高热传播写成事实事件
4. 不给投资者 / 品牌方 / 公众行动指南
5. 不做趋势预测，不写宏大叙事
6. sample_titles（代表性传播文本）只是"有人在这样说 / 传"，不是事实来源

---

## 快速开始

### 环境要求

- Python >= 3.12
- 包管理工具：[uv](https://docs.astral.sh/uv/)（推荐）或 pip

### 本地运行

```bash
# 克隆仓库
git clone https://github.com/carrot-peace/TrendRadar.git
cd TrendRadar

# 使用 uv 安装依赖
uv sync

# 编辑配置（见下方"配置入口"）
cp config/config.yaml config/config.yaml.bak
# 编辑 config/config.yaml，配置数据源和推送渠道

# 运行
uv run python -m trendradar
```

### Docker 部署

```bash
cd docker
cp .env.example .env
# 编辑 .env 配置环境变量
docker compose up -d
```

### GitHub Actions 部署

1. Fork 本仓库
2. 在仓库 Settings → Secrets 中配置所需环境变量（推送 Webhook、AI API Key 等）
3. 启用 `.github/workflows/crawler.yml` 中的定时任务

### AI 分析（可选）

AI 分析功能需要配置 API Key。在 `config/config.yaml` 的 `ai` 段配置：

```yaml
ai:
  model: "deepseek/deepseek-v4-flash"  # 支持 LiteLLM 格式的任意模型
  api_key: "your-api-key"
```

支持的模型包括 DeepSeek、OpenAI、Gemini、Claude、Ollama 等，详见 [LiteLLM 文档](https://docs.litellm.ai/docs/providers)。

### 推送渠道（可选）

在 `config/config.yaml` 的 `notification.channels` 段配置需要的推送渠道。支持多账号（分号分隔）和多渠道并行。

### 诊断命令

```bash
# 环境体检
uv run python -m trendradar --doctor

# 测试通知连通性
uv run python -m trendradar --test-notification

# 查看当前调度状态
uv run python -m trendradar --show-schedule
```

---

## 配置入口

| 文件 | 用途 |
|------|------|
| `config/config.yaml` | 主配置：数据源、报告模式、通知渠道、AI 模型、存储、调度等 |
| `config/source_tiers.yaml` | 来源分层：平台 / RSS feed 到 A / B / C / D 层级的映射 |
| `config/frequency_words.txt` | 关键词 / 主题组：用于关键词匹配模式（`filter.method: keyword`） |
| `config/ai_interests.txt` | AI 筛选兴趣描述：用于 AI 智能筛选模式（`filter.method: ai`） |
| `config/ai_environment_report_prompt.txt` | 信息环境异常监测日报的 AI 提示词模板 |
| `config/timeline.yaml` | 调度策略：时间段定义、日计划、周映射 |
| `config/ai_analysis_prompt.txt` | 经典热点分析风格的 AI 提示词模板（`report_style: classic`） |

配置文件内有详细注释。可视化配置编辑器：https://sansan0.github.io/TrendRadar/

---

## 项目架构

```
trendradar/
├── __main__.py          # 主入口：NewsAnalyzer 编排 采集→分析→推送 流程
├── context.py           # AppContext：依赖注入容器，封装配置相关操作
├── core/                # 核心逻辑
│   ├── config.py        #   配置解析、多账号管理
│   ├── loader.py        #   配置文件加载
│   ├── frequency.py     #   关键词匹配
│   ├── analyzer.py      #   频率统计、权重计算
│   ├── scheduler.py     #   时间线调度器
│   ├── source_tiers.py  #   来源分层解析器
│   ├── data.py          #   数据读取与新增检测
│   └── cdn.py           #   CDN 多源回退
├── crawler/             # 数据抓取
│   ├── fetcher.py       #   热榜平台爬虫
│   └── rss/             #   RSS 抓取器与解析器
├── storage/             # 存储层
│   ├── base.py          #   数据模型（NewsItem、RSSItem）
│   ├── sqlite_mixin.py  #   SQLite 存储混入
│   ├── local.py         #   本地存储后端
│   ├── remote.py        #   远程存储后端（S3 兼容）
│   └── manager.py       #   存储管理器
├── ai/                  # AI 模块
│   ├── analyzer.py      #   AI 深度分析
│   ├── evidence.py      #   证据摘要构建与程序化分栏
│   ├── filter.py        #   AI 智能筛选
│   ├── translator.py    #   AI 翻译
│   ├── formatter.py     #   AI 分析结果格式化
│   └── client.py        #   LiteLLM 客户端
├── report/              # 报告生成
│   ├── html.py          #   HTML 报告渲染
│   ├── generator.py     #   报告生成器
│   └── formatter.py     #   标题格式化
├── notification/        # 推送通知
│   ├── dispatcher.py    #   多渠道通知调度器
│   ├── senders.py       #   各渠道发送函数
│   ├── renderer.py      #   通知内容渲染
│   └── splitter.py      #   消息分批拆分
└── utils/               # 工具函数
    ├── time.py          #   时间处理
    └── url.py           #   URL 处理

mcp_server/              # MCP Server（FastMCP 2.0）
├── server.py            #   MCP 工具服务器入口
└── tools/               #   数据查询、分析、搜索等 MCP 工具
```

---

## 免责声明

本项目输出的是**信息环境观察结果**，不是事实结论、投资建议、公共安全判断或法律意见。

- 来源层级（A / B / C / D）是来源分类标签，不是事实真伪判断
- 验证状态由程序规则生成，表达的是"来源层级分布特征"，不是"事件是否属实"
- AI 生成的文字基于程序提供的证据摘要，AI 不引入证据外的新事实
- 使用者应自行判断信息的可靠性，不应将本项目的输出作为决策的唯一依据

---

## 上游致谢与 License

本项目基于 [sansan0/TrendRadar](https://github.com/sansan0/TrendRadar) 改造，感谢原作者的开源贡献。

热榜数据来源：[newsnow](https://github.com/ourongxing/newsnow) 开源项目。

本项目使用 [GPL-3.0](LICENSE) 许可证。
