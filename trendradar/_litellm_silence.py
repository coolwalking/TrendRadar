"""
深度抑制 LiteLLM 后台噪声

LiteLLM 在 Python 3.14 + 同步 completion() 调用下会持续漏 async coroutine
("Task was destroyed but it is pending"),导致主进程退出 hang 或运行变慢。

策略:
  1. import litellm 前用环境变量切到本地价格表 + ERROR 级日志
  2. 清空所有 callback 列表
  3. monkey-patch GLOBAL_LOGGING_WORKER 的关键方法,让 coroutine 主动 close
     (而不是被 LoggingWorker 接收后又因为没事件循环跑而泄漏)
  4. atexit 时不再扫队列(原始 _flush_on_exit 会创建新 event loop)

用法:**任何 import litellm 之前** import 本模块即可。
"""

import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LITELLM_LOG", "ERROR")

import litellm
from litellm.litellm_core_utils import logging_worker as _lw

litellm.success_callback = []
litellm.failure_callback = []
litellm._async_success_callback = []
litellm._async_failure_callback = []
litellm.callbacks = []
litellm.suppress_debug_info = True
litellm.set_verbose = False
litellm.turn_off_message_logging = True


def _close_coroutine_silently(*args, **kwargs):
    """不入队,直接 close 掉 coroutine,避免 Task pending 警告。

    LiteLLM 不同版本调用签名不一:
      - enqueue(coroutine)               位置参数
      - enqueue(async_coroutine=...)     关键字参数
    所以接受任意 args / kwargs,逐个找 coroutine 关掉。
    """
    candidates = list(args) + list(kwargs.values())
    for c in candidates:
        try:
            c.close()
        except (AttributeError, RuntimeError):
            pass


_worker = _lw.GLOBAL_LOGGING_WORKER
_worker.enqueue = _close_coroutine_silently
_worker.ensure_initialized_and_enqueue = _close_coroutine_silently
_worker.start = lambda: None
_worker._ensure_queue = lambda: None
_worker._flush_on_exit = lambda: None
