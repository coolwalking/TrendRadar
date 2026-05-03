# coding=utf-8

import smtplib
import traceback
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Dict, Optional

from trendradar import utils
from trendradar.config import SMTP_CONFIGS
from trendradar.logging_config import get_logger


logger = get_logger(__name__)
class EmailNotifier:
    """邮件通知器（独立实现，不走 webhook 流程）"""

    name = "邮件"

    def __init__(self, config: Dict):
        self.config = config

    def send(
        self,
        from_email: str,
        password: str,
        to_email: str,
        report_type: str,
        html_file_path: Optional[str] = None,
        custom_smtp_server: Optional[str] = None,
        custom_smtp_port: Optional[int] = None,
    ) -> bool:
        """发送邮件通知"""
        try:
            if not html_file_path or not Path(html_file_path).exists():
                logger.error(f"错误：HTML文件不存在或未提供: {html_file_path}")
                return False

            logger.info(f"使用HTML文件: {html_file_path}")
            with open(html_file_path, "r", encoding="utf-8") as f:
                html_content = f.read()

            domain = from_email.split("@")[-1].lower()

            if custom_smtp_server and custom_smtp_port:
                smtp_server = custom_smtp_server
                smtp_port = int(custom_smtp_port)
                if smtp_port == 465:
                    use_tls = False
                elif smtp_port == 587:
                    use_tls = True
                else:
                    use_tls = True
            elif domain in SMTP_CONFIGS:
                smtp_config = SMTP_CONFIGS[domain]
                smtp_server = str(smtp_config["server"])
                smtp_port = int(smtp_config["port"])
                use_tls = smtp_config["encryption"] == "TLS"
            else:
                logger.info(f"未识别的邮箱服务商: {domain}，使用通用 SMTP 配置")
                smtp_server = f"smtp.{domain}"
                smtp_port = 587
                use_tls = True

            msg = MIMEMultipart("alternative")
            sender_name = "TrendRadar"
            msg["From"] = formataddr((sender_name, from_email))

            recipients = [addr.strip() for addr in to_email.split(",")]
            msg["To"] = recipients[0] if len(recipients) == 1 else ", ".join(recipients)

            now = utils.get_beijing_time()
            subject = f"TrendRadar 热点分析报告 - {report_type} - {now.strftime('%m月%d日 %H:%M')}"
            msg["Subject"] = Header(subject, "utf-8")  # type: ignore[assignment]

            msg["MIME-Version"] = "1.0"
            msg["Date"] = formatdate(localtime=True)
            msg["Message-ID"] = make_msgid()

            text_content = f"""
TrendRadar 热点分析报告
========================
报告类型：{report_type}
生成时间：{now.strftime('%Y-%m-%d %H:%M:%S')}

请使用支持HTML的邮件客户端查看完整报告内容。
            """
            msg.attach(MIMEText(text_content, "plain", "utf-8"))
            msg.attach(MIMEText(html_content, "html", "utf-8"))

            logger.info(f"正在发送邮件到 {to_email}...")
            logger.info(f"SMTP 服务器: {smtp_server}:{smtp_port}")
            logger.info(f"发件人: {from_email}")

            if use_tls:
                server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
                server.ehlo()
                server.starttls()
                server.ehlo()
            else:
                server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)
                server.ehlo()

            server.login(from_email, password)
            server.send_message(msg)
            server.quit()

            logger.info(f"邮件发送成功 [{report_type}] -> {to_email}")
            return True

        except smtplib.SMTPServerDisconnected:
            logger.error("邮件发送失败：服务器意外断开连接，请检查网络或稍后重试")
            return False
        except smtplib.SMTPAuthenticationError as e:
            logger.error("邮件发送失败：认证错误，请检查邮箱和密码/授权码")
            logger.error(f"详细错误: {str(e)}")
            return False
        except smtplib.SMTPRecipientsRefused as e:
            logger.error(f"邮件发送失败：收件人地址被拒绝 {e}")
            return False
        except smtplib.SMTPSenderRefused as e:
            logger.error(f"邮件发送失败：发件人地址被拒绝 {e}")
            return False
        except smtplib.SMTPDataError as e:
            logger.error(f"邮件发送失败：邮件数据错误 {e}")
            return False
        except smtplib.SMTPConnectError as e:
            logger.error(f"邮件发送失败：无法连接到 SMTP 服务器")
            logger.error(f"详细错误: {str(e)}")
            return False
        except Exception as e:
            logger.exception(f"邮件发送失败 [{report_type}]：{e}")
            return False