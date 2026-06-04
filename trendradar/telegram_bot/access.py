# coding=utf-8
"""Pure Telegram access-control helpers.

Legacy compatibility:
- TELEGRAM_CHAT_ID keeps working and is treated as owner chat id.
- Legacy multi-account chat ids in TELEGRAM_CHAT_ID are also owners.
- Owners automatically have receiver and command permissions.
- If a chat should only receive outbound pushes and must not run future
  commands, move it to TELEGRAM_RECEIVER_CHAT_IDS instead of keeping it in
  TELEGRAM_CHAT_ID.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


AUTHORIZED_BEHAVIORS = {"ignore", "reply"}


@dataclass(frozen=True)
class AuthorizationResult:
    allowed: bool
    chat_id: Optional[str]
    behavior: str
    reason: str


def normalize_chat_id(value: Any) -> Optional[str]:
    """Normalize a Telegram chat id to string while preserving signs."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dedupe_keep_order(values: Iterable[Optional[str]]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        normalized = normalize_chat_id(value)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def parse_chat_id_list(value: Any) -> List[str]:
    """Parse comma-separated chat ids, trimming whitespace and empty entries."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return _dedupe_keep_order(value)
    return _dedupe_keep_order(part for part in str(value).split(","))


def parse_legacy_chat_id_list(value: Any) -> List[str]:
    """Parse old TELEGRAM_CHAT_ID value using the existing semicolon account separator."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return _dedupe_keep_order(value)
    return _dedupe_keep_order(part for part in str(value).split(";"))


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_behavior(value: Any) -> str:
    behavior = str(value or "ignore").strip().lower()
    return behavior if behavior in AUTHORIZED_BEHAVIORS else "ignore"


def build_telegram_access_config(raw_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build normalized Telegram access config from env/YAML-style values."""
    raw = raw_config or {}

    legacy_owner_ids = parse_legacy_chat_id_list(raw.get("TELEGRAM_CHAT_ID") or raw.get("chat_id"))
    explicit_owner_ids = parse_chat_id_list(
        raw.get("TELEGRAM_OWNER_CHAT_IDS") or raw.get("owner_chat_ids")
    )
    explicit_receiver_ids = parse_chat_id_list(
        raw.get("TELEGRAM_RECEIVER_CHAT_IDS") or raw.get("receiver_chat_ids")
    )
    explicit_command_ids = parse_chat_id_list(
        raw.get("TELEGRAM_COMMAND_CHAT_IDS") or raw.get("command_chat_ids")
    )

    owner_chat_ids = _dedupe_keep_order([*legacy_owner_ids, *explicit_owner_ids])
    receiver_chat_ids = _dedupe_keep_order([*owner_chat_ids, *explicit_receiver_ids])
    command_chat_ids = _dedupe_keep_order([*owner_chat_ids, *explicit_command_ids])

    config = {
        "owner_chat_ids": owner_chat_ids,
        "receiver_chat_ids": receiver_chat_ids,
        "command_chat_ids": command_chat_ids,
        "explicit_receiver_chat_ids": explicit_receiver_ids,
        "explicit_command_chat_ids": explicit_command_ids,
        "commands_enabled": _as_bool(
            raw.get("TELEGRAM_COMMANDS_ENABLED", raw.get("commands_enabled")), False
        ),
        "unauthorized_behavior": _normalize_behavior(
            raw.get("TELEGRAM_UNAUTHORIZED_BEHAVIOR", raw.get("unauthorized_behavior"))
        ),
    }
    validate_telegram_access_config(config)
    return config


def validate_telegram_access_config(config: Dict[str, Any]) -> None:
    receiver_ids = set(config.get("receiver_chat_ids") or [])
    missing = [chat_id for chat_id in config.get("command_chat_ids", []) if chat_id not in receiver_ids]
    if missing:
        raise ValueError(
            "Telegram command_chat_ids must be a subset of receiver_chat_ids after owner merge: "
            + ", ".join(missing)
        )


def extract_chat_id_from_update(update: Dict[str, Any]) -> Optional[str]:
    """Extract chat id from a Telegram update without side effects."""
    if not isinstance(update, dict):
        return None

    for message_key in ("message", "edited_message"):
        message = update.get(message_key)
        if isinstance(message, dict):
            chat = message.get("chat")
            if isinstance(chat, dict):
                chat_id = normalize_chat_id(chat.get("id"))
                if chat_id is not None:
                    return chat_id

    callback_query = update.get("callback_query")
    if isinstance(callback_query, dict):
        message = callback_query.get("message")
        if isinstance(message, dict):
            chat = message.get("chat")
            if isinstance(chat, dict):
                chat_id = normalize_chat_id(chat.get("id"))
                if chat_id is not None:
                    return chat_id
        sender = callback_query.get("from")
        if isinstance(sender, dict):
            return normalize_chat_id(sender.get("id"))

    return None


def is_receiver_chat(chat_id: Any, access_config: Dict[str, Any]) -> bool:
    normalized = normalize_chat_id(chat_id)
    return bool(normalized and normalized in set(access_config.get("receiver_chat_ids") or []))


def is_command_chat(chat_id: Any, access_config: Dict[str, Any]) -> bool:
    normalized = normalize_chat_id(chat_id)
    return bool(normalized and normalized in set(access_config.get("command_chat_ids") or []))


def check_command_authorized(update: Dict[str, Any], access_config: Dict[str, Any]) -> AuthorizationResult:
    behavior = _normalize_behavior(access_config.get("unauthorized_behavior"))
    chat_id = extract_chat_id_from_update(update)
    if chat_id is None:
        return AuthorizationResult(False, None, behavior, "missing_chat_id")
    if not access_config.get("commands_enabled", False):
        return AuthorizationResult(False, chat_id, behavior, "commands_disabled")
    if not is_command_chat(chat_id, access_config):
        return AuthorizationResult(False, chat_id, behavior, "unauthorized_chat")
    return AuthorizationResult(True, chat_id, behavior, "authorized")
