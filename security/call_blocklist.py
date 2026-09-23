"""
security/call_blocklist.py

Centralized Call Rejection & Number Blocklist for LiveKit Voice Agent.

Provides mechanisms to reject incoming and outgoing calls based on phone numbers,
SIP caller IDs, and participant identities.

Supported management sources:
1. IN-CODE LIST: Edit BLOCKED_NUMBERS directly in this file.
2. JSON STORAGE: Stored in data/blocked_numbers.json (auto-saved & reloaded).
3. ENVIRONMENT VARIABLE: BLOCKED_PHONE_NUMBERS in .env (comma-separated).
4. SCRIPT / CLI: Use manage_blocked_numbers.py to add/remove/list at runtime.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("call_blocklist")

# Path to the persistent JSON file
_DEFAULT_JSON_PATH = Path(__file__).resolve().parent.parent / "data" / "blocked_numbers.json"

# ==============================================================================
# IN-CODE BLOCKED NUMBERS LIST
# You can directly add/remove numbers, SIP usernames, or caller identities here.
# ==============================================================================
BLOCKED_NUMBERS: set[str] = set(
    [
        # Example format:
        #"+919786586806",asuf sir number
        "+919360827096",
        # "101",
    ]
)


def normalize_number(value: str | None) -> str:
    """
    Normalizes a phone number or caller identity for reliable comparison.
    - Strips 'sip:', 'sip_', '@domain...'
    - Removes whitespace, dashes, parentheses, dots
    - Preserves digits and leading '+'
    """
    if not value:
        return ""
    
    val = str(value).strip()
    
    # Remove 'sip:' URI scheme or 'sip_' LiveKit prefix
    if val.lower().startswith("sip:"):
        val = val[4:]
    elif val.lower().startswith("sip_"):
        val = val[4:]
        
    # Strip domain part if SIP URI like 100@192.168.1.1
    if "@" in val:
        val = val.split("@")[0]
        
    # Remove punctuation/formatting characters (spaces, dashes, parens, dots)
    val = re.sub(r"[\s\-\(\)\.]", "", val)
    return val.lower()


def _get_number_variations(val: str) -> set[str]:
    """
    Generates variations of a normalized number to ensure robust matching.
    e.g. for '+919876543210': returns {'+919876543210', '919876543210', '9876543210'}
    """
    norm = normalize_number(val)
    if not norm:
        return set()
    
    variations = {norm}
    
    # Without leading +
    if norm.startswith("+"):
        variations.add(norm[1:])
        
    # Digits-only version
    digits_only = re.sub(r"\D", "", norm)
    if digits_only:
        variations.add(digits_only)
        # Handle 10-digit Indian number with 91 prefix
        if digits_only.startswith("91") and len(digits_only) == 12:
            variations.add(digits_only[2:])
        # Handle number with leading 0
        if digits_only.startswith("0") and len(digits_only) > 1:
            variations.add(digits_only.lstrip("0"))
            
    return variations


def _load_env_blocked_numbers() -> set[str]:
    """Loads blocked numbers defined in .env (BLOCKED_PHONE_NUMBERS)."""
    env_val = os.environ.get("BLOCKED_PHONE_NUMBERS", "").strip()
    if not env_val:
        return set()
    return {num.strip() for num in env_val.split(",") if num.strip()}


def _load_json_blocked_numbers(json_path: Path = _DEFAULT_JSON_PATH) -> set[str]:
    """Loads blocked numbers from the JSON file."""
    if not json_path.exists():
        return set()
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return {str(item).strip() for item in data if str(item).strip()}
            elif isinstance(data, dict):
                return {str(k).strip() for k in data.keys() if str(k).strip()}
    except Exception as e:
        logger.warning(f"Failed to read blocked numbers from {json_path}: {e}")
    return set()


def _save_json_blocked_numbers(numbers: Iterable[str], json_path: Path = _DEFAULT_JSON_PATH) -> bool:
    """Saves the combined blocked numbers to JSON file."""
    try:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        sorted_list = sorted(list(set(numbers)))
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(sorted_list, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save blocked numbers to {json_path}: {e}")
        return False


def get_all_blocked_numbers(json_path: Path = _DEFAULT_JSON_PATH) -> set[str]:
    """
    Returns all active blocked numbers combined from:
    1. IN-CODE BLOCKED_NUMBERS
    2. JSON file (data/blocked_numbers.json)
    3. .env BLOCKED_PHONE_NUMBERS
    """
    combined = set()
    combined.update(BLOCKED_NUMBERS)
    combined.update(_load_json_blocked_numbers(json_path))
    combined.update(_load_env_blocked_numbers())
    return combined


def add_blocked_number(number: str, json_path: Path = _DEFAULT_JSON_PATH) -> bool:
    """
    Adds a number to the persistent blocklist and in-memory set.
    """
    clean_num = str(number).strip()
    if not clean_num:
        return False
    
    BLOCKED_NUMBERS.add(clean_num)
    all_numbers = get_all_blocked_numbers(json_path)
    all_numbers.add(clean_num)
    saved = _save_json_blocked_numbers(all_numbers, json_path)
    logger.info(f"Added '{clean_num}' to call blocklist (persisted: {saved}).")
    return True


def remove_blocked_number(number: str, json_path: Path = _DEFAULT_JSON_PATH) -> bool:
    """
    Removes a number from the in-memory set and the JSON persistent blocklist.
    """
    target = str(number).strip()
    if not target:
        return False
    
    target_variations = _get_number_variations(target)
    
    # Remove from in-memory set
    to_remove_incode = [n for n in BLOCKED_NUMBERS if bool(_get_number_variations(n) & target_variations)]
    for n in to_remove_incode:
        BLOCKED_NUMBERS.discard(n)
        
    # Remove from JSON file
    current_json = _load_json_blocked_numbers(json_path)
    updated_json = {n for n in current_json if not bool(_get_number_variations(n) & target_variations)}
    saved = _save_json_blocked_numbers(updated_json, json_path)
    logger.info(f"Removed '{target}' from call blocklist (persisted: {saved}).")
    return True


def is_number_blocked(number: str | None, json_path: Path = _DEFAULT_JSON_PATH) -> bool:
    """
    Checks if a given phone number, SIP caller ID, or identity is blocked.
    Returns True if blocked, False otherwise.
    """
    if not number:
        return False
    
    candidate_variations = _get_number_variations(number)
    if not candidate_variations:
        return False
    
    all_blocked = get_all_blocked_numbers(json_path)
    for blocked in all_blocked:
        blocked_variations = _get_number_variations(blocked)
        if candidate_variations & blocked_variations:
            return True
        # Direct string check
        if normalize_number(blocked) == normalize_number(number):
            return True
            
    return False


def extract_caller_identifiers(entity: Any) -> list[str]:
    """
    Extracts all candidate caller phone numbers / IDs from a participant,
    JobContext, JobRequest, or dictionary.
    """
    identifiers: list[str] = []
    
    if entity is None:
        return identifiers

    # 1. Plain string or number
    if isinstance(entity, (str, int)):
        identifiers.append(str(entity))
        return identifiers

    # 2. Dictionary / JSON metadata
    if isinstance(entity, dict):
        for key in ("sip.phoneNumber", "phoneNumber", "phone_number", "phone", "from", "caller_id", "identity", "name"):
            val = entity.get(key)
            if val and isinstance(val, str):
                identifiers.append(val)
        return identifiers

    # 3. LiveKit Participant (rtc.RemoteParticipant or proto ParticipantInfo)
    if hasattr(entity, "identity") and entity.identity:
        identifiers.append(entity.identity)
    if hasattr(entity, "name") and entity.name:
        identifiers.append(entity.name)
    if hasattr(entity, "attributes") and entity.attributes:
        attrs = entity.attributes
        if hasattr(attrs, "get"):
            for key in ("sip.phoneNumber", "sip.callerId", "sip.trunkPhoneNumber", "sip.from", "phoneNumber", "phone_number", "phone"):
                val = attrs.get(key)
                if val:
                    identifiers.append(str(val))

    # 4. LiveKit JobContext
    if hasattr(entity, "room") and entity.room:
        if hasattr(entity.room, "remote_participants"):
            for p in entity.room.remote_participants.values():
                identifiers.extend(extract_caller_identifiers(p))
    if hasattr(entity, "job") and entity.job:
        if hasattr(entity.job, "participant") and entity.job.participant:
            identifiers.extend(extract_caller_identifiers(entity.job.participant))
        if hasattr(entity.job, "metadata") and entity.job.metadata:
            try:
                meta = json.loads(entity.job.metadata) if isinstance(entity.job.metadata, str) else entity.job.metadata
                if isinstance(meta, dict):
                    identifiers.extend(extract_caller_identifiers(meta))
            except Exception:
                pass

    return list(dict.fromkeys([i for i in identifiers if i]))


def is_call_blocked(entity: Any, json_path: Path = _DEFAULT_JSON_PATH) -> tuple[bool, str | None]:
    """
    Evaluates whether an incoming call (JobRequest, JobContext, RemoteParticipant, etc.)
    originates from a blocked number/identity.
    
    Returns:
        (is_blocked: bool, matched_identifier: str | None)
    """
    candidates = extract_caller_identifiers(entity)
    for candidate in candidates:
        if is_number_blocked(candidate, json_path):
            return True, candidate
    return False, None
