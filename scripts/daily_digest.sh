#!/bin/zsh
# TrendRadar 每日报告生成脚本
# 由 macOS LaunchAgent 每天 17:00 调度一次。
#
# 设计要点:
# 1. (2026-05-06) 每步带超时刹车 — 防止某步卡死把整个任务拖到深夜(5/5 教训:跑了 6 小时)
# 2. (2026-05-06) 任何步骤失败,立即推一条飞书消息 — 不用等第二天看 log 才发现
# 3. (2026-05-06) 不再 set -e — 让失败能走到失败通知逻辑
# 4. (2026-05-07) caffeinate 阻止睡眠 — 防止脚本在 DarkWake 期间被反复冻结
#                 (5/7 教训:Mac 睡眠期间脚本被切片成几秒一段,从 12 分钟拖到 78 分钟)
# 5. (2026-05-07) Python 加 -u 强制行缓冲 + 每步打开始/结束时间戳 — 出问题能看到卡哪步
#
# 日志: output/cron/<日期>.log

cd "$(dirname "$0")/.."

# ─── 阻止 Mac 进入睡眠 ──────────────────────────────────────
# -u: 模拟用户活跃,把 DarkWake 升级为真 Wake(关键!launchd 在 DarkWake 启动时必需)
# -d -i -m -s: 阻止显示/系统/磁盘/AC 模式的所有睡眠
# -w $$: 等本脚本(PID=$$)退出后 caffeinate 自动结束,不会泄露进程
caffeinate -u -t 2 2>/dev/null   # 同步唤醒一下,把 DarkWake 顶成真 Wake
caffeinate -dims -w $$ 2>/dev/null &
CAFFEINATE_PID=$!

# 加载 API key (launchd 不会自动 source .zshrc)
[ -f ~/.zshrc ] && source ~/.zshrc

# 直接用绝对路径调 venv 里的 python
# 不 source .venv/bin/activate — 因为 activate 里硬编码了 venv 创建时的旧路径
# (项目搬家后 activate 会污染 PATH,而 .venv/bin/python symlink 仍然有效)
VENV_PY="$(pwd)/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "[Error] $VENV_PY 不存在或不可执行" >&2
  exit 1
fi

# 加载飞书凭证(失败时推送用)
[ -f .env ] && source .env

LOG_DIR="output/cron"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d).log"

