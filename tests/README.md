# 测试说明（信息环境异常监测改造）

这些测试覆盖"信息环境异常监测 / 舆情雷达"改造涉及的核心逻辑：来源分层、
程序化证据摘要与分栏规则、AI 流程组装、各渠道渲染，以及关键配置/提示词文件。

## 如何运行

在**项目根目录**执行（无需安装第三方依赖，纯标准库即可）：

```bash
python3 -m unittest discover -s tests -v
```

或用 pytest（在装好依赖的 Python 3.12 环境中）：

```bash
pytest tests/
```

> 运行时 stdout 会出现若干 `[AI] 模型: ...` 日志，这是 `AIAnalyzer.analyze` 的正常打印，
> 不影响断言。如需安静输出，可 `... 2>&1 | grep -v '^\[AI\]'`。

## 为什么能在精简解释器下运行

主包 `trendradar` 的 `__init__` 会导入 litellm 等重依赖（项目要求 Python 3.12）。
`tests/_bootstrap.py` 在不触发这些 `__init__` 的前提下，按文件路径单独加载被测的
**纯逻辑模块**，并仅 stub 掉 litellm 客户端（`trendradar.ai.client`）。AI 调用通过
monkeypatch `AIAnalyzer._call_ai` 注入"假响应"，因此测试**不联网、不需要 API Key**。

## 覆盖内容

| 文件 | 覆盖点 |
|------|--------|
| `test_source_tier_resolver.py` | 按显示名/ID 查 tier、unknown 兜底、tier 归一化、空 resolver（缺 source_tiers.yaml）不崩、从 config 构建 |
| `test_evidence_labels.py` | `assign_label` 全规则矩阵与优先级（跨层/中文源独热/高热/情绪/沉默温差/背景）、bucketize、overview_stats、prompt 渲染、固定风险提示常量 |
| `test_analyzer_environment.py` | environment 端到端：程序定栏定标签（AI 改不动）、AI 文字按议题合并、高热强制风险提示、AI 失败/坏 JSON/代码块 JSON 容错、无信号 skipped、缺 resolver 兜底、classic 不受影响 |
| `test_formatter_environment.py` | 6 渠道 + HTML 渲染含标题/栏目/风险提示/方法说明、router、skipped/failed、classic 回归、默认 report_style |
| `test_config_and_prompt_files.py` | 新 prompt 含 `{evidence_summary}`/`{overview_stats}` 且不含 `{news_content}`/`{rss_content}`、角色为监测编辑、source_tiers.yaml 与 config.yaml 关键项 |

## 未覆盖（需在 3.12 + 依赖 + API Key 环境手动验证）

- 真实热榜采集、入库、timeline 调度（主流程，本次未改动）。
- 真实 LiteLLM 调用与真实模型输出质量。
- HTML 报告整页生成与各推送渠道实际投递。

建议在 Python 3.12 环境中按 `计划文件 → 验收 / 本地验证` 章节跑一次端到端。
