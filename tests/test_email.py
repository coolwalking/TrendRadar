# coding=utf-8

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trendradar.notifier.email import EmailNotifier


class TestEmailNotifier:
    def _make_config(self):
        return {}

    def _make_html_file(self, tmpdir):
        p = Path(tmpdir) / "report.html"
        p.write_text("<html><body>Test</body></html>", encoding="utf-8")
        return str(p)

    @patch("trendradar.notifier.email.smtplib.SMTP")
    def test_send_success_tls(self, mock_smtp_cls, tmp_path):
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        html_path = tmp_path / "report.html"
        html_path.write_text("<html>test</html>", encoding="utf-8")

        notifier = EmailNotifier(self._make_config())
        result = notifier.send(
            "user@gmail.com",
            "password",
            "to@example.com",
            "当日汇总",
            str(html_path),
        )
        assert result is True
        mock_server.starttls.assert_called()
        mock_server.login.assert_called_with("user@gmail.com", "password")
        mock_server.send_message.assert_called()
        mock_server.quit.assert_called()

    @patch("trendradar.notifier.email.smtplib.SMTP_SSL")
    def test_send_success_ssl(self, mock_smtp_cls, tmp_path):
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        html_path = tmp_path / "report.html"
        html_path.write_text("<html>test</html>", encoding="utf-8")

        notifier = EmailNotifier(self._make_config())
        result = notifier.send(
            "user@qq.com",
            "password",
            "to@example.com",
            "当日汇总",
            str(html_path),
        )
        assert result is True
        mock_server.login.assert_called()

    @patch("trendradar.notifier.email.smtplib.SMTP")
    def test_send_with_custom_smtp(self, mock_smtp_cls, tmp_path):
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        html_path = tmp_path / "report.html"
        html_path.write_text("<html>test</html>", encoding="utf-8")

        notifier = EmailNotifier(self._make_config())
        result = notifier.send(
            "user@custom.com",
            "password",
            "to@example.com",
            "当日汇总",
            str(html_path),
            "smtp.custom.com",
            "587",
        )
        assert result is True
        mock_smtp_cls.assert_called_with("smtp.custom.com", 587, timeout=30)

    def test_send_missing_html_file(self):
        notifier = EmailNotifier(self._make_config())
        result = notifier.send(
            "user@gmail.com",
            "password",
            "to@example.com",
            "当日汇总",
            "/nonexistent/file.html",
        )
        assert result is False

    @patch("trendradar.notifier.email.smtplib.SMTP")
    def test_send_authentication_error(self, mock_smtp_cls, tmp_path):
        import smtplib

        mock_server = MagicMock()
        mock_server.starttls.side_effect = smtplib.SMTPAuthenticationError(535, b"Auth failed")
        mock_smtp_cls.return_value = mock_server

        html_path = tmp_path / "report.html"
        html_path.write_text("<html>test</html>", encoding="utf-8")

        notifier = EmailNotifier(self._make_config())
        result = notifier.send(
            "user@gmail.com",
            "password",
            "to@example.com",
            "当日汇总",
            str(html_path),
        )
        assert result is False

    @patch("trendradar.notifier.email.smtplib.SMTP")
    def test_send_connect_error(self, mock_smtp_cls, tmp_path):
        import smtplib

        mock_smtp_cls.side_effect = smtplib.SMTPConnectError(421, b"Cannot connect")

        html_path = tmp_path / "report.html"
        html_path.write_text("<html>test</html>", encoding="utf-8")

        notifier = EmailNotifier(self._make_config())
        result = notifier.send(
            "user@gmail.com",
            "password",
            "to@example.com",
            "当日汇总",
            str(html_path),
        )
        assert result is False

    @patch("trendradar.notifier.email.smtplib.SMTP")
    def test_send_server_disconnected(self, mock_smtp_cls, tmp_path):
        import smtplib

        mock_server = MagicMock()
        mock_server.starttls.side_effect = smtplib.SMTPServerDisconnected("Server disconnected")
        mock_smtp_cls.return_value = mock_server

        html_path = tmp_path / "report.html"
        html_path.write_text("<html>test</html>", encoding="utf-8")

        notifier = EmailNotifier(self._make_config())
        result = notifier.send(
            "user@gmail.com",
            "password",
            "to@example.com",
            "当日汇总",
            str(html_path),
        )
        assert result is False

    @patch("trendradar.notifier.email.smtplib.SMTP")
    def test_send_multiple_recipients(self, mock_smtp_cls, tmp_path):
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        html_path = tmp_path / "report.html"
        html_path.write_text("<html>test</html>", encoding="utf-8")

        notifier = EmailNotifier(self._make_config())
        result = notifier.send(
            "user@gmail.com",
            "password",
            "to1@example.com, to2@example.com",
            "当日汇总",
            str(html_path),
        )
        assert result is True

    @patch("trendradar.notifier.email.smtplib.SMTP")
    def test_send_unknown_domain_defaults_smtp(self, mock_smtp_cls, tmp_path):
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        html_path = tmp_path / "report.html"
        html_path.write_text("<html>test</html>", encoding="utf-8")

        notifier = EmailNotifier(self._make_config())
        result = notifier.send(
            "user@unknown-domain.com",
            "password",
            "to@example.com",
            "当日汇总",
            str(html_path),
        )
        assert result is True
        mock_smtp_cls.assert_called_with("smtp.unknown-domain.com", 587, timeout=30)
