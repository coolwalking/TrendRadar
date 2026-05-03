"""
高级数据分析工具

提供热度趋势分析、平台对比、关键词共现、情感分析等高级分析功能。
"""

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from difflib import SequenceMatcher

from ..services.data_service import DataService
from ..utils.validators import (
    validate_platforms,
    validate_limit,
    validate_keyword,
    validate_top_n,
    validate_date_range
)
from ..utils.errors import MCPError, InvalidParameterError, DataNotFoundError
from trendradar.logging_config import get_logger


logger = get_logger(__name__)


def calculate_news_weight(news_data: Dict, rank_threshold: int = 5) -> float:
    """
    计算新闻权重（用于排序）

    基于 main.py 的权重算法实现，综合考虑：
    - 排名权重 (60%)：新闻在榜单中的排名
    - 频次权重 (30%)：新闻出现的次数
    - 热度权重 (10%)：高排名出现的比例

    Args:
        news_data: 新闻数据字典，包含 ranks 和 count 字段
        rank_threshold: 高排名阈值，默认5

    Returns:
        权重分数（0-100之间的浮点数）
    """
    ranks = news_data.get("ranks", [])
    if not ranks:
        return 0.0

    count = news_data.get("count", len(ranks))

    # 权重配置（与 config.yaml 保持一致）
    RANK_WEIGHT = 0.6
    FREQUENCY_WEIGHT = 0.3
    HOTNESS_WEIGHT = 0.1

    # 1. 排名权重：Σ(11 - min(rank, 10)) / 出现次数
    rank_scores = []
    for rank in ranks:
        score = 11 - min(rank, 10)
        rank_scores.append(score)

    rank_weight = sum(rank_scores) / len(ranks) if ranks else 0

    # 2. 频次权重：min(出现次数, 10) × 10
    frequency_weight = min(count, 10) * 10

    # 3. 热度加成：高排名次数 / 总出现次数 × 100
    high_rank_count = sum(1 for rank in ranks if rank <= rank_threshold)
    hotness_ratio = high_rank_count / len(ranks) if ranks else 0
    hotness_weight = hotness_ratio * 100

    # 综合权重
    total_weight = (
        rank_weight * RANK_WEIGHT
        + frequency_weight * FREQUENCY_WEIGHT
        + hotness_weight * HOTNESS_WEIGHT
    )

    return total_weight


