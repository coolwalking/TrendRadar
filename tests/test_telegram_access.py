# coding=utf-8

import unittest
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import _bootstrap

B = _bootstrap.load_all()
ACCESS = B.access
build_telegram_access_config = ACCESS.build_telegram_access_config
check_command_authorized = ACCESS.check_command_authorized
extract_chat_id_from_update = ACCESS.extract_chat_id_from_update
is_command_chat = ACCESS.is_command_chat
is_receiver_chat = ACCESS.is_receiver_chat
parse_chat_id_list = ACCESS.parse_chat_id_list


class TestTelegramAccessConfig(unittest.TestCase):
    def test_legacy_chat_id_becomes_owner_receiver_command(self):
        cfg = build_telegram_access_config({"TELEGRAM_CHAT_ID": "111"})
        self.assertEqual(cfg["owner_chat_ids"], ["111"])
        self.assertEqual(cfg["receiver_chat_ids"], ["111"])
        self.assertEqual(cfg["command_chat_ids"], ["111"])

    def test_owner_ids_merge_into_receiver_and_command(self):
        cfg = build_telegram_access_config({"TELEGRAM_OWNER_CHAT_IDS": "111, 222"})
        self.assertEqual(cfg["owner_chat_ids"], ["111", "222"])
        self.assertEqual(cfg["receiver_chat_ids"], ["111", "222"])
        self.assertEqual(cfg["command_chat_ids"], ["111", "222"])

    def test_receiver_list_trims_empty_values_and_dedupes(self):
        self.assertEqual(parse_chat_id_list(" 222, ,333,222, "), ["222", "333"])

    def test_command_ids_subset_passes(self):
        cfg = build_telegram_access_config(
            {
                "TELEGRAM_RECEIVER_CHAT_IDS": "222,333",
                "TELEGRAM_COMMAND_CHAT_IDS": "222",
            }
        )
        self.assertEqual(cfg["receiver_chat_ids"], ["222", "333"])
        self.assertEqual(cfg["command_chat_ids"], ["222"])

    def test_command_ids_outside_receiver_fail(self):
        with self.assertRaisesRegex(ValueError, "333"):
            build_telegram_access_config(
                {
                    "TELEGRAM_RECEIVER_CHAT_IDS": "222",
                    "TELEGRAM_COMMAND_CHAT_IDS": "333",
                }
            )

    def test_negative_chat_id_is_preserved(self):
        cfg = build_telegram_access_config({"TELEGRAM_CHAT_ID": "-100123456789"})
        self.assertEqual(cfg["owner_chat_ids"], ["-100123456789"])

    def test_duplicate_chat_ids_are_deduped_in_order(self):
        cfg = build_telegram_access_config(
            {
                "TELEGRAM_CHAT_ID": "111",
                "TELEGRAM_OWNER_CHAT_IDS": "111,222",
                "TELEGRAM_RECEIVER_CHAT_IDS": "222,333",
                "TELEGRAM_COMMAND_CHAT_IDS": "222",
            }
        )
        self.assertEqual(cfg["owner_chat_ids"], ["111", "222"])
        self.assertEqual(cfg["receiver_chat_ids"], ["111", "222", "333"])
        self.assertEqual(cfg["command_chat_ids"], ["111", "222"])

    def test_invalid_unauthorized_behavior_falls_back_to_ignore(self):
        cfg = build_telegram_access_config({"TELEGRAM_UNAUTHORIZED_BEHAVIOR": "loud"})
        self.assertEqual(cfg["unauthorized_behavior"], "ignore")

    def test_commands_enabled_defaults_false(self):
        cfg = build_telegram_access_config({"TELEGRAM_CHAT_ID": "111"})
        self.assertFalse(cfg["commands_enabled"])


class TestTelegramAccessChecks(unittest.TestCase):
    def setUp(self):
        self.cfg = build_telegram_access_config(
            {
                "TELEGRAM_CHAT_ID": "111",
                "TELEGRAM_RECEIVER_CHAT_IDS": "222,333",
                "TELEGRAM_COMMAND_CHAT_IDS": "222",
                "TELEGRAM_COMMANDS_ENABLED": "true",
                "TELEGRAM_UNAUTHORIZED_BEHAVIOR": "reply",
            }
        )

    def test_owner_can_receive_and_command(self):
        self.assertTrue(is_receiver_chat("111", self.cfg))
        self.assertTrue(is_command_chat("111", self.cfg))

    def test_receiver_only_cannot_command(self):
        self.assertTrue(is_receiver_chat("333", self.cfg))
        self.assertFalse(is_command_chat("333", self.cfg))

    def test_command_user_can_command(self):
        self.assertTrue(is_command_chat("222", self.cfg))

    def test_unknown_chat_denied(self):
        self.assertFalse(is_receiver_chat("999", self.cfg))
        self.assertFalse(is_command_chat("999", self.cfg))

    def test_commands_disabled_denies_even_whitelisted_chat(self):
        cfg = build_telegram_access_config(
            {
                "TELEGRAM_CHAT_ID": "111",
                "TELEGRAM_COMMANDS_ENABLED": "false",
            }
        )
        result = check_command_authorized({"message": {"chat": {"id": 111}}}, cfg)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "commands_disabled")

    def test_malformed_update_without_chat_id_denied(self):
        result = check_command_authorized({"message": {}}, self.cfg)
        self.assertFalse(result.allowed)
        self.assertIsNone(result.chat_id)
        self.assertEqual(result.behavior, "reply")

    def test_extract_message_chat_id(self):
        self.assertEqual(
            extract_chat_id_from_update({"message": {"chat": {"id": -100123}}}),
            "-100123",
        )

    def test_extract_callback_query_message_chat_id(self):
        update = {"callback_query": {"message": {"chat": {"id": "222"}}, "from": {"id": "999"}}}
        self.assertEqual(extract_chat_id_from_update(update), "222")

    def test_extract_non_dict_update_returns_none(self):
        self.assertIsNone(extract_chat_id_from_update(None))
        self.assertIsNone(extract_chat_id_from_update("bad update"))

    def test_check_command_authorized_allows_whitelisted_chat(self):
        result = check_command_authorized({"message": {"chat": {"id": "222"}}}, self.cfg)
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason, "authorized")


if __name__ == "__main__":
    unittest.main()
