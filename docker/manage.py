#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新闻爬虫容器管理工具 - supercronic
"""

import os
import sys
import subprocess
import time
import signal
from pathlib import Path
from trendradar.logging_config import get_logger

# Web 服务器配置
WEBSERVER_PORT = int(os.environ.get("WEBSERVER_PORT", "8080"))
WEBSERVER_DIR = "/app/output"
WEBSERVER_PID_FILE = "/tmp/webserver.pid"


logger = get_logger(__name__)
def run_command(cmd, shell=True, capture_output=True):
    """执行系统命令"""
    try:
        result = subprocess.run(
            cmd, shell=shell, capture_output=capture_output, text=True
        )
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)


def manual_run():
    """手动执行一次爬虫"""
    logger.info("🔄 手动执行爬虫...")
    try:
        result = subprocess.run(
            ["python", "main.py"], cwd="/app", capture_output=False, text=True
        )
        if result.returncode == 0:
            logger.info("✅ 执行完成")
        else:
            logger.error(f"❌ 执行失败，退出码: {result.returncode}")
    except Exception as e:
        logger.error(f"❌ 执行出错: {e}")


def parse_cron_schedule(cron_expr):
    """解析cron表达式并返回人类可读的描述"""
    if not cron_expr or cron_expr == "未设置":
        return "未设置"
    
    try:
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            return f"原始表达式: {cron_expr}"
        
        minute, hour, day, month, weekday = parts
        
        # 分析分钟
        if minute == "*":
            minute_desc = "每分钟"
        elif minute.startswith("*/"):
            interval = minute[2:]
            minute_desc = f"每{interval}分钟"
        elif "," in minute:
            minute_desc = f"在第{minute}分钟"
        else:
            minute_desc = f"在第{minute}分钟"
        
        # 分析小时
        if hour == "*":
            hour_desc = "每小时"
        elif hour.startswith("*/"):
            interval = hour[2:]
            hour_desc = f"每{interval}小时"
        elif "," in hour:
            hour_desc = f"在{hour}点"
        else:
            hour_desc = f"在{hour}点"
        
        # 分析日期
        if day == "*":
            day_desc = "每天"
        elif day.startswith("*/"):
            interval = day[2:]
            day_desc = f"每{interval}天"
        else:
            day_desc = f"每月{day}号"
        
        # 分析月份
        if month == "*":
            month_desc = "每月"
        else:
            month_desc = f"在{month}月"
        
        # 分析星期
        weekday_names = {
            "0": "周日", "1": "周一", "2": "周二", "3": "周三", 
            "4": "周四", "5": "周五", "6": "周六", "7": "周日"
        }
        if weekday == "*":
            weekday_desc = ""
        else:
            weekday_desc = f"在{weekday_names.get(weekday, weekday)}"
        
        # 组合描述
        if minute.startswith("*/") and hour == "*" and day == "*" and month == "*" and weekday == "*":
            # 简单的间隔模式，如 */30 * * * *
            return f"每{minute[2:]}分钟执行一次"
        elif hour != "*" and minute != "*" and day == "*" and month == "*" and weekday == "*":
            # 每天特定时间，如 0 9 * * *
            return f"每天{hour}:{minute.zfill(2)}执行"
        elif weekday != "*" and day == "*":
            # 每周特定时间
            return f"{weekday_desc}{hour}:{minute.zfill(2)}执行"
        else:
            # 复杂模式，显示详细信息
            desc_parts = [part for part in [month_desc, day_desc, weekday_desc, hour_desc, minute_desc] if part and part != "每月" and part != "每天" and part != "每小时"]
            if desc_parts:
                return " ".join(desc_parts) + "执行"
            else:
                return f"复杂表达式: {cron_expr}"
    
    except Exception as e:
        return f"解析失败: {cron_expr}"


def show_status():
    """显示容器状态"""
    logger.info("📊 容器状态:")

    # 检查 PID 1 状态
    supercronic_is_pid1 = False
    pid1_cmdline = ""
    try:
        with open('/proc/1/cmdline', 'r') as f:
            pid1_cmdline = f.read().replace('\x00', ' ').strip()
        logger.info(f"  🔍 PID 1 进程: {pid1_cmdline}")
        
        if "supercronic" in pid1_cmdline.lower():
            logger.info("  ✅ supercronic 正确运行为 PID 1")
            supercronic_is_pid1 = True
        else:
            logger.error("  ❌ PID 1 不是 supercronic")
            logger.info(f"  📋 实际的 PID 1: {pid1_cmdline}")
    except Exception as e:
        logger.error(f"  ❌ 无法读取 PID 1 信息: {e}")

    # 检查环境变量
    cron_schedule = os.environ.get("CRON_SCHEDULE", "未设置")
    run_mode = os.environ.get("RUN_MODE", "未设置")
    immediate_run = os.environ.get("IMMEDIATE_RUN", "未设置")
    
    logger.info(f"  ⚙️ 运行配置:")
    logger.info(f"    CRON_SCHEDULE: {cron_schedule}")
    
    # 解析并显示cron表达式的含义
    cron_description = parse_cron_schedule(cron_schedule)
    logger.info(f"    ⏰ 执行频率: {cron_description}")
    
    logger.info(f"    RUN_MODE: {run_mode}")
    logger.info(f"    IMMEDIATE_RUN: {immediate_run}")

    # 检查配置文件
    config_files = ["/app/config/config.yaml", "/app/config/frequency_words.txt"]
    logger.info("  📁 配置文件:")
    for file_path in config_files:
        if Path(file_path).exists():
            logger.info(f"    ✅ {Path(file_path).name}")
        else:
            logger.error(f"    ❌ {Path(file_path).name} 缺失")

    # 检查关键文件
    key_files = [
        ("/usr/local/bin/supercronic-linux-amd64", "supercronic二进制文件"),
        ("/usr/local/bin/supercronic", "supercronic软链接"),
        ("/tmp/crontab", "crontab文件"),
        ("/entrypoint.sh", "启动脚本")
    ]
    
    logger.info("  📂 关键文件检查:")
    for file_path, description in key_files:
        if Path(file_path).exists():
            logger.info(f"    ✅ {description}: 存在")
            # 对于crontab文件，显示内容
            if file_path == "/tmp/crontab":
                try:
                    with open(file_path, 'r') as f:
                        crontab_content = f.read().strip()
                        logger.info(f"         内容: {crontab_content}")
                except:
                    pass
        else:
            logger.error(f"    ❌ {description}: 不存在")

    # 检查容器运行时间
    logger.info("  ⏱️ 容器时间信息:")
    try:
        # 检查 PID 1 的启动时间
        with open('/proc/1/stat', 'r') as f:
            stat_content = f.read().strip().split()
            if len(stat_content) >= 22:
                # starttime 是第22个字段（索引21）
                starttime_ticks = int(stat_content[21])
                
                # 读取系统启动时间
                with open('/proc/stat', 'r') as stat_f:
                    for line in stat_f:
                        if line.startswith('btime'):
                            boot_time = int(line.split()[1])
                            break
                    else:
                        boot_time = 0
                
                # 读取系统时钟频率
                clock_ticks = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
                
                if boot_time > 0:
                    pid1_start_time = boot_time + (starttime_ticks / clock_ticks)
                    current_time = time.time()
                    uptime_seconds = int(current_time - pid1_start_time)
                    uptime_minutes = uptime_seconds // 60
                    uptime_hours = uptime_minutes // 60
                    
                    if uptime_hours > 0:
                        logger.info(f"    PID 1 运行时间: {uptime_hours} 小时 {uptime_minutes % 60} 分钟")
                    else:
                        logger.info(f"    PID 1 运行时间: {uptime_minutes} 分钟 ({uptime_seconds} 秒)")
                else:
                    logger.info(f"    PID 1 运行时间: 无法精确计算")
            else:
                logger.error("    ❌ 无法解析 PID 1 统计信息")
    except Exception as e:
        logger.error(f"    ❌ 时间检查失败: {e}")

    # 状态总结和建议
    logger.info("  📊 状态总结:")
    if supercronic_is_pid1:
        logger.info("    ✅ supercronic 正确运行为 PID 1")
        logger.info("    ✅ 定时任务应该正常工作")
        
        # 显示当前的调度信息
        if cron_schedule != "未设置":
            logger.info(f"    ⏰ 当前调度: {cron_description}")
            
            # 提供一些常见的调度建议
            if "分钟" in cron_description and "每30分钟" not in cron_description and "每60分钟" not in cron_description:
                logger.info("    💡 频繁执行模式，适合实时监控")
            elif "小时" in cron_description:
                logger.info("    💡 按小时执行模式，适合定期汇总")
            elif "天" in cron_description:
                logger.info("    💡 每日执行模式，适合日报生成")
        
        logger.info("    💡 如果定时任务不执行，检查:")
        logger.info("       • crontab 格式是否正确")
        logger.info("       • 时区设置是否正确")
        logger.error("       • 应用程序是否有错误")
    else:
        logger.error("    ❌ supercronic 状态异常")
        if pid1_cmdline:
            logger.info(f"    📋 当前 PID 1: {pid1_cmdline}")
        logger.info("    💡 建议操作:")
        logger.info("       • 重启容器: docker restart trend-radar")
        logger.info("       • 检查容器日志: docker logs trend-radar")

    # 显示日志检查建议
    logger.info("  📋 运行状态检查:")
    logger.info("    • 查看完整容器日志: docker logs trend-radar")
    logger.info("    • 查看实时日志: docker logs -f trend-radar")
    logger.info("    • 手动执行测试: python manage.py run")
    logger.info("    • 重启容器服务: docker restart trend-radar")


def show_config():
    """显示当前配置"""
    logger.info("⚙️ 当前配置:")

    env_vars = [
        "CRON_SCHEDULE",
        "RUN_MODE",
        "IMMEDIATE_RUN",
        "FEISHU_WEBHOOK_URL",
        "DINGTALK_WEBHOOK_URL",
        "WEWORK_WEBHOOK_URL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "CONFIG_PATH",
        "FREQUENCY_WORDS_PATH",
    ]

    for var in env_vars:
        value = os.environ.get(var, "未设置")
        # 隐藏敏感信息
        if any(sensitive in var for sensitive in ["WEBHOOK", "TOKEN", "KEY"]):
            if value and value != "未设置":
                masked_value = value[:10] + "***" if len(value) > 10 else "***"
                logger.info(f"  {var}: {masked_value}")
            else:
                logger.info(f"  {var}: {value}")
        else:
            logger.info(f"  {var}: {value}")

    crontab_file = "/tmp/crontab"
    if Path(crontab_file).exists():
        logger.info("  📅 Crontab内容:")
        try:
            with open(crontab_file, "r") as f:
                content = f.read().strip()
                logger.info(f"    {content}")
        except Exception as e:
            logger.error(f"    读取失败: {e}")
    else:
        logger.info("  📅 Crontab文件不存在")


def show_files():
    """显示输出文件"""
    logger.info("📁 输出文件:")

    output_dir = Path("/app/output")
    if not output_dir.exists():
        logger.info("  📭 输出目录不存在")
        return

    # 显示最近的文件
    date_dirs = sorted([d for d in output_dir.iterdir() if d.is_dir()], reverse=True)

    if not date_dirs:
        logger.info("  📭 输出目录为空")
        return

    # 显示最近2天的文件
    for date_dir in date_dirs[:2]:
        logger.info(f"  📅 {date_dir.name}:")
        for subdir in ["html", "txt"]:
            sub_path = date_dir / subdir
            if sub_path.exists():
                files = list(sub_path.glob("*"))
                if files:
                    recent_files = sorted(
                        files, key=lambda x: x.stat().st_mtime, reverse=True
                    )[:3]
                    logger.info(f"    📂 {subdir}: {len(files)} 个文件")
                    for file in recent_files:
                        mtime = time.ctime(file.stat().st_mtime)
                        size_kb = file.stat().st_size // 1024
                        logger.info(f"      📄 {file.name} ({size_kb}KB, {mtime.split()[3][:5]})")
                else:
                    logger.info(f"    📂 {subdir}: 空")


def show_logs():
    """显示实时日志"""
    logger.info("📋 实时日志 (按 Ctrl+C 退出):")
    logger.info("💡 提示: 这将显示 PID 1 进程的输出")
    try:
        # 尝试多种方法查看日志
        log_files = [
            "/proc/1/fd/1",  # PID 1 的标准输出
            "/proc/1/fd/2",  # PID 1 的标准错误
        ]
        
        for log_file in log_files:
            if Path(log_file).exists():
                logger.info(f"📄 尝试读取: {log_file}")
                subprocess.run(["tail", "-f", log_file], check=True)
                break
        else:
            logger.info("📋 无法找到标准日志文件，建议使用: docker logs trend-radar")
            
    except KeyboardInterrupt:
        logger.info("\n👋 退出日志查看")
    except Exception as e:
        logger.error(f"❌ 查看日志失败: {e}")
        logger.info("💡 建议使用: docker logs trend-radar")


def restart_supercronic():
    """重启supercronic进程"""
    logger.info("🔄 重启supercronic...")
    logger.warning("⚠️ 注意: supercronic 是 PID 1，无法直接重启")

    # 检查当前 PID 1
    try:
        with open('/proc/1/cmdline', 'r') as f:
            pid1_cmdline = f.read().replace('\x00', ' ').strip()
        logger.info(f"  🔍 当前 PID 1: {pid1_cmdline}")

        if "supercronic" in pid1_cmdline.lower():
            logger.info("  ✅ PID 1 是 supercronic")
            logger.info("  💡 要重启 supercronic，需要重启整个容器:")
            logger.info("    docker restart trend-radar")
        else:
            logger.error("  ❌ PID 1 不是 supercronic，这是异常状态")
            logger.info("  💡 建议重启容器以修复问题:")
            logger.info("    docker restart trend-radar")
    except Exception as e:
        logger.error(f"  ❌ 无法检查 PID 1: {e}")
        logger.info("  💡 建议重启容器: docker restart trend-radar")


def start_webserver():
    """启动 Web 服务器托管 output 目录"""
    logger.info(f"🌐 启动 Web 服务器 (端口: {WEBSERVER_PORT})...")
    logger.info(f"  🔒 安全提示：仅提供静态文件访问，限制在 {WEBSERVER_DIR} 目录")

    # 检查是否已经运行
    if Path(WEBSERVER_PID_FILE).exists():
        try:
            with open(WEBSERVER_PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            try:
                os.kill(old_pid, 0)  # 检查进程是否存在
                logger.warning(f"  ⚠️ Web 服务器已在运行 (PID: {old_pid})")
                logger.info(f"  💡 访问: http://localhost:{WEBSERVER_PORT}")
                logger.info("  💡 停止服务: python manage.py stop_webserver")
                return
            except OSError:
                # 进程不存在，删除旧的 PID 文件
                os.remove(WEBSERVER_PID_FILE)
        except Exception as e:
            logger.warning(f"  ⚠️ 清理旧的 PID 文件: {e}")
            try:
                os.remove(WEBSERVER_PID_FILE)
            except:
                pass

    # 检查目录是否存在
    if not Path(WEBSERVER_DIR).exists():
        logger.error(f"  ❌ 目录不存在: {WEBSERVER_DIR}")
        return

    try:
        # 启动 HTTP 服务器
        # 使用 --bind 绑定到 0.0.0.0 使容器内部可访问
        # 工作目录限制在 WEBSERVER_DIR，防止访问其他目录
        process = subprocess.Popen(
            [sys.executable, '-m', 'http.server', str(WEBSERVER_PORT), '--bind', '0.0.0.0'],
            cwd=WEBSERVER_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )

        # 等待一下确保服务器启动
        time.sleep(1)

        # 检查进程是否还在运行
        if process.poll() is None:
            # 保存 PID
            with open(WEBSERVER_PID_FILE, 'w') as f:
                f.write(str(process.pid))

            logger.info(f"  ✅ Web 服务器已启动 (PID: {process.pid})")
            logger.info(f"  📁 服务目录: {WEBSERVER_DIR} (只读，仅静态文件)")
            logger.info(f"  🌐 访问地址: http://localhost:{WEBSERVER_PORT}")
            logger.info(f"  📄 首页: http://localhost:{WEBSERVER_PORT}/index.html")
            logger.info("  💡 停止服务: python manage.py stop_webserver")
        else:
            logger.error(f"  ❌ Web 服务器启动失败")
    except Exception as e:
        logger.error(f"  ❌ 启动失败: {e}")


def stop_webserver():
    """停止 Web 服务器"""
    logger.info("🛑 停止 Web 服务器...")

    if not Path(WEBSERVER_PID_FILE).exists():
        logger.info("  ℹ️ Web 服务器未运行")
        return

    try:
        with open(WEBSERVER_PID_FILE, 'r') as f:
            pid = int(f.read().strip())

        try:
            # 尝试终止进程
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)

            # 检查进程是否已终止
            try:
                os.kill(pid, 0)
                # 进程还在，强制杀死
                os.kill(pid, signal.SIGKILL)
                logger.warning(f"  ⚠️ 强制停止 Web 服务器 (PID: {pid})")
            except OSError:
                logger.info(f"  ✅ Web 服务器已停止 (PID: {pid})")
        except OSError as e:
            if e.errno == 3:  # No such process
                logger.info(f"  ℹ️ 进程已不存在 (PID: {pid})")
            else:
                raise

        # 删除 PID 文件
        os.remove(WEBSERVER_PID_FILE)
    except Exception as e:
        logger.error(f"  ❌ 停止失败: {e}")
        # 尝试清理 PID 文件
        try:
            os.remove(WEBSERVER_PID_FILE)
        except:
            pass


def webserver_status():
    """查看 Web 服务器状态"""
    logger.info("🌐 Web 服务器状态:")

    if not Path(WEBSERVER_PID_FILE).exists():
        logger.info("  ⭕ 未运行")
        logger.info(f"  💡 启动服务: python manage.py start_webserver")
        return

    try:
        with open(WEBSERVER_PID_FILE, 'r') as f:
            pid = int(f.read().strip())

        try:
            os.kill(pid, 0)  # 检查进程是否存在
            logger.info(f"  ✅ 运行中 (PID: {pid})")
            logger.info(f"  📁 服务目录: {WEBSERVER_DIR}")
            logger.info(f"  🌐 访问地址: http://localhost:{WEBSERVER_PORT}")
            logger.info(f"  📄 首页: http://localhost:{WEBSERVER_PORT}/index.html")
            logger.info("  💡 停止服务: python manage.py stop_webserver")
        except OSError:
            logger.info(f"  ⭕ 未运行 (PID 文件存在但进程不存在)")
            os.remove(WEBSERVER_PID_FILE)
            logger.info("  💡 启动服务: python manage.py start_webserver")
    except Exception as e:
        logger.error(f"  ❌ 状态检查失败: {e}")


def show_help():
    """显示帮助信息"""
    help_text = """
