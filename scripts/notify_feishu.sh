#!/bin/zsh
# 把当日 6 张 PNG 推送到飞书私聊(机器人 → 你)
# 由 daily_digest.sh 在所有图卡生成完后调用
# 也可独立运行测试: scripts/notify_feishu.sh [YYYY-MM-DD]
#
# 设计要点:
# 1. 从 output/cards/<日期>/ 读图(项目内目录),不读 Desktop —
#    Desktop 在 macOS TCC 下 launchd 无权访问,sips/curl 会失败
# 2. 每张图独立处理,失败不影响其它,最后报告成功/失败计数
# 3. 上传 + 发送都重试 3 次

cd "$(dirname "$0")/.."
[ -f .env ] && source .env

if [ -z "$FEISHU_APP_ID" ] || [ -z "$FEISHU_APP_SECRET" ] || [ -z "$FEISHU_USER_OPENID" ]; then
  echo "[Feishu] 凭证未配置 (FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_USER_OPENID),跳过"
  exit 0
fi

# 优先从 latest.json 读 date 字段(跟 generate_digest.py 实际写出的日期对齐),
# 防止时区漂移(2026-05-16 修: 主流程用 LA 时区,旧 fallback 写死 Asia/Shanghai 会算成隔天目录)
DATE_FROM_LATEST=$(.venv/bin/python -c "import json,sys; print(json.load(open('output/digest/latest.json'))['date'])" 2>/dev/null)
DATE="${1:-${DATE_FROM_LATEST:-$(TZ=Asia/Shanghai date +%Y-%m-%d)}}"
CARD_DIR="output/cards/$DATE"

if [ ! -d "$CARD_DIR" ]; then
  echo "[Feishu] ❌ 图卡目录不存在: $CARD_DIR — 这是异常,不是正常跳过"
  exit 1
fi

PY=".venv/bin/python"

# ─── 拿 tenant_access_token ─────────────────────────────────
echo "[Feishu] 获取 tenant_access_token..."
TOKEN_RESP=$(curl -fsS -X POST "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal" \
  -H "Content-Type: application/json" \
  -d "{\"app_id\":\"$FEISHU_APP_ID\",\"app_secret\":\"$FEISHU_APP_SECRET\"}")
TOKEN=$(echo "$TOKEN_RESP" | "$PY" -c "
import sys, json
d = json.load(sys.stdin)
if d.get('code') == 0:
    print(d['tenant_access_token'])
" 2>/dev/null)

if [ -z "$TOKEN" ]; then
  echo "[Feishu] ❌ 拿 token 失败: $TOKEN_RESP"
  exit 1
fi

# ─── 发日期分隔条(文字消息)──────────────────────────────
echo "[Feishu] 推送日期分隔条..."
DIVIDER_PAYLOAD="{\"receive_id\":\"$FEISHU_USER_OPENID\",\"msg_type\":\"text\",\"content\":\"{\\\"text\\\":\\\"──── $DATE ────\\\"}\"}"
curl -fsS -X POST "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "$DIVIDER_PAYLOAD" > /dev/null \
  || echo "  ⚠️  分隔条推送失败,继续推图"

# ─── 推 6 张图 ──────────────────────────────────────────────
echo "[Feishu] 推送 6 张图..."
ok=0
fail=0
i=0
for f in \
  "$CARD_DIR/00_cover.png" \
  "$CARD_DIR/01_AI_领域.png" \
  "$CARD_DIR/02_GitHub_开源生态.png" \
  "$CARD_DIR/03_生物医疗工程.png" \
  "$CARD_DIR/04_Hacker_News.png" \
  "$CARD_DIR/05_国际局势.png"
do
  i=$((i + 1))
  if [ ! -f "$f" ]; then
    echo "  [$i/6] ⚠️  缺图: $f"
    fail=$((fail + 1))
    continue
  fi

  name=$(basename "$f")
  size=$(stat -f %z "$f")

  # ── Step A: 上传换 image_key,重试 3 次 ──
  attempt=0
  image_key=""
  last_upload_resp=""
  while [ $attempt -lt 3 ]; do
    attempt=$((attempt + 1))
    last_upload_resp=$(curl -fsS -X POST "https://open.feishu.cn/open-apis/im/v1/images" \
      -H "Authorization: Bearer $TOKEN" \
      -F "image_type=message" \
      -F "image=@$f" 2>&1)
    image_key=$(echo "$last_upload_resp" | "$PY" -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if d.get('code') == 0:
        print(d['data']['image_key'])
except Exception:
    pass
" 2>/dev/null)
    [ -n "$image_key" ] && break
    sleep 1
  done

  if [ -z "$image_key" ]; then
    echo "  [$i/6] ❌ $name 上传失败 (${size}B,重试 $attempt 次)"
    echo "       $last_upload_resp"
    fail=$((fail + 1))
    continue
  fi

  # ── Step B: 发图片消息 ──
  SEND_RESP=$(curl -fsS -X POST "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"receive_id\":\"$FEISHU_USER_OPENID\",\"msg_type\":\"image\",\"content\":\"{\\\"image_key\\\":\\\"$image_key\\\"}\"}" 2>&1)
  send_code=$(echo "$SEND_RESP" | "$PY" -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('code', -1))
except Exception:
    print(-1)
" 2>/dev/null)

  if [ "$send_code" = "0" ]; then
    if [ $attempt -gt 1 ]; then
      echo "  [$i/6] ✅ $name (${size}B,上传重试 $attempt 次)"
    else
      echo "  [$i/6] ✅ $name (${size}B)"
    fi
    ok=$((ok + 1))
  else
    echo "  [$i/6] ❌ $name 发送失败: $SEND_RESP"
    fail=$((fail + 1))
  fi

  # 飞书 API 限流: send_message 100次/分钟,uploadImage 5次/秒
  sleep 0.3
done

if [ $fail -eq 0 ]; then
  echo "[Feishu] 推送完成 ✅ ($ok/6 张)"
  exit 0
else
  echo "[Feishu] 推送部分完成 ⚠️  成功 $ok 张,失败 $fail 张"
  exit 1
fi