class AnalyticsTools:
    """高级数据分析工具类"""

    def __init__(self, project_root: Optional[str] = None):
        """
        初始化分析工具

        Args:
            project_root: 项目根目录
        """
        self.data_service = DataService(project_root)

    def analyze_data_insights_unified(
        self,
        insight_type: str = "platform_compare",
        topic: Optional[str] = None,
        date_range: Optional[Dict[str, str]] = None,
        min_frequency: int = 3,
        top_n: int = 20
    ) -> Dict:
        """
        统一数据洞察分析工具 - 整合多种数据分析模式

        Args:
            insight_type: 洞察类型，可选值：
                - "platform_compare": 平台对比分析（对比不同平台对话题的关注度）
                - "platform_activity": 平台活跃度统计（统计各平台发布频率和活跃时间）
                - "keyword_cooccur": 关键词共现分析（分析关键词同时出现的模式）
            topic: 话题关键词（可选，platform_compare模式适用）
            date_range: 日期范围，格式: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
            min_frequency: 最小共现频次（keyword_cooccur模式），默认3
            top_n: 返回TOP N结果（keyword_cooccur模式），默认20

        Returns:
            数据洞察分析结果字典

        Examples:
            - analyze_data_insights_unified(insight_type="platform_compare", topic="人工智能")
            - analyze_data_insights_unified(insight_type="platform_activity", date_range={...})
            - analyze_data_insights_unified(insight_type="keyword_cooccur", min_frequency=5)
        """
        try:
            # 参数验证
            if insight_type not in ["platform_compare", "platform_activity", "keyword_cooccur"]:
                raise InvalidParameterError(
                    f"无效的洞察类型: {insight_type}",
                    suggestion="支持的类型: platform_compare, platform_activity, keyword_cooccur"
                )

            # 根据洞察类型调用相应方法
            if insight_type == "platform_compare":
                return self.compare_platforms(
                    topic=topic,
                    date_range=date_range
                )
            elif insight_type == "platform_activity":
                return self.get_platform_activity_stats(
                    date_range=date_range
                )
            else:  # keyword_cooccur
                return self.analyze_keyword_cooccurrence(
                    min_frequency=min_frequency,
                    top_n=top_n
                )

        except MCPError as e:
            return {
                "success": False,
                "error": e.to_dict()
            }
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e)
                }
            }

    def analyze_topic_trend_unified(
        self,
        topic: str,
        analysis_type: str = "trend",
        date_range: Optional[Dict[str, str]] = None,
        granularity: str = "day",
        threshold: float = 3.0,
        time_window: int = 24,
        lookahead_hours: int = 6,
        confidence_threshold: float = 0.7
    ) -> Dict:
        """
        统一话题趋势分析工具 - 整合多种趋势分析模式

        Args:
            topic: 话题关键词（必需）
            analysis_type: 分析类型，可选值：
                - "trend": 热度趋势分析（追踪话题的热度变化）
                - "lifecycle": 生命周期分析（从出现到消失的完整周期）
                - "viral": 异常热度检测（识别突然爆火的话题）
                - "predict": 话题预测（预测未来可能的热点）
            date_range: 日期范围（trend和lifecycle模式），可选
                       - **格式**: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
                       - **默认**: 不指定时默认分析最近7天
            granularity: 时间粒度（trend模式），默认"day"（hour/day）
            threshold: 热度突增倍数阈值（viral模式），默认3.0
            time_window: 检测时间窗口小时数（viral模式），默认24
            lookahead_hours: 预测未来小时数（predict模式），默认6
            confidence_threshold: 置信度阈值（predict模式），默认0.7

        Returns:
            趋势分析结果字典

        Examples (假设今天是 2025-11-17):
            - 用户："分析AI最近7天的趋势" → analyze_topic_trend_unified(topic="人工智能", analysis_type="trend", date_range={"start": "2025-11-11", "end": "2025-11-17"})
            - 用户："看看特斯拉本月的热度" → analyze_topic_trend_unified(topic="特斯拉", analysis_type="lifecycle", date_range={"start": "2025-11-01", "end": "2025-11-17"})
            - analyze_topic_trend_unified(topic="比特币", analysis_type="viral", threshold=3.0)
            - analyze_topic_trend_unified(topic="ChatGPT", analysis_type="predict", lookahead_hours=6)
        """
        try:
            # 参数验证
            topic = validate_keyword(topic)

            if analysis_type not in ["trend", "lifecycle", "viral", "predict"]:
                raise InvalidParameterError(
                    f"无效的分析类型: {analysis_type}",
                    suggestion="支持的类型: trend, lifecycle, viral, predict"
                )

            # 根据分析类型调用相应方法
            if analysis_type == "trend":
                return self.get_topic_trend_analysis(
                    topic=topic,
                    date_range=date_range,
                    granularity=granularity
                )
            elif analysis_type == "lifecycle":
                return self.analyze_topic_lifecycle(
                    topic=topic,
                    date_range=date_range
                )
            elif analysis_type == "viral":
                # viral模式不需要topic参数，使用通用检测
                return self.detect_viral_topics(
                    threshold=threshold,
                    time_window=time_window
                )
            else:  # predict
                # predict模式不需要topic参数，使用通用预测
                return self.predict_trending_topics(
                    lookahead_hours=lookahead_hours,
                    confidence_threshold=confidence_threshold
                )

        except MCPError as e:
            return {
                "success": False,
                "error": e.to_dict()
            }
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e)
                }
            }

    def get_topic_trend_analysis(
        self,
        topic: str,
        date_range: Optional[Dict[str, str]] = None,
        granularity: str = "day"
    ) -> Dict:
        """
        热度趋势分析 - 追踪特定话题的热度变化趋势

        Args:
            topic: 话题关键词
            date_range: 日期范围（可选）
                       - **格式**: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
                       - **默认**: 不指定时默认分析最近7天
            granularity: 时间粒度，仅支持 day（天）

        Returns:
            趋势分析结果字典

        Examples:
            用户询问示例：
            - "帮我分析一下'人工智能'这个话题最近一周的热度趋势"
            - "查看'比特币'过去一周的热度变化"
            - "看看'iPhone'最近7天的趋势如何"
            - "分析'特斯拉'最近一个月的热度趋势"
            - "查看'ChatGPT'2024年12月的趋势变化"

            代码调用示例：
            >>> tools = AnalyticsTools()
            >>> # 分析7天趋势（假设今天是 2025-11-17）
            >>> result = tools.get_topic_trend_analysis(
            ...     topic="人工智能",
            ...     date_range={"start": "2025-11-11", "end": "2025-11-17"},
            ...     granularity="day"
            ... )
            >>> # 分析历史月份趋势
            >>> result = tools.get_topic_trend_analysis(
            ...     topic="特斯拉",
            ...     date_range={"start": "2024-12-01", "end": "2024-12-31"},
            ...     granularity="day"
            ... )
            >>> logger.info(result['trend_data'])
        """
        try:
            # 验证参数
            topic = validate_keyword(topic)

            # 验证粒度参数（只支持day）
            if granularity != "day":
                from ..utils.errors import InvalidParameterError
                raise InvalidParameterError(
                    f"不支持的粒度参数: {granularity}",
                    suggestion="当前仅支持 'day' 粒度，因为底层数据按天聚合"
                )

            # 处理日期范围（不指定时默认最近7天）
            if date_range:
                from ..utils.validators import validate_date_range
                date_range_tuple = validate_date_range(date_range)
                start_date, end_date = date_range_tuple
            else:
                # 默认最近7天
                end_date = datetime.now()
                start_date = end_date - timedelta(days=6)

            # 收集趋势数据
            trend_data = []
            current_date = start_date

            while current_date <= end_date:
                try:
                    all_titles, _, _ = self.data_service.parser.read_all_titles_for_date(
                        date=current_date
                    )

                    # 统计该时间点的话题出现次数
                    count = 0
                    matched_titles = []

                    for _, titles in all_titles.items():
                        for title in titles.keys():
                            if topic.lower() in title.lower():
                                count += 1
                                matched_titles.append(title)

                    trend_data.append({
                        "date": current_date.strftime("%Y-%m-%d"),
                        "count": count,
                        "sample_titles": matched_titles[:3]  # 只保留前3个样本
                    })

                except DataNotFoundError:
                    trend_data.append({
                        "date": current_date.strftime("%Y-%m-%d"),
                        "count": 0,
                        "sample_titles": []
                    })

                # 按天增加时间
                current_date += timedelta(days=1)

            # 计算趋势指标
            counts = [item["count"] for item in trend_data]
            total_days = (end_date - start_date).days + 1

            if len(counts) >= 2:
                # 计算涨跌幅度
                first_non_zero = next((c for c in counts if c > 0), 0)
                last_count = counts[-1]

                if first_non_zero > 0:
                    change_rate = ((last_count - first_non_zero) / first_non_zero) * 100
                else:
                    change_rate = 0

                # 找到峰值时间
                max_count = max(counts)
                peak_index = counts.index(max_count)
                peak_time = trend_data[peak_index]["date"]
            else:
                change_rate = 0
                peak_time = None
                max_count = 0

            return {
                "success": True,
                "topic": topic,
                "date_range": {
                    "start": start_date.strftime("%Y-%m-%d"),
                    "end": end_date.strftime("%Y-%m-%d"),
                    "total_days": total_days
                },
                "granularity": granularity,
                "trend_data": trend_data,
                "statistics": {
                    "total_mentions": sum(counts),
                    "average_mentions": round(sum(counts) / len(counts), 2) if counts else 0,
                    "peak_count": max_count,
                    "peak_time": peak_time,
                    "change_rate": round(change_rate, 2)
                },
                "trend_direction": "上升" if change_rate > 10 else "下降" if change_rate < -10 else "稳定"
            }

        except MCPError as e:
            return {
                "success": False,
                "error": e.to_dict()
            }
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e)
                }
            }

    def compare_platforms(
        self,
        topic: Optional[str] = None,
        date_range: Optional[Dict[str, str]] = None
    ) -> Dict:
        """
        平台对比分析 - 对比不同平台对同一话题的关注度

        Args:
            topic: 话题关键词（可选，不指定则对比整体活跃度）
            date_range: 日期范围，格式: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}

        Returns:
            平台对比分析结果

        Examples:
            用户询问示例：
            - "对比一下各个平台对'人工智能'话题的关注度"
            - "看看知乎和微博哪个平台更关注科技新闻"
            - "分析各平台今天的热点分布"

            代码调用示例：
            >>> # 对比各平台（假设今天是 2025-11-17）
            >>> result = tools.compare_platforms(
            ...     topic="人工智能",
            ...     date_range={"start": "2025-11-08", "end": "2025-11-17"}
            ... )
            >>> logger.info(result['platform_stats'])
        """
        try:
            # 参数验证
            if topic:
                topic = validate_keyword(topic)
            date_range_tuple = validate_date_range(date_range)

            # 确定日期范围
            if date_range_tuple:
                start_date, end_date = date_range_tuple
            else:
                start_date = end_date = datetime.now()

            # 收集各平台数据
            platform_stats: Dict[str, Any] = defaultdict(lambda: {
                "total_news": 0,
                "topic_mentions": 0,
                "unique_titles": set(),
                "top_keywords": Counter()
            })

            # 遍历日期范围
            current_date = start_date
            while current_date <= end_date:
                try:
                    all_titles, id_to_name, _ = self.data_service.parser.read_all_titles_for_date(
                        date=current_date
                    )

                    for platform_id, titles in all_titles.items():
                        platform_name = id_to_name.get(platform_id, platform_id)

                        for title in titles.keys():
                            platform_stats[platform_name]["total_news"] += 1
                            platform_stats[platform_name]["unique_titles"].add(title)

                            # 如果指定了话题，统计包含话题的新闻
                            if topic and topic.lower() in title.lower():
                                platform_stats[platform_name]["topic_mentions"] += 1

                            # 提取关键词（简单分词）
                            keywords = self._extract_keywords(title)
                            platform_stats[platform_name]["top_keywords"].update(keywords)

                except DataNotFoundError:
                    pass

                current_date += timedelta(days=1)

            # 转换为可序列化的格式
            result_stats = {}
            for platform, stats in platform_stats.items():
                coverage_rate = 0
                if stats["total_news"] > 0:
                    coverage_rate = (stats["topic_mentions"] / stats["total_news"]) * 100

                result_stats[platform] = {
                    "total_news": stats["total_news"],
                    "topic_mentions": stats["topic_mentions"],
                    "unique_titles": len(stats["unique_titles"]),
                    "coverage_rate": round(coverage_rate, 2),
                    "top_keywords": [
                        {"keyword": k, "count": v}
                        for k, v in stats["top_keywords"].most_common(5)
                    ]
                }

            # 找出各平台独有的热点
            unique_topics = self._find_unique_topics(platform_stats)

            return {
                "success": True,
                "topic": topic,
                "date_range": {
                    "start": start_date.strftime("%Y-%m-%d"),
                    "end": end_date.strftime("%Y-%m-%d")
                },
                "platform_stats": result_stats,
                "unique_topics": unique_topics,
                "total_platforms": len(result_stats)
            }

        except MCPError as e:
            return {
                "success": False,
                "error": e.to_dict()
            }
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e)
                }
            }

    def analyze_keyword_cooccurrence(
        self,
        min_frequency: int = 3,
        top_n: int = 20
    ) -> Dict:
        """
        关键词共现分析 - 分析哪些关键词经常同时出现

        Args:
            min_frequency: 最小共现频次
            top_n: 返回TOP N关键词对

        Returns:
            关键词共现分析结果

        Examples:
            用户询问示例：
            - "分析一下哪些关键词经常一起出现"
            - "看看'人工智能'经常和哪些词一起出现"
            - "找出今天新闻中的关键词关联"

            代码调用示例：
            >>> tools = AnalyticsTools()
            >>> result = tools.analyze_keyword_cooccurrence(
            ...     min_frequency=5,
            ...     top_n=15
            ... )
            >>> logger.info(result['cooccurrence_pairs'])
        """
        try:
            # 参数验证
            min_frequency = validate_limit(min_frequency, default=3, max_limit=100)
            top_n = validate_top_n(top_n, default=20)

            # 读取今天的数据
            all_titles, _, _ = self.data_service.parser.read_all_titles_for_date()

            # 关键词共现统计
            cooccurrence: Counter[Tuple[str, ...]] = Counter()
            keyword_titles: Dict[str, List[str]] = defaultdict(list)

            for platform_id, titles in all_titles.items():
                for title in titles.keys():
                    # 提取关键词
                    keywords = self._extract_keywords(title)

                    # 记录每个关键词出现的标题
                    for kw in keywords:
                        keyword_titles[kw].append(title)

                    # 计算两两共现
                    if len(keywords) >= 2:
                        for i, kw1 in enumerate(keywords):
                            for kw2 in keywords[i+1:]:
                                # 统一排序，避免重复
                                pair = tuple(sorted([kw1, kw2]))
                                cooccurrence[pair] += 1

            # 过滤低频共现
            filtered_pairs = [
                (pair, count) for pair, count in cooccurrence.items()
                if count >= min_frequency
            ]

            # 排序并取TOP N
            top_pairs = sorted(filtered_pairs, key=lambda x: x[1], reverse=True)[:top_n]

            # 构建结果
            result_pairs = []
            for (kw1, kw2), count in top_pairs:
                # 找出同时包含两个关键词的标题样本
                titles_with_both = [
                    title for title in keyword_titles[kw1]
                    if kw2 in self._extract_keywords(title)
                ]

                result_pairs.append({
                    "keyword1": kw1,
                    "keyword2": kw2,
                    "cooccurrence_count": count,
                    "sample_titles": titles_with_both[:3]
                })

            return {
                "success": True,
                "cooccurrence_pairs": result_pairs,
                "total_pairs": len(result_pairs),
                "min_frequency": min_frequency,
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

        except MCPError as e:
            return {
                "success": False,
                "error": e.to_dict()
            }
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e)
                }
            }

    def analyze_sentiment(
        self,
        topic: Optional[str] = None,
        platforms: Optional[List[str]] = None,
        date_range: Optional[Dict[str, str]] = None,
        limit: int = 50,
        sort_by_weight: bool = True,
        include_url: bool = False
    ) -> Dict:
        """
        情感倾向分析 - 生成用于 AI 情感分析的结构化提示词

        本工具收集新闻数据并生成优化的 AI 提示词，你可以将其发送给 AI 进行深度情感分析。

        Args:
            topic: 话题关键词（可选），只分析包含该关键词的新闻
            platforms: 平台过滤列表（可选），如 ['zhihu', 'weibo']
            date_range: 日期范围（可选），格式: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
                       不指定则默认查询今天的数据
            limit: 返回新闻数量限制，默认50，最大100
            sort_by_weight: 是否按权重排序，默认True（推荐）
            include_url: 是否包含URL链接，默认False（节省token）

        Returns:
            包含 AI 提示词和新闻数据的结构化结果

        Examples:
            用户询问示例：
            - "分析一下今天新闻的情感倾向"
            - "看看'特斯拉'相关新闻是正面还是负面的"
            - "分析各平台对'人工智能'的情感态度"
            - "看看'特斯拉'相关新闻是正面还是负面的，请选择一周内的前10条新闻来分析"

            代码调用示例：
            >>> tools = AnalyticsTools()
            >>> # 分析今天的特斯拉新闻，返回前10条
            >>> result = tools.analyze_sentiment(
            ...     topic="特斯拉",
            ...     limit=10
            ... )
            >>> # 分析一周内的特斯拉新闻（假设今天是 2025-11-17）
            >>> result = tools.analyze_sentiment(
            ...     topic="特斯拉",
            ...     date_range={"start": "2025-11-11", "end": "2025-11-17"},
            ...     limit=10
            ... )
            >>> logger.info(result['ai_prompt'])  # 获取生成的提示词
        """
        try:
            # 参数验证
            if topic:
                topic = validate_keyword(topic)
            platforms = validate_platforms(platforms)
            limit = validate_limit(limit, default=50)

            # 处理日期范围
            if date_range:
                date_range_tuple = validate_date_range(date_range)
                start_date, end_date = date_range_tuple
            else:
                # 默认今天
                start_date = end_date = datetime.now()

            # 收集新闻数据（支持多天）
            all_news_items = []
            current_date = start_date

            while current_date <= end_date:
                try:
                    all_titles, id_to_name, _ = self.data_service.parser.read_all_titles_for_date(
                        date=current_date,
                        platform_ids=platforms
                    )

                    # 收集该日期的新闻
                    for platform_id, titles in all_titles.items():
                        platform_name = id_to_name.get(platform_id, platform_id)
                        for title, info in titles.items():
                            # 如果指定了话题，只收集包含话题的标题
                            if topic and topic.lower() not in title.lower():
                                continue

                            news_item = {
                                "platform": platform_name,
                                "title": title,
                                "ranks": info.get("ranks", []),
                                "count": len(info.get("ranks", [])),
                                "date": current_date.strftime("%Y-%m-%d")
                            }

                            # 条件性添加 URL 字段
                            if include_url:
                                news_item["url"] = info.get("url", "")
                                news_item["mobileUrl"] = info.get("mobileUrl", "")

                            all_news_items.append(news_item)

                except DataNotFoundError:
                    # 该日期没有数据，继续下一天
                    pass

                # 下一天
                current_date += timedelta(days=1)

            if not all_news_items:
                time_desc = "今天" if start_date == end_date else f"{start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}"
                raise DataNotFoundError(
                    f"未找到相关新闻（{time_desc}）",
                    suggestion="请尝试其他话题、日期范围或平台"
                )

            # 去重（同一标题只保留一次）
            unique_news = {}
            for item in all_news_items:
                key = f"{item['platform']}::{item['title']}"
                if key not in unique_news:
                    unique_news[key] = item
                else:
                    # 合并 ranks（如果同一新闻在多天出现）
                    existing = unique_news[key]
                    existing["ranks"].extend(item["ranks"])
                    existing["count"] = len(existing["ranks"])

            deduplicated_news = list(unique_news.values())

            # 按权重排序（如果启用）
            if sort_by_weight:
                deduplicated_news.sort(
                    key=lambda x: calculate_news_weight(x),
                    reverse=True
                )

            # 限制返回数量
            selected_news = deduplicated_news[:limit]

            # 生成 AI 提示词
            ai_prompt = self._create_sentiment_analysis_prompt(
                news_data=selected_news,
                topic=topic
            )

            # 构建时间范围描述
            if start_date == end_date:
                time_range_desc = start_date.strftime("%Y-%m-%d")
            else:
                time_range_desc = f"{start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}"

            result = {
                "success": True,
                "method": "ai_prompt_generation",
                "summary": {
                    "total_found": len(deduplicated_news),
                    "returned_count": len(selected_news),
                    "requested_limit": limit,
                    "duplicates_removed": len(all_news_items) - len(deduplicated_news),
                    "topic": topic,
                    "time_range": time_range_desc,
                    "platforms": list(set(item["platform"] for item in selected_news)),
                    "sorted_by_weight": sort_by_weight
                },
                "ai_prompt": ai_prompt,
                "news_sample": selected_news,
                "usage_note": "请将 ai_prompt 字段的内容发送给 AI 进行情感分析"
            }

            # 如果返回数量少于请求数量，增加提示
            if len(selected_news) < limit and len(deduplicated_news) >= limit:
                result["note"] = "返回数量少于请求数量是因为去重逻辑（同一标题在不同平台只保留一次）"
            elif len(deduplicated_news) < limit:
                result["note"] = f"在指定时间范围内仅找到 {len(deduplicated_news)} 条匹配的新闻"

            return result

        except MCPError as e:
            return {
                "success": False,
                "error": e.to_dict()
            }
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e)
                }
            }

    def _create_sentiment_analysis_prompt(
        self,
        news_data: List[Dict],
        topic: Optional[str]
    ) -> str:
        """
        创建情感分析的 AI 提示词

        Args:
            news_data: 新闻数据列表（已排序和限制数量）
            topic: 话题关键词

        Returns:
            格式化的 AI 提示词
        """
        # 按平台分组
        platform_news = defaultdict(list)
        for item in news_data:
            platform_news[item["platform"]].append({
                "title": item["title"],
                "date": item.get("date", "")
            })

        # 构建提示词
        prompt_parts = []

        # 1. 任务说明
        if topic:
            prompt_parts.append(f"请分析以下关于「{topic}」的新闻标题的情感倾向。")
        else:
            prompt_parts.append("请分析以下新闻标题的情感倾向。")

        prompt_parts.append("")
        prompt_parts.append("分析要求：")
        prompt_parts.append("1. 识别每条新闻的情感倾向（正面/负面/中性）")
        prompt_parts.append("2. 统计各情感类别的数量和百分比")
        prompt_parts.append("3. 分析不同平台的情感差异")
        prompt_parts.append("4. 总结整体情感趋势")
        prompt_parts.append("5. 列举典型的正面和负面新闻样本")
        prompt_parts.append("")

        # 2. 数据概览
        prompt_parts.append(f"数据概览：")
        prompt_parts.append(f"- 总新闻数：{len(news_data)}")
        prompt_parts.append(f"- 覆盖平台：{len(platform_news)}")

        # 时间范围
        dates = set(item.get("date", "") for item in news_data if item.get("date"))
        if dates:
            date_list = sorted(dates)
            if len(date_list) == 1:
                prompt_parts.append(f"- 时间范围：{date_list[0]}")
            else:
                prompt_parts.append(f"- 时间范围：{date_list[0]} 至 {date_list[-1]}")

        prompt_parts.append("")

        # 3. 按平台展示新闻
        prompt_parts.append("新闻列表（按平台分类，已按重要性排序）：")
        prompt_parts.append("")

        for platform, items in sorted(platform_news.items()):
            prompt_parts.append(f"【{platform}】({len(items)} 条)")
            for i, item in enumerate(items, 1):
                title = item["title"]
                date_str = f" [{item['date']}]" if item.get("date") else ""
                prompt_parts.append(f"{i}. {title}{date_str}")
            prompt_parts.append("")

        # 4. 输出格式说明
        prompt_parts.append("请按以下格式输出分析结果：")
        prompt_parts.append("")
        prompt_parts.append("## 情感分布统计")
        prompt_parts.append("- 正面：XX条 (XX%)")
        prompt_parts.append("- 负面：XX条 (XX%)")
        prompt_parts.append("- 中性：XX条 (XX%)")
        prompt_parts.append("")
        prompt_parts.append("## 平台情感对比")
        prompt_parts.append("[各平台的情感倾向差异]")
        prompt_parts.append("")
        prompt_parts.append("## 整体情感趋势")
        prompt_parts.append("[总体分析和关键发现]")
        prompt_parts.append("")
        prompt_parts.append("## 典型样本")
        prompt_parts.append("正面新闻样本：")
        prompt_parts.append("[列举3-5条]")
        prompt_parts.append("")
        prompt_parts.append("负面新闻样本：")
        prompt_parts.append("[列举3-5条]")

        return "\n".join(prompt_parts)

    def find_similar_news(
        self,
        reference_title: str,
        threshold: float = 0.6,
        limit: int = 50,
        include_url: bool = False
    ) -> Dict:
        """
        相似新闻查找 - 基于标题相似度查找相关新闻

        Args:
            reference_title: 参考标题
            threshold: 相似度阈值（0-1之间）
            limit: 返回条数限制，默认50
            include_url: 是否包含URL链接，默认False（节省token）

        Returns:
            相似新闻列表

        Examples:
            用户询问示例：
            - "找出和'特斯拉降价'相似的新闻"
            - "查找关于iPhone发布的类似报道"
            - "看看有没有和这条新闻相似的报道"

            代码调用示例：
            >>> tools = AnalyticsTools()
            >>> result = tools.find_similar_news(
            ...     reference_title="特斯拉宣布降价",
            ...     threshold=0.6,
            ...     limit=10
            ... )
            >>> logger.info(result['similar_news'])
        """
        try:
            # 参数验证
            reference_title = validate_keyword(reference_title)

            if not 0 <= threshold <= 1:
                raise InvalidParameterError(
                    "threshold 必须在 0 到 1 之间",
                    suggestion="推荐值：0.5-0.8"
                )

            limit = validate_limit(limit, default=50)

            # 读取数据
            all_titles, id_to_name, _ = self.data_service.parser.read_all_titles_for_date()

            # 计算相似度
            similar_items = []

            for platform_id, titles in all_titles.items():
                platform_name = id_to_name.get(platform_id, platform_id)

                for title, info in titles.items():
                    if title == reference_title:
                        continue

                    # 计算相似度
                    similarity = self._calculate_similarity(reference_title, title)

                    if similarity >= threshold:
                        news_item = {
                            "title": title,
                            "platform": platform_id,
                            "platform_name": platform_name,
                            "similarity": round(similarity, 3),
                            "rank": info["ranks"][0] if info["ranks"] else 0
                        }

                        # 条件性添加 URL 字段
                        if include_url:
                            news_item["url"] = info.get("url", "")

                        similar_items.append(news_item)

            # 按相似度排序
            similar_items.sort(key=lambda x: x["similarity"], reverse=True)

            # 限制数量
            result_items = similar_items[:limit]

            if not result_items:
                raise DataNotFoundError(
                    f"未找到相似度超过 {threshold} 的新闻",
                    suggestion="请降低相似度阈值或尝试其他标题"
                )

            result = {
                "success": True,
                "summary": {
                    "total_found": len(similar_items),
                    "returned_count": len(result_items),
                    "requested_limit": limit,
                    "threshold": threshold,
                    "reference_title": reference_title
                },
                "similar_news": result_items
            }

            if len(similar_items) < limit:
                result["note"] = f"相似度阈值 {threshold} 下仅找到 {len(similar_items)} 条相似新闻"

            return result

        except MCPError as e:
            return {
                "success": False,
                "error": e.to_dict()
            }
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e)
                }
            }

    def search_by_entity(
        self,
        entity: str,
        entity_type: Optional[str] = None,
        limit: int = 50,
        sort_by_weight: bool = True
    ) -> Dict:
        """
        实体识别搜索 - 搜索包含特定人物/地点/机构的新闻

        Args:
            entity: 实体名称
            entity_type: 实体类型（person/location/organization），可选
            limit: 返回条数限制，默认50，最大200
            sort_by_weight: 是否按权重排序，默认True

        Returns:
            实体相关新闻列表

        Examples:
            用户询问示例：
            - "搜索马斯克相关的新闻"
            - "查找关于特斯拉公司的报道，返回前20条"
            - "看看北京有什么新闻"

            代码调用示例：
            >>> tools = AnalyticsTools()
            >>> result = tools.search_by_entity(
            ...     entity="马斯克",
            ...     entity_type="person",
            ...     limit=20
            ... )
            >>> logger.info(result['related_news'])
        """
        try:
            # 参数验证
            entity = validate_keyword(entity)
            limit = validate_limit(limit, default=50)

            if entity_type and entity_type not in ["person", "location", "organization"]:
                raise InvalidParameterError(
                    f"无效的实体类型: {entity_type}",
                    suggestion="支持的类型: person, location, organization"
                )

            # 读取数据
            all_titles, id_to_name, _ = self.data_service.parser.read_all_titles_for_date()

            # 搜索包含实体的新闻
            related_news: List[Dict] = []
            entity_context: Counter[str] = Counter()  # 统计实体周边的词

            for platform_id, titles in all_titles.items():
                platform_name = id_to_name.get(platform_id, platform_id)

                for title, info in titles.items():
                    if entity in title:
                        url = info.get("url", "")
                        mobile_url = info.get("mobileUrl", "")
                        ranks = info.get("ranks", [])
                        count = len(ranks)

                        related_news.append({
                            "title": title,
                            "platform": platform_id,
                            "platform_name": platform_name,
                            "url": url,
                            "mobileUrl": mobile_url,
                            "ranks": ranks,
                            "count": count,
                            "rank": ranks[0] if ranks else 999
                        })

                        # 提取实体周边的关键词
                        keywords = self._extract_keywords(title)
                        entity_context.update(keywords)

            if not related_news:
                raise DataNotFoundError(
                    f"未找到包含实体 '{entity}' 的新闻",
                    suggestion="请尝试其他实体名称"
                )

            # 移除实体本身
            if entity in entity_context:
                del entity_context[entity]

            # 按权重排序（如果启用）
            if sort_by_weight:
                related_news.sort(
                    key=lambda x: calculate_news_weight(x),
                    reverse=True
                )
            else:
                # 按排名排序
                related_news.sort(key=lambda x: x["rank"])

            # 限制返回数量
            result_news = related_news[:limit]

            return {
                "success": True,
                "entity": entity,
                "entity_type": entity_type or "auto",
                "related_news": result_news,
                "total_found": len(related_news),
                "returned_count": len(result_news),
                "sorted_by_weight": sort_by_weight,
                "related_keywords": [
                    {"keyword": k, "count": v}
                    for k, v in entity_context.most_common(10)
                ]
            }

        except MCPError as e:
            return {
                "success": False,
                "error": e.to_dict()
            }
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e)
                }
            }

    def generate_summary_report(
        self,
        report_type: str = "daily",
        date_range: Optional[Dict[str, str]] = None
    ) -> Dict:
        """
        每日/每周摘要生成器 - 自动生成热点摘要报告

        Args:
            report_type: 报告类型（daily/weekly）
            date_range: 自定义日期范围（可选）

        Returns:
            Markdown格式的摘要报告

        Examples:
            用户询问示例：
            - "生成今天的新闻摘要报告"
            - "给我一份本周的热点总结"
            - "生成过去7天的新闻分析报告"

            代码调用示例：
            >>> tools = AnalyticsTools()
            >>> result = tools.generate_summary_report(
            ...     report_type="daily"
            ... )
            >>> logger.info(result['markdown_report'])
        """
        try:
            # 参数验证
            if report_type not in ["daily", "weekly"]:
                raise InvalidParameterError(
                    f"无效的报告类型: {report_type}",
                    suggestion="支持的类型: daily, weekly"
                )

            # 确定日期范围
            if date_range:
                date_range_tuple = validate_date_range(date_range)
                start_date, end_date = date_range_tuple
            else:
                if report_type == "daily":
                    start_date = end_date = datetime.now()
                else:  # weekly
                    end_date = datetime.now()
                    start_date = end_date - timedelta(days=6)

            # 收集数据
            all_keywords: Counter[str] = Counter()
            all_platforms_news: Dict[str, int] = defaultdict(int)
            all_titles_list: List[Dict] = []

            current_date = start_date
            while current_date <= end_date:
                try:
                    all_titles, id_to_name, _ = self.data_service.parser.read_all_titles_for_date(
                        date=current_date
                    )

                    for platform_id, titles in all_titles.items():
                        platform_name = id_to_name.get(platform_id, platform_id)
                        all_platforms_news[platform_name] += len(titles)

                        for title in titles.keys():
                            all_titles_list.append({
                                "title": title,
                                "platform": platform_name,
                                "date": current_date.strftime("%Y-%m-%d")
                            })

                            # 提取关键词
                            keywords = self._extract_keywords(title)
                            all_keywords.update(keywords)

                except DataNotFoundError:
                    pass

                current_date += timedelta(days=1)

            # 生成报告
            report_title = f"{'每日' if report_type == 'daily' else '每周'}新闻热点摘要"
            date_str = f"{start_date.strftime('%Y-%m-%d')}" if report_type == "daily" else f"{start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}"

            # 构建Markdown报告
            markdown = f"""# {report_title}

**报告日期**: {date_str}
**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## 📊 数据概览

- **总新闻数**: {len(all_titles_list)}
- **覆盖平台**: {len(all_platforms_news)}
- **热门关键词数**: {len(all_keywords)}

## 🔥 TOP 10 热门话题

"""

            # 添加TOP 10关键词
            for i, (keyword, count) in enumerate(all_keywords.most_common(10), 1):
                markdown += f"{i}. **{keyword}** - 出现 {count} 次\n"

            # 平台分析
            markdown += "\n## 📱 平台活跃度\n\n"
            sorted_platforms = sorted(all_platforms_news.items(), key=lambda x: x[1], reverse=True)

            for platform, count in sorted_platforms:
                markdown += f"- **{platform}**: {count} 条新闻\n"

            # 趋势变化（如果是周报）
            if report_type == "weekly":
                markdown += "\n## 📈 趋势分析\n\n"
                markdown += "本周热度持续的话题（样本数据）：\n\n"

                # 简单的趋势分析
                top_keywords = [kw for kw, _ in all_keywords.most_common(5)]
                for keyword in top_keywords:
                    markdown += f"- **{keyword}**: 持续热门\n"

            # 添加样本新闻（按权重选择，确保确定性）
            markdown += "\n## 📰 精选新闻样本\n\n"

            # 确定性选取：按标题的权重排序，取前5条
            # 这样相同输入总是返回相同结果
            if all_titles_list:
                # 计算每条新闻的权重分数（基于关键词出现次数）
                news_with_scores = []
                for news in all_titles_list:
                    # 简单权重：统计包含TOP关键词的次数
                    score = 0
                    title_lower = news['title'].lower()
                    for keyword, count in all_keywords.most_common(10):
                        if keyword.lower() in title_lower:
                            score += count
                    news_with_scores.append((news, score))

                # 按权重降序排序，权重相同则按标题字母顺序（确保确定性）
                news_with_scores.sort(key=lambda x: (-x[1], x[0]['title']))

                # 取前5条
                sample_news = [item[0] for item in news_with_scores[:5]]

                for news in sample_news:
                    markdown += f"- [{news['platform']}] {news['title']}\n"

            markdown += "\n---\n\n*本报告由 TrendRadar MCP 自动生成*\n"

            return {
                "success": True,
                "report_type": report_type,
                "date_range": {
                    "start": start_date.strftime("%Y-%m-%d"),
                    "end": end_date.strftime("%Y-%m-%d")
                },
                "markdown_report": markdown,
                "statistics": {
                    "total_news": len(all_titles_list),
                    "platforms_count": len(all_platforms_news),
                    "keywords_count": len(all_keywords),
                    "top_keyword": all_keywords.most_common(1)[0] if all_keywords else None
                }
            }

        except MCPError as e:
            return {
                "success": False,
                "error": e.to_dict()
            }
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e)
                }
            }

    def get_platform_activity_stats(
        self,
        date_range: Optional[Dict[str, str]] = None
    ) -> Dict:
        """
        平台活跃度统计 - 统计各平台的发布频率和活跃时间段

        Args:
            date_range: 日期范围（可选）

        Returns:
            平台活跃度统计结果

        Examples:
            用户询问示例：
            - "统计各平台今天的活跃度"
            - "看看哪个平台更新最频繁"
            - "分析各平台的发布时间规律"

            代码调用示例：
            >>> # 查看各平台活跃度（假设今天是 2025-11-17）
            >>> result = tools.get_platform_activity_stats(
            ...     date_range={"start": "2025-11-08", "end": "2025-11-17"}
            ... )
            >>> logger.info(result['platform_activity'])
        """
        try:
            # 参数验证
            date_range_tuple = validate_date_range(date_range)

            # 确定日期范围
            if date_range_tuple:
                start_date, end_date = date_range_tuple
            else:
                start_date = end_date = datetime.now()

            # 统计各平台活跃度
            platform_activity: Dict[str, Any] = defaultdict(lambda: {
                "total_updates": 0,
                "days_active": set(),
                "news_count": 0,
                "hourly_distribution": Counter()
            })

            # 遍历日期范围
            current_date = start_date
            while current_date <= end_date:
                try:
                    all_titles, id_to_name, timestamps = self.data_service.parser.read_all_titles_for_date(
                        date=current_date
                    )

                    for platform_id, titles in all_titles.items():
                        platform_name = id_to_name.get(platform_id, platform_id)

                        platform_activity[platform_name]["news_count"] += len(titles)
                        platform_activity[platform_name]["days_active"].add(current_date.strftime("%Y-%m-%d"))

                        # 统计更新次数（基于文件数量）
                        platform_activity[platform_name]["total_updates"] += len(timestamps)

                        # 统计时间分布（基于文件名中的时间）
                        for filename in timestamps.keys():
                            # 解析文件名中的小时（格式：HHMM.txt）
                            match = re.match(r'(\d{2})(\d{2})\.txt', filename)
                            if match:
                                hour = int(match.group(1))
                                platform_activity[platform_name]["hourly_distribution"][hour] += 1

                except DataNotFoundError:
                    pass

                current_date += timedelta(days=1)

            # 转换为可序列化的格式
            result_activity = {}
            for platform, stats in platform_activity.items():
                days_count = len(stats["days_active"])
                avg_news_per_day = stats["news_count"] / days_count if days_count > 0 else 0

                # 找出最活跃的时间段
                most_active_hours = stats["hourly_distribution"].most_common(3)

                result_activity[platform] = {
                    "total_updates": stats["total_updates"],
                    "news_count": stats["news_count"],
                    "days_active": days_count,
                    "avg_news_per_day": round(avg_news_per_day, 2),
                    "most_active_hours": [
                        {"hour": f"{hour:02d}:00", "count": count}
                        for hour, count in most_active_hours
                    ],
                    "activity_score": round(stats["news_count"] / max(days_count, 1), 2)
                }

            # 按活跃度排序
            sorted_platforms = sorted(
                result_activity.items(),
                key=lambda x: x[1]["activity_score"],
                reverse=True
            )

            return {
                "success": True,
                "date_range": {
                    "start": start_date.strftime("%Y-%m-%d"),
                    "end": end_date.strftime("%Y-%m-%d")
                },
                "platform_activity": dict(sorted_platforms),
                "most_active_platform": sorted_platforms[0][0] if sorted_platforms else None,
                "total_platforms": len(result_activity)
            }

        except MCPError as e:
            return {
                "success": False,
                "error": e.to_dict()
            }
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e)
                }
            }

    def analyze_topic_lifecycle(
        self,
        topic: str,
        date_range: Optional[Dict[str, str]] = None
    ) -> Dict:
        """
        话题生命周期分析 - 追踪话题从出现到消失的完整周期

        Args:
            topic: 话题关键词
            date_range: 日期范围（可选）
                       - **格式**: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
                       - **默认**: 不指定时默认分析最近7天

        Returns:
            话题生命周期分析结果

        Examples:
            用户询问示例：
            - "分析'人工智能'这个话题的生命周期"
            - "看看'iPhone'话题是昙花一现还是持续热点"
            - "追踪'比特币'话题的热度变化"

            代码调用示例：
            >>> # 分析话题生命周期（假设今天是 2025-11-17）
            >>> result = tools.analyze_topic_lifecycle(
            ...     topic="人工智能",
            ...     date_range={"start": "2025-10-19", "end": "2025-11-17"}
            ... )
            >>> logger.info(result['lifecycle_stage'])
        """
        try:
            # 参数验证
            topic = validate_keyword(topic)

            # 处理日期范围（不指定时默认最近7天）
            if date_range:
                from ..utils.validators import validate_date_range
                date_range_tuple = validate_date_range(date_range)
                start_date, end_date = date_range_tuple
            else:
                # 默认最近7天
                end_date = datetime.now()
                start_date = end_date - timedelta(days=6)

            # 收集话题历史数据
            lifecycle_data = []
            current_date = start_date
            while current_date <= end_date:
                try:
                    all_titles, _, _ = self.data_service.parser.read_all_titles_for_date(
                        date=current_date
                    )

                    # 统计该日的话题出现次数
                    count = 0
                    for _, titles in all_titles.items():
                        for title in titles.keys():
                            if topic.lower() in title.lower():
                                count += 1

                    lifecycle_data.append({
                        "date": current_date.strftime("%Y-%m-%d"),
                        "count": count
                    })

                except DataNotFoundError:
                    lifecycle_data.append({
                        "date": current_date.strftime("%Y-%m-%d"),
                        "count": 0
                    })

                current_date += timedelta(days=1)

            # 计算分析天数
            total_days = (end_date - start_date).days + 1

            # 分析生命周期阶段
            counts = [item["count"] for item in lifecycle_data]

            if not any(counts):
                time_desc = f"{start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}"
                raise DataNotFoundError(
                    f"在 {time_desc} 内未找到话题 '{topic}'",
                    suggestion="请尝试其他话题或扩大时间范围"
                )

            # 找到首次出现和最后出现
            first_appearance = next((item["date"] for item in lifecycle_data if item["count"] > 0), None)
            last_appearance = next((item["date"] for item in reversed(lifecycle_data) if item["count"] > 0), None)

            # 计算峰值
            max_count = max(counts)
            peak_index = counts.index(max_count)
            peak_date = lifecycle_data[peak_index]["date"]

            # 计算平均值和标准差（简单实现）
            non_zero_counts = [c for c in counts if c > 0]
            avg_count = sum(non_zero_counts) / len(non_zero_counts) if non_zero_counts else 0

            # 判断生命周期阶段
            recent_counts = counts[-3:]  # 最近3天
            early_counts = counts[:3]    # 前3天

            if sum(recent_counts) > sum(early_counts):
                lifecycle_stage = "上升期"
            elif sum(recent_counts) < sum(early_counts) * 0.5:
                lifecycle_stage = "衰退期"
            elif max_count in recent_counts:
                lifecycle_stage = "爆发期"
            else:
                lifecycle_stage = "稳定期"

            # 分类：昙花一现 vs 持续热点
            active_days = sum(1 for c in counts if c > 0)

            if active_days <= 2 and max_count > avg_count * 2:
                topic_type = "昙花一现"
            elif active_days >= total_days * 0.6:
                topic_type = "持续热点"
            else:
                topic_type = "周期性热点"

            return {
                "success": True,
                "topic": topic,
                "date_range": {
                    "start": start_date.strftime("%Y-%m-%d"),
                    "end": end_date.strftime("%Y-%m-%d"),
                    "total_days": total_days
                },
                "lifecycle_data": lifecycle_data,
                "analysis": {
                    "first_appearance": first_appearance,
                    "last_appearance": last_appearance,
                    "peak_date": peak_date,
                    "peak_count": max_count,
                    "active_days": active_days,
                    "avg_daily_mentions": round(avg_count, 2),
                    "lifecycle_stage": lifecycle_stage,
                    "topic_type": topic_type
                }
            }

        except MCPError as e:
            return {
                "success": False,
                "error": e.to_dict()
            }
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e)
                }
            }

    def detect_viral_topics(
        self,
        threshold: float = 3.0,
        time_window: int = 24
    ) -> Dict:
        """
        异常热度检测 - 自动识别突然爆火的话题

        Args:
            threshold: 热度突增倍数阈值
            time_window: 检测时间窗口（小时）

        Returns:
            爆火话题列表

        Examples:
            用户询问示例：
            - "检测今天有哪些突然爆火的话题"
            - "看看有没有热度异常的新闻"
            - "预警可能的重大事件"

            代码调用示例：
            >>> tools = AnalyticsTools()
            >>> result = tools.detect_viral_topics(
            ...     threshold=3.0,
            ...     time_window=24
            ... )
            >>> logger.info(result['viral_topics'])
        """
        try:
            # 参数验证
            if threshold < 1.0:
                raise InvalidParameterError(
                    "threshold 必须大于等于 1.0",
                    suggestion="推荐值：2.0-5.0"
                )

            time_window = validate_limit(time_window, default=24, max_limit=72)

            # 读取当前和之前的数据
            current_all_titles, _, _ = self.data_service.parser.read_all_titles_for_date()

            # 读取昨天的数据作为基准
            yesterday = datetime.now() - timedelta(days=1)
            try:
                previous_all_titles, _, _ = self.data_service.parser.read_all_titles_for_date(
                    date=yesterday
                )
            except DataNotFoundError:
                previous_all_titles = {}

            # 统计当前的关键词频率
            current_keywords: Counter[str] = Counter()
            current_keyword_titles: Dict[str, List[str]] = defaultdict(list)

            for _, titles in current_all_titles.items():
                for title in titles.keys():
                    keywords = self._extract_keywords(title)
                    current_keywords.update(keywords)

                    for kw in keywords:
                        current_keyword_titles[kw].append(title)

            # 统计之前的关键词频率
            previous_keywords: Counter[str] = Counter()

            for _, titles in previous_all_titles.items():
                for title in titles.keys():
                    keywords = self._extract_keywords(title)
                    previous_keywords.update(keywords)

            # 检测异常热度
            viral_topics = []

            for keyword, current_count in current_keywords.items():
                previous_count = previous_keywords.get(keyword, 0)

                # 计算增长倍数
                if previous_count == 0:
                    # 新出现的话题
                    if current_count >= 5:  # 至少出现5次才认为是爆火
                        growth_rate = float('inf')
                        is_viral = True
                    else:
                        continue
                else:
                    growth_rate = current_count / previous_count
                    is_viral = growth_rate >= threshold

                if is_viral:
                    viral_topics.append({
                        "keyword": keyword,
                        "current_count": current_count,
                        "previous_count": previous_count,
                        "growth_rate": round(growth_rate, 2) if growth_rate != float('inf') else "新话题",
                        "sample_titles": current_keyword_titles[keyword][:3],
                        "alert_level": "高" if growth_rate > threshold * 2 else "中"
                    })

            # 按增长率排序
            viral_topics.sort(
                key=lambda x: x["current_count"] if x["growth_rate"] == "新话题" else x["growth_rate"],
                reverse=True
            )

            if not viral_topics:
                return {
                    "success": True,
                    "viral_topics": [],
                    "total_detected": 0,
                    "message": f"未检测到热度增长超过 {threshold} 倍的话题"
                }

            return {
                "success": True,
                "viral_topics": viral_topics,
                "total_detected": len(viral_topics),
                "threshold": threshold,
                "time_window": time_window,
                "detection_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

        except MCPError as e:
            return {
                "success": False,
                "error": e.to_dict()
            }
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e)
                }
            }

    def predict_trending_topics(
        self,
        lookahead_hours: int = 6,
        confidence_threshold: float = 0.7
    ) -> Dict:
        """
        话题预测 - 基于历史数据预测未来可能的热点

        Args:
            lookahead_hours: 预测未来多少小时
            confidence_threshold: 置信度阈值

        Returns:
            预测的潜力话题列表

        Examples:
            用户询问示例：
            - "预测接下来6小时可能的热点话题"
            - "有哪些话题可能会火起来"
            - "早期发现潜力话题"

            代码调用示例：
            >>> tools = AnalyticsTools()
            >>> result = tools.predict_trending_topics(
            ...     lookahead_hours=6,
            ...     confidence_threshold=0.7
            ... )
            >>> logger.info(result['predicted_topics'])
        """
        try:
            # 参数验证
            lookahead_hours = validate_limit(lookahead_hours, default=6, max_limit=48)

            if not 0 <= confidence_threshold <= 1:
                raise InvalidParameterError(
                    "confidence_threshold 必须在 0 到 1 之间",
                    suggestion="推荐值：0.6-0.8"
                )

            # 收集最近3天的数据用于预测
            keyword_trends = defaultdict(list)

            for days_ago in range(3, 0, -1):
                date = datetime.now() - timedelta(days=days_ago)

                try:
                    all_titles, _, _ = self.data_service.parser.read_all_titles_for_date(
                        date=date
                    )

                    # 统计关键词
                    keywords_count: Counter[str] = Counter()
                    for _, titles in all_titles.items():
                        for title in titles.keys():
                            keywords = self._extract_keywords(title)
                            keywords_count.update(keywords)

                    # 记录每个关键词的历史数据
                    for keyword, count in keywords_count.items():
                        keyword_trends[keyword].append(count)

                except DataNotFoundError:
                    pass

            # 添加今天的数据
            try:
                all_titles, _, _ = self.data_service.parser.read_all_titles_for_date()

                keywords_count = Counter()
                keyword_titles = defaultdict(list)

                for _, titles in all_titles.items():
                    for title in titles.keys():
                        keywords = self._extract_keywords(title)
                        keywords_count.update(keywords)

                        for kw in keywords:
                            keyword_titles[kw].append(title)

                for keyword, count in keywords_count.items():
                    keyword_trends[keyword].append(count)

            except DataNotFoundError:
                raise DataNotFoundError(
                    "未找到今天的数据",
                    suggestion="请等待爬虫任务完成"
                )

            # 预测潜力话题
            predicted_topics = []

            for keyword, trend_data in keyword_trends.items():
                if len(trend_data) < 2:
                    continue

                # 简单的线性趋势预测
                # 计算增长率
                recent_value = trend_data[-1]
                previous_value = trend_data[-2] if len(trend_data) >= 2 else 0

                if previous_value == 0:
                    if recent_value >= 3:
                        growth_rate = 1.0
                    else:
                        continue
                else:
                    growth_rate = (recent_value - previous_value) / previous_value

                # 判断是否是上升趋势
                if growth_rate > 0.3:  # 增长超过30%
                    # 计算置信度（基于趋势的稳定性）
                    if len(trend_data) >= 3:
                        # 检查是否连续增长
                        is_consistent = all(
                            trend_data[i] <= trend_data[i+1]
                            for i in range(len(trend_data)-1)
                        )
                        confidence = 0.9 if is_consistent else 0.7
                    else:
                        confidence = 0.6

                    if confidence >= confidence_threshold:
                        predicted_topics.append({
                            "keyword": keyword,
                            "current_count": recent_value,
                            "growth_rate": round(growth_rate * 100, 2),
                            "confidence": round(confidence, 2),
                            "trend_data": trend_data,
                            "prediction": "上升趋势，可能成为热点",
                            "sample_titles": keyword_titles.get(keyword, [])[:3]
                        })

            # 按置信度和增长率排序
            predicted_topics.sort(
                key=lambda x: (x["confidence"], x["growth_rate"]),
                reverse=True
            )

            return {
                "success": True,
                "predicted_topics": predicted_topics[:20],  # 返回TOP 20
                "total_predicted": len(predicted_topics),
                "lookahead_hours": lookahead_hours,
                "confidence_threshold": confidence_threshold,
                "prediction_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "note": "预测基于历史趋势，实际结果可能有偏差"
            }

        except MCPError as e:
            return {
                "success": False,
                "error": e.to_dict()
            }
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e)
                }
            }

    # ==================== 辅助方法 ====================

    def _extract_keywords(self, title: str, min_length: int = 2) -> List[str]:
        """
        从标题中提取关键词（简单实现）

        Args:
            title: 标题文本
            min_length: 最小关键词长度

        Returns:
            关键词列表
        """
        # 移除URL和特殊字符
        title = re.sub(r'http[s]?://\S+', '', title)
        title = re.sub(r'[^\w\s]', ' ', title)

        # 简单分词（按空格和常见分隔符）
        words = re.split(r'[\s，。！？、]+', title)

        # 过滤停用词和短词
        stopwords = {'的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一', '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有', '看', '好', '自己', '这'}

        keywords = [
            word.strip() for word in words
            if word.strip() and len(word.strip()) >= min_length and word.strip() not in stopwords
        ]

        return keywords

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """
        计算两个文本的相似度

        Args:
            text1: 文本1
            text2: 文本2

        Returns:
            相似度分数（0-1之间）
        """
        # 使用 SequenceMatcher 计算相似度
        return SequenceMatcher(None, text1, text2).ratio()

    def _find_unique_topics(self, platform_stats: Dict) -> Dict[str, List[str]]:
        """
        找出各平台独有的热点话题

        Args:
            platform_stats: 平台统计数据

        Returns:
            各平台独有话题字典
        """
        unique_topics = {}

        # 获取每个平台的TOP关键词
        platform_keywords = {}
        for platform, stats in platform_stats.items():
            top_keywords = set([kw for kw, _ in stats["top_keywords"].most_common(10)])
            platform_keywords[platform] = top_keywords

        # 找出独有关键词
        for platform, keywords in platform_keywords.items():
            # 找出其他平台的所有关键词
            other_keywords = set()
            for other_platform, other_kws in platform_keywords.items():
                if other_platform != platform:
                    other_keywords.update(other_kws)

            # 找出独有的
            unique = keywords - other_keywords
            if unique:
                unique_topics[platform] = list(unique)[:5]  # 最多5个

        return unique_topics