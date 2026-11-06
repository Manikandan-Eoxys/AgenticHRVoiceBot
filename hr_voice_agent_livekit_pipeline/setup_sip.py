"""
setup_sip.py

Creates/updates an Inbound SIP Trunk and Dispatch Rule on the local LiveKit server.
Configures authentication (user: 100, pass: 1234) so MicroSIP connects seamlessly.
"""

import asyncio
from livekit import api
from config import LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, WORKER_AGENT_NAME


async def main():
    print(f"Connecting to LiveKit server at {LIVEKIT_URL}...")
    lk = api.LiveKitAPI(
        url=LIVEKIT_URL,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )

    try:
        # Delete old trunks/rules to start fresh
        trunks = await lk.sip.list_sip_inbound_trunk(api.ListSIPInboundTrunkRequest())
        for t in trunks.items:
            print(f"Removing old trunk: {t.sip_trunk_id}")
            await lk.sip.delete_sip_trunk(api.DeleteSIPTrunkRequest(sip_trunk_id=t.sip_trunk_id))

        rules = await lk.sip.list_sip_dispatch_rule(api.ListSIPDispatchRuleRequest())
        for r in rules.items:
            print(f"Removing old dispatch rule: {r.sip_dispatch_rule_id}")
            await lk.sip.delete_sip_dispatch_rule(api.DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=r.sip_dispatch_rule_id))

        # 1. Create Inbound SIP Trunk with Auth
        trunk = await lk.sip.create_sip_inbound_trunk(
            api.CreateSIPInboundTrunkRequest(
                trunk=api.SIPInboundTrunkInfo(
                    name="microsip-trunk",
                    numbers=["100", "101", "800", "*"],
                    allowed_addresses=[],  # Allow call from any IP
                    auth_username="100",
                    auth_password="1234",
                )
            )
        )
        trunk_id = trunk.sip_trunk_id
        print(f"✅ Created Inbound SIP Trunk: {trunk_id}")

        # 2. Create SIP Dispatch Rule — Direct to fixed shared room "hr-sip-live"
        #    Using SIPDispatchRuleDirect so every MicroSIP call lands in the same
        #    well-known room that the browser UI can also join (SIP mode).
        #    This is what makes the transcript visible in the UI.
        SIP_ROOM = "hr-sip-live"
        rule = await lk.sip.create_sip_dispatch_rule(
            api.CreateSIPDispatchRuleRequest(
                name="hr-dispatch-rule",
                rule=api.SIPDispatchRule(
                    dispatch_rule_direct=api.SIPDispatchRuleDirect(
                        room_name=SIP_ROOM,
                        pin="",          # no PIN required
                    )
                ),
                room_config=api.RoomConfiguration(
                    agents=[api.RoomAgentDispatch(agent_name=WORKER_AGENT_NAME)]
                ),
                trunk_ids=[trunk_id],
            )
        )
        print(f"✅ Created SIP Dispatch Rule (Direct → {SIP_ROOM}): {rule.sip_dispatch_rule_id}")

        print("\n🎉 LiveKit SIP Ingress Configured!")
        print("--------------------------------------------------")
        print("MicroSIP Account Settings (Windows):")
        print("  SIP Server / Domain: 192.168.35.128:5060")
        print("  Username / Login:    100")
        print("  Password:            1234")
        print("--------------------------------------------------")

    except Exception as e:
        print(f"❌ Error setting up SIP: {e}")
    finally:
        await lk.aclose()


if __name__ == "__main__":
    asyncio.run(main())
