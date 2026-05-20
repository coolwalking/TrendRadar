#!/bin/zsh
# 把当日 6 张 PNG 推送到 ntfy 手机端
# 由 daily_digest.sh 在所有图卡生成完后调用
# 也可独立运行测试: scripts/notify_ntfy.sh [YYYY-MM-DD]
#
# 设计原则: 每张图独立推送,单张失败不影响其它,最后报告成功/失败计数。
# 不用 set -e — 否则一张 IO 抖动就会把整批拉死(5/4 17:17 教训)。

cd "$(dirname "$0")/.."

# 加载 .env(NTFY_TOPIC)
[ -f .env ] && source .env

if [ -z "$NTFY_TOPIC" ]; then
  echo "[Notify] NTFY_TOPIC 未在 .env 配置,跳过推送"
  exit 0
fi

# 默认用 Asia/Shanghai 算日期,跟 generate_digest.py / trendradar 主程序一致(2026-05-13 修)
DATE="${1:-$(TZ=Asia/Shanghai date +%Y-%m-%d)}"
MONTH="${DATE:0:7}"
DESKTOP_DIR="$HOME/Desktop/trend trender/$MONTH/$DATE"

if [ ! -d "$DESKTOP_DIR" ]; then
  echo "[Notify] 桌面目录不存在: $DESKTOP_DIR — 跳过"
  exit 0
fi

NTFY_URL="https://ntfy.sh/$NTFY_TOPIC"

echo "[Notify] 推送日期分隔条..."
curl -fsS -X POST "$NTFY_URL" \
  -H "Title: ──── $DATE ────" \
  -H "Priority: low" \
  -d "" > /dev/null \
  || echo "  ⚠️  分隔条推送失败(继续推图)"

echo "[Notify] 推送 6 张图..."
# ntfy.sh 公共服务器单文件 ~2MB 上限。超了的 PNG 自动转 JPEG q=60(实测 ~1.7MB)
SIZE_LIMIT=1900000
TMP_DIR=$(mktemp -d /tmp/ntfy_XXXXXX)
trap 'rm -rf "$TMP_DIR"' EXIT

ok=0
fail=0
i=0
for f in \
  "$DESKTOP_DIR/00_cover.png" \
  "$DESKTOP_DIR/01_AI_领域.png" \
  "$DESKTOP_DIR/02_GitHub_开源生态.png" \
  "$DESKTOP_DIR/03_生物医疗工程.png" \
  "$DESKTOP_DIR/04_Hacker_News.png" \
  "$DESKTOP_DIR/05_国际局势.png"
do
  i=$((i + 1))
  if [ ! -f "$f" ]; then
    echo "  [$i/6] ⚠️  缺图: $f"
    fail=$((fail + 1))
    continue
  fi
  size=$(stat -f %z "$f")
  if [ "$size" -gt "$SIZE_LIMIT" ]; then
    base=$(basename "${f%.png}")
    upload="$TMP_DIR/$base.jpg"
    sips_err=$(sips -s format jpeg -s formatOptions 60 "$f" --out "$upload" 2>&1 >/dev/null)
    if [ $? -ne 0 ]; then
      echo "  [$i/6] ❌ JPEG 转换失败: $f"
      echo "       $sips_err"
      fail=$((fail + 1))
      continue
    fi
    name="$base.jpg"
    label="(PNG ${size}B → JPEG)"
  else
    upload="$f"
    name=$(basename "$f")
    label=""
  fi

  # 重试 2 次(每次间隔 1s),应对瞬时 IO/网络抖动
  attempt=0
  uploaded=0
  last_error=""
  while [ $attempt -lt 3 ]; do
    attempt=$((attempt + 1))
    # ntfy 官方推荐 -T 上传方式
    last_error=$(curl -fsS -T "$upload" "$NTFY_URL" \
         -H "Filename: $name" 2>&1 >/dev/null)
    if [ $? -eq 0 ]; then
      uploaded=1
      break
    fi
    sleep 1
  done

  if [ $uploaded -eq 1 ]; then
    if [ $attempt -gt 1 ]; then
      echo "  [$i/6] ✅ $name $label (重试 $attempt 次成功)"
    else
      echo "  [$i/6] ✅ $name $label"
    fi
    ok=$((ok + 1))
  else
    echo "  [$i/6] ❌ $name $label (重试 3 次仍失败)"
    echo "       $last_error"
    fail=$((fail + 1))
  fi

  # ntfy 免费档限流约 5 条/秒,sleep 0.6 防 throttle
  sleep 0.6
done

if [ $fail -eq 0 ]; then
  echo "[Notify] 推送完成 ✅ ($ok/6 张)"
  exit 0
else
  echo "[Notify] 推送部分完成 ⚠️  成功 $ok 张,失败 $fail 张"
  exit 1
fi
