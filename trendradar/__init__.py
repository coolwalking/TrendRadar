# coding=utf-8
"""
TrendRadar - 热点新闻聚合与分析工具

使用方式:
  python -m trendradar        # 模块执行
  trendradar                  # 安装后执行
"""

__version__ = "6.7.1"
__all__ = ["AppContext", "__version__"]


def __getattr__(name):
    if name == "AppContext":
        from trendradar.context import AppContext

        return AppContext
    raise AttributeError(f"module 'trendradar' has no attribute {name!r}")
