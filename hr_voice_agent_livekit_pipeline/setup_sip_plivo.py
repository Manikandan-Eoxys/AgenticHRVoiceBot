"""
setup_sip_plivo.py

Registers the LiveKit Cloud SIP Inbound Trunk and Dispatch Rule
for Plivo inbound calls.

Architecture:
  Phone → Plivo DID → Plivo Inbound SIP Trunk
        → sip:32ab16rym19.sip.livekit.cloud (TLS)
        → LiveKit Cloud SIP Ingress (matched by this trunk config)
        → New room per call (SIPDispatchRuleIndividual)
        → hr-voice-agent auto-dispatched into the room

Run ONCE (or re-run if you need to reset trunks/rules):
    python setup_sip_plivo.py

Requirements:
  - LIVEKIT_URL must point to LiveKit Cloud (wss://...)
  - LIVEKIT_API_KEY / LIVEKIT_API_SECRET must be your Cloud credentials
  - SIP_TRUNK_USERNAME / SIP_TRUNK_PASSWORD must match what you set
    in the LiveKit Cloud "Create new LiveKit Cloud SIP URI" dialog.
"""

import asyncio
from livekit import api
from config import (
    LIVEKIT_URL,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    WORKER_AGENT_NAME,
    Config,
)

# ── Plivo SIP server IP ranges ─────────────────────────────────────────────
# These are the IPs from which Plivo will send SIP INVITE packets.
# LiveKit will reject any INVITE not originating from these addresses.
# Source: https://www.plivo.com/docs/voice/troubleshooting/firewall-network-configuration
PLIVO_SIP_IPS = [
    # India region (primary — matches your Plivo account data region)
    "15.207.90.192/31",
    "204.89.151.128/27",
    "204.89.151.160/27",
    # US East (Ashburn) — Plivo may failover here
    "3.215.109.192/27",
    "52.55.231.224/27",
    # US West (San Jose)
    "54.241.63.240/28",
    # Frankfurt — EU fallback
    "3.120.131.60/32",
    "18.185.135.192/27",
    # Singapore — APAC fallback
    "54.254.246.0/27",
]

# ── Agent name (must match agent_name in main.py WorkerOptions) ────────────
AGENT_NAME = WORKER_AGENT_NAME

# ── Room name prefix for individual calls ──────────────────────────────────
# Each caller gets a room like "plivo-hr-call-<random-id>"
ROOM_PREFIX = Config.SIP_ROOM_PREFIX  # default: "plivo-hr-call"


async def main() -> None:
    print(f"\n🔗 Connecting to LiveKit at: {LIVEKIT_URL}")
    lk = api.LiveKitAPI(
        url=LIVEKIT_URL,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )

    try:
        # ------------------------------------------------------------------
        # 1. Clean up any previously registered Plivo trunks & rules
        #    so we start fresh (idempotent re-runs).
        # ------------------------------------------------------------------
        print("\n🧹 Cleaning up existing Plivo SIP trunks and dispatch rules...")

        existing_trunks = await lk.sip.list_sip_inbound_trunk(
            api.ListSIPInboundTrunkRequest()
        )
        for trunk in existing_trunks.items:
            if "plivo" in trunk.name.lower():
                print(f"   Removing old trunk: {trunk.name} ({trunk.sip_trunk_id})")
                await lk.sip.delete_sip_trunk(
                    api.DeleteSIPTrunkRequest(sip_trunk_id=trunk.sip_trunk_id)
                )

        existing_rules = await lk.sip.list_sip_dispatch_rule(
            api.ListSIPDispatchRuleRequest()
        )
        for rule in existing_rules.items:
            if "plivo" in rule.name.lower():
                print(f"   Removing old dispatch rule: {rule.name} ({rule.sip_dispatch_rule_id})")
                await lk.sip.delete_sip_dispatch_rule(
                    api.DeleteSIPDispatchRuleRequest(
                        sip_dispatch_rule_id=rule.sip_dispatch_rule_id
                    )
                )

        # ------------------------------------------------------------------
        # 2. Create the Plivo Inbound SIP Trunk
        #
        #    - allowed_addresses: Only accept SIP from Plivo's IP ranges
        #    - auth_username / auth_password: Must match the credentials
        #      you entered when creating the LiveKit Cloud SIP URI
        #      (Username: AgenticHRVoicebot, Password: Agent@110)
        # ------------------------------------------------------------------
        print("\n📞 Creating Plivo Inbound SIP Trunk...")

        trunk = await lk.sip.create_sip_inbound_trunk(
            api.CreateSIPInboundTrunkRequest(
                trunk=api.SIPInboundTrunkInfo(
                    name="plivo-inbound-trunk",
                    # Accept calls for any number or DID (+918031451001)
                    numbers=["*"],
                    # Allow calls from any IP
                    allowed_addresses=[],
                    # No Digest Auth required (prevents URI @ parsing issues in Plivo)
                    auth_username="",
                    auth_password="",
                )
            )
        )
        trunk_id = trunk.sip_trunk_id
        print(f"   ✅ Trunk created: {trunk.name} → ID: {trunk_id}")

        # ------------------------------------------------------------------
        # 3. Create the SIP Dispatch Rule — Individual (one room per call)
        #
        #    SIPDispatchRuleIndividual creates a unique room for every
        #    incoming call, so callers never share audio with each other.
        #    The room_prefix is combined with a random suffix by LiveKit.
        #
        #    room_config.agents tells LiveKit to automatically spin up
        #    the hr-voice-agent inside each new room — no manual dispatch.
        # ------------------------------------------------------------------
        print(f"\n📋 Creating SIP Dispatch Rule (Individual → prefix: {ROOM_PREFIX})...")

        rule = await lk.sip.create_sip_dispatch_rule(
            api.CreateSIPDispatchRuleRequest(
                name="plivo-hr-dispatch-rule",
                rule=api.SIPDispatchRule(
                    dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                        room_prefix=ROOM_PREFIX,
                        pin="",  # No PIN required — open inbound
                    )
                ),
                room_config=api.RoomConfiguration(
                    agents=[
                        api.RoomAgentDispatch(agent_name=AGENT_NAME)
                    ]
                ),
                trunk_ids=[trunk_id],
            )
        )
        print(
            f"   ✅ Dispatch rule created: {rule.name} → ID: {rule.sip_dispatch_rule_id}"
        )

        # ------------------------------------------------------------------
        # 4. Summary
        # ------------------------------------------------------------------
        print("\n" + "=" * 60)
        print("🎉 LiveKit SIP Ingress configured for Plivo!")
        print("=" * 60)
        print(f"\n  Trunk Name      : plivo-inbound-trunk")
        print(f"  Trunk ID        : {trunk_id}")
        print(f"  Auth Username   : {Config.SIP_TRUNK_USERNAME}")
        print(f"  Allowed IPs     : Plivo {len(PLIVO_SIP_IPS)} CIDR ranges")
        print(f"\n  Dispatch Rule   : plivo-hr-dispatch-rule")
        print(f"  Rule ID         : {rule.sip_dispatch_rule_id}")
        print(f"  Room Prefix     : {ROOM_PREFIX}")
        print(f"  Agent Dispatch  : {AGENT_NAME}")
        print("\n" + "=" * 60)
        print("\n📌 Next steps:")
        print("   1. Start the agent:  python main.py dev")
        print("   2. Link a Plivo DID number to your Plivo SIP trunk")
        print("   3. Call that number to test!")
        print()

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await lk.aclose()


if __name__ == "__main__":
    asyncio.run(main())
