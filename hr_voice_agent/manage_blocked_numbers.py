"""
manage_blocked_numbers.py

CLI tool to manage blocked numbers / reject list for LiveKit calls.

Usage:
    python manage_blocked_numbers.py list
    python manage_blocked_numbers.py add <phone_number_or_sip_id> ...
    python manage_blocked_numbers.py remove <phone_number_or_sip_id> ...
    python manage_blocked_numbers.py check <phone_number_or_sip_id>
    python manage_blocked_numbers.py clear
"""

import sys
import argparse
from pathlib import Path

# Ensure root directory is on path
root_dir = Path(__file__).resolve().parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from security.call_blocklist import (
    get_all_blocked_numbers,
    add_blocked_number,
    remove_blocked_number,
    is_number_blocked,
    BLOCKED_NUMBERS,
    _load_json_blocked_numbers,
    _load_env_blocked_numbers,
    _save_json_blocked_numbers,
)


def cmd_list(args):
    all_blocked = sorted(list(get_all_blocked_numbers()))
    json_blocked = sorted(list(_load_json_blocked_numbers()))
    incode_blocked = sorted(list(BLOCKED_NUMBERS))
    env_blocked = sorted(list(_load_env_blocked_numbers()))
    
    print("\n" + "=" * 55)
    print(" 🚫 LIVEKIT REJECTED / BLOCKED NUMBERS")
    print("=" * 55)
    
    if not all_blocked:
        print("  No numbers currently blocked.")
        print("  All incoming & outgoing calls are allowed.")
    else:
        print(f"  Total Active Blocked Numbers: {len(all_blocked)}")
        print("-" * 55)
        for i, num in enumerate(all_blocked, 1):
            sources = []
            if num in json_blocked:
                sources.append("JSON (data/blocked_numbers.json)")
            if num in incode_blocked:
                sources.append("In-Code (call_blocklist.py)")
            if num in env_blocked:
                sources.append(".env")
            src_text = f" [{', '.join(sources)}]" if sources else ""
            print(f"  {i}. {num}{src_text}")
            
    print("=" * 55 + "\n")


def cmd_add(args):
    if not args.numbers:
        print("❌ Error: Please specify at least one number to add.")
        sys.exit(1)
        
    for num in args.numbers:
        add_blocked_number(num)
        print(f"✅ Added '{num}' to blocked list. Calls from this number will be rejected.")


def cmd_remove(args):
    if not args.numbers:
        print("❌ Error: Please specify at least one number to remove.")
        sys.exit(1)
        
    for num in args.numbers:
        remove_blocked_number(num)
        print(f"✅ Removed '{num}' from blocked list. Calls from this number are now allowed.")


def cmd_check(args):
    if not args.number:
        print("❌ Error: Please specify a number to check.")
        sys.exit(1)
        
    blocked = is_number_blocked(args.number)
    if blocked:
        print(f"🚫 [BLOCKED] Number '{args.number}' is in the reject list. Calls will be REJECTED.")
    else:
        print(f"✅ [ALLOWED] Number '{args.number}' is NOT in the reject list. Calls will be ACCEPTED.")


def cmd_clear(args):
    _save_json_blocked_numbers([])
    BLOCKED_NUMBERS.clear()
    print("✅ Cleared all blocked numbers from JSON storage and in-memory list.")


def main():
    parser = argparse.ArgumentParser(
        description="Manage blocked phone numbers & SIP IDs for LiveKit calls."
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # list
    subparsers.add_parser("list", help="List all currently blocked numbers")
    
    # add
    add_parser = subparsers.add_parser("add", help="Add one or more numbers to blocklist")
    add_parser.add_argument("numbers", nargs="+", help="Phone number(s) or SIP ID(s) to reject")
    
    # remove
    remove_parser = subparsers.add_parser("remove", help="Remove one or more numbers from blocklist")
    remove_parser.add_argument("numbers", nargs="+", help="Phone number(s) or SIP ID(s) to remove")
    
    # check
    check_parser = subparsers.add_parser("check", help="Check if a number is currently blocked")
    check_parser.add_argument("number", help="Phone number or SIP ID to check")
    
    # clear
    subparsers.add_parser("clear", help="Clear all stored blocked numbers")
    
    args = parser.parse_args()
    
    if args.command == "list" or args.command is None:
        cmd_list(args)
    elif args.command == "add":
        cmd_add(args)
    elif args.command == "remove":
        cmd_remove(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "clear":
        cmd_clear(args)


if __name__ == "__main__":
    main()