🐳 TrendRadar 容器管理工具

📋 命令列表:
  run              - 手动执行一次爬虫
  status           - 显示容器运行状态
  config           - 显示当前配置
  files            - 显示输出文件
  logs             - 实时查看日志
  restart          - 重启说明
  start_webserver  - 启动 Web 服务器托管 output 目录
  stop_webserver   - 停止 Web 服务器
  webserver_status - 查看 Web 服务器状态
  help             - 显示此帮助

📖 使用示例:
  # 在容器中执行
  python manage.py run
  python manage.py status
  python manage.py logs
  python manage.py start_webserver

  # 在宿主机执行
  docker exec -it trend-radar python manage.py run
  docker exec -it trend-radar python manage.py status
  docker exec -it trend-radar python manage.py start_webserver
  docker logs trend-radar

💡 常用操作指南:
  1. 检查运行状态: status
     - 查看 supercronic 是否为 PID 1
     - 检查配置文件和关键文件
     - 查看 cron 调度设置

  2. 手动执行测试: run
     - 立即执行一次新闻爬取
     - 测试程序是否正常工作

  3. 查看日志: logs
     - 实时监控运行情况
     - 也可使用: docker logs trend-radar

  4. 重启服务: restart
     - 由于 supercronic 是 PID 1，需要重启整个容器
     - 使用: docker restart trend-radar

  5. Web 服务器管理:
     - 启动: start_webserver
     - 停止: stop_webserver
     - 状态: webserver_status
     - 访问: http://localhost:8080
"""
    logger.info(help_text)


def main():
    if len(sys.argv) < 2:
        show_help()
        return

    command = sys.argv[1]
    commands = {
        "run": manual_run,
        "status": show_status,
        "config": show_config,
        "files": show_files,
        "logs": show_logs,
        "restart": restart_supercronic,
        "start_webserver": start_webserver,
        "stop_webserver": stop_webserver,
        "webserver_status": webserver_status,
        "help": show_help,
    }

    if command in commands:
        try:
            commands[command]()
        except KeyboardInterrupt:
            logger.info("\n👋 操作已取消")
        except Exception as e:
            logger.error(f"❌ 执行出错: {e}")
    else:
        logger.error(f"❌ 未知命令: {command}")
        logger.info("运行 'python manage.py help' 查看可用命令")


if __name__ == "__main__":
    main()