# ─── 幂等检查 ──────────────────────────────────────────────
# 2026-05-12 加: 防止 RunAtLoad 在同一天触发多次重跑
# success flag 由主流程末尾写入,跨调度周期(13:00 触发 + 开机 RunAtLoad 触发)共用一份
SUCCESS_FLAG="$LOG_DIR/.success-$(date +%Y-%m-%d)"
if [ -f "$SUCCESS_FLAG" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [Skip] 今日 $(date +%Y-%m-%d) 已成功跑过,退出" >> "$LOG_FILE"
  exit 0
fi

# ─── 超时执行函数 ──────────────────────────────────────────
# 用法: run_with_timeout SECS CMD ARGS...
# 返回: 0 成功, 124 超时, 其它=命令本身退出码
run_with_timeout() {
  local secs=$1
  shift
  "$@" &
  local pid=$!
  (
    sleep $secs
    if kill -0 $pid 2>/dev/null; then
      echo ""
      echo "[TIMEOUT] 超过 ${secs} 秒,强制终止 PID $pid"
      pkill -TERM -P $pid 2>/dev/null
      kill -TERM $pid 2>/dev/null
      sleep 3
      pkill -KILL -P $pid 2>/dev/null
      kill -KILL $pid 2>/dev/null
    fi
  ) &
  local watchdog=$!
  wait $pid 2>/dev/null
  local rc=$?
  kill $watchdog 2>/dev/null
  wait $watchdog 2>/dev/null
  if [ $rc -eq 137 ] || [ $rc -eq 143 ]; then
    return 124
  fi
  return $rc
}

# ─── 失败通知函数(发到飞书机器人私聊)──────────────────────
push_failure_to_phone() {
  local step="$1"
  local detail="$2"
  if [ -z "$FEISHU_APP_ID" ] || [ -z "$FEISHU_APP_SECRET" ] || [ -z "$FEISHU_USER_OPENID" ]; then
    return
  fi
  local token
  token=$(curl -fsS -X POST "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal" \
    -H "Content-Type: application/json" \
    -d "{\"app_id\":\"$FEISHU_APP_ID\",\"app_secret\":\"$FEISHU_APP_SECRET\"}" 2>/dev/null \
    | "$VENV_PY" -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if d.get('code') == 0: print(d['tenant_access_token'])
except Exception:
    pass
" 2>/dev/null)
  [ -z "$token" ] && return
  local text="❌ TrendRadar 失败 — ${step}
${detail}
日志: output/cron/$(date +%Y-%m-%d).log"
  local payload
  payload=$("$VENV_PY" -c "
import json, sys
print(json.dumps({
    'receive_id': sys.argv[1],
    'msg_type': 'text',
    'content': json.dumps({'text': sys.argv[2]}, ensure_ascii=False)
}, ensure_ascii=False))
" "$FEISHU_USER_OPENID" "$text" 2>/dev/null)
  [ -z "$payload" ] && return
  curl -fsS -X POST "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id" \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json; charset=utf-8" \
    -d "$payload" > /dev/null 2>&1 || true
}

# ─── 主流程 ────────────────────────────────────────────────
{
  echo ""
  echo "==================== $(date '+%Y-%m-%d %H:%M:%S') ===================="
  START_TS=$(date +%s)
  STEP_FAILED=""

  # ─── 每步打时间戳的小工具 ────────────────────────────────
  # 用法: step_time_start  /  step_time_end "[1/4]"
  step_time_start() { STEP_T0=$(date +%s); echo "  ⏱  开始: $(date '+%H:%M:%S')"; }
  step_time_end() {
    local label="$1"
    local t1=$(date +%s)
    local d=$((t1 - STEP_T0))
    local m=$((d / 60))
    local s=$((d % 60))
    echo "  ⏱  $label 结束: $(date '+%H:%M:%S')  耗时 ${m}分${s}秒"
  }

  # ─── [0/4] 同步 B 站 AI 一手访谈源 ────────────────────────
  # 2026-05-12 加: 拉 3 个 B 站账号最新视频 → 本地 RSS XML
  # 失败不阻塞主流程(主管道对 file:// 也容错)
  echo "[0/4] 同步 3 个 B 站访谈源 (web3天空之城 / 张小珺 / 老罗的十字路口) (超时 3 分钟)"
  echo "------------------------------------------------------------"
  step_time_start
  run_with_timeout 180 "$VENV_PY" -u scripts/sync_bilibili.py 2>&1
  rc=$?
  step_time_end "[0/4]"
  if [ $rc -eq 124 ]; then
    echo "  ⚠️  sync_bilibili 超时,跳过(不阻塞)"
  elif [ $rc -ne 0 ]; then
    echo "  ⚠️  sync_bilibili 失败 (退出码 $rc),跳过(不阻塞)"
  fi
  echo ""

  # ─── [1/4] trendradar 抓取 + AI 筛选 + 翻译 ───────────────
  echo "[1/4] trendradar 抓取 + AI 筛选 + 翻译 (超时 25 分钟)"
  echo "------------------------------------------------------------"
  step_time_start
  run_with_timeout 1500 "$VENV_PY" -u -m trendradar 2>&1
  rc=$?
  step_time_end "[1/4]"
  if [ $rc -eq 124 ]; then
    STEP_FAILED="[1/4] trendradar 跑超 25 分钟,被强杀(配置或外部 API 异常)"
  elif [ $rc -ne 0 ]; then
    STEP_FAILED="[1/4] trendradar 失败,退出码=$rc"
  fi

  # ─── [2/4] digest ────────────────────────────────────────
  # 注:generate_digest.py 是一次大 LLM 调用(一次性生成 5 分类 × 10 条 + 摘要,
  # max_tokens=24000),DeepSeek 通常要 5-8 分钟。给 10 分钟才稳。
  if [ -z "$STEP_FAILED" ]; then
    echo ""
    echo "[2/4] 生成 digest 精选报告 (超时 10 分钟)"
    echo "------------------------------------------------------------"
    step_time_start
    run_with_timeout 600 "$VENV_PY" -u generate_digest.py 2>&1
    rc=$?
    step_time_end "[2/4]"
    if [ $rc -eq 124 ]; then
      STEP_FAILED="[2/4] generate_digest 跑超 10 分钟"
    elif [ $rc -ne 0 ]; then
      STEP_FAILED="[2/4] generate_digest 失败,退出码=$rc"
    fi
  fi

  # ─── [3/4] cards ─────────────────────────────────────────
  if [ -z "$STEP_FAILED" ]; then
    echo ""
    echo "[3/4] 生成 6 张图卡(封面 + 5 分类长图) (超时 5 分钟)"
    echo "------------------------------------------------------------"
    step_time_start
    run_with_timeout 300 "$VENV_PY" -u digest_to_card.py 2>&1
    rc=$?
    step_time_end "[3/4]"
    if [ $rc -eq 124 ]; then
      STEP_FAILED="[3/4] digest_to_card 跑超 5 分钟"
    elif [ $rc -ne 0 ]; then
      STEP_FAILED="[3/4] digest_to_card 失败,退出码=$rc"
    fi
  fi

  # ─── [4/4] notify ────────────────────────────────────────
  # 改用飞书私聊推送(2026-05-06 切换):
  # ntfy 脚本仍保留 scripts/notify_ntfy.sh,如需切回直接改这里
  if [ -z "$STEP_FAILED" ]; then
    echo ""
    echo "[4/4] 推送到飞书(机器人 → 你) (超时 5 分钟)"
    echo "------------------------------------------------------------"
    step_time_start
    run_with_timeout 300 scripts/notify_feishu.sh 2>&1
    rc=$?
    step_time_end "[4/4]"
    if [ $rc -eq 124 ]; then
      STEP_FAILED="[4/4] notify_feishu 跑超 5 分钟"
    elif [ $rc -ne 0 ]; then
      STEP_FAILED="[4/4] notify_feishu 部分失败(图没全部到位)"
    fi
  fi

  END_TS=$(date +%s)
  DURATION=$((END_TS - START_TS))
  MIN=$((DURATION / 60))
  SEC=$((DURATION % 60))

  echo ""
  if [ -z "$STEP_FAILED" ]; then
    echo "==================== ✅ 完成 $(date '+%Y-%m-%d %H:%M:%S') (耗时 ${MIN}分${SEC}秒) ===================="
    touch "$SUCCESS_FLAG"
  else
    echo "==================== ❌ 失败 $(date '+%Y-%m-%d %H:%M:%S') (耗时 ${MIN}分${SEC}秒) ===================="
    echo "失败步骤: $STEP_FAILED"
    push_failure_to_phone "${STEP_FAILED%% *}" "$STEP_FAILED"
  fi
} >> "$LOG_FILE" 2>&1
