# Coordinator transfer (user ↔ bot's Plivo number ↔ coordinator)

```
 caller's phone ──► Plivo number 918031451001 ──► Asterisk (this server) ──► Plivo outbound trunk ──► coordinator's phone
                                                   │  bridge: caller + coordinator (AI leg removed)
                                                   └─ Python (asterisk/transfer.py) supervises both legs
```

## What happens (default `TRANSFER_MODE=bridge`)
1. Caller verifies, says *"talk to the coordinator"*, confirms → bot says *"Please hold…"* (fully played).
2. A **ring tone** is played to the caller while the coordinator's phone rings (the caller never leaves the Stasis app).
3. ARI originate `PJSIP/918925355704@plivo-outbound`, caller ID `"HR Voicebot" <918031451001>`.
4. Coordinator answers → the session is flagged *transferred* **first**, then the coordinator joins the caller's bridge and the AI's AudioSocket leg is removed and hung up.
5. Either person hangs up → the other leg + the bridge are torn down.
6. Busy / reject / no answer (`COORDINATOR_DIAL_TIMEOUT_SECONDS`) → the caller is still with the AI, which offers an HR ticket.

Web-demo calls (browser) use the same flow through `webcall/bridge.py` (unchanged).

## `.env`
| Variable | Default | Meaning |
|---|---|---|
| `COORDINATOR_PHONE_NUMBER` | `+918925355704` | who to transfer to |
| `COORDINATOR_DIAL_TIMEOUT_SECONDS` | `30` | how long the coordinator's phone rings |
| `OUTBOUND_TRUNK_ENDPOINT` | `plivo-outbound` | PJSIP endpoint of the trunk |
| `OUTBOUND_CALLER_ID` | – (**required**) | `918031451001` – must be a number on your Plivo account |
| `OUTBOUND_CALLER_NAME` | `HR Voicebot` | display name |
| `OUTBOUND_ALLOWED_PREFIXES` | `91` | the coordinator number must start with one of these |
| `OUTBOUND_DIAL_PLUS` | `0` | send `+91…` instead of `91…` to the trunk |
| `TRANSFER_RINGBACK` | `1` | ring tone to the caller while dialing |
| `TRANSFER_MODE` | `bridge` | `bridge` (recommended) or `dialplan` (see below) |

## If the coordinator answers and the call still drops — find out WHO hangs up (2 minutes)
The agent log now names every hang-up it performs:

```
✂️ [ARI] We hang up channel <id> - <reason>        <- WE did it (a bug / race in our code, the reason says which)
🔔 [ARI] Hangup requested on <id> cause=16 ...      <- with NO "We hang up" line before it: the FAR END sent a BYE
🔕 [ARI] Channel destroyed: <id> cause=...
```

If the far end hung up, capture the SIP:
```bash
sudo bash hr_voice_agent_asterisk_pipeline/scripts/diagnose_transfer.sh
# place the test call, answer on the coordinator phone, wait for the drop, Ctrl-C
```
It prints each INVITE/200/ACK/BYE/UPDATE with direction and Reason header, then a verdict.

| Verdict | Meaning / fix |
|---|---|
| `BYE sent by Plivo` seconds after `200 OK`, **no ACK from Asterisk** | Plivo never got the ACK → `external_signaling_address/port` in `pjsip.conf` wrong, or UDP 5080 not forwarded to this machine |
| `BYE sent by Plivo`, ACK present, no RTP seen (`rtp set debug on`) | RTP not reaching Plivo / Plivo's RTP not reaching you → forward **UDP 10000-20000** to this machine; `external_media_address` = your current public IP |
| `mid-call UPDATE/INVITE` followed by a BYE | session-timer refresh Plivo does not answer → `timers=no` on `plivo-outbound` (already in this `pjsip.conf` – make sure it was installed) |
| `BYE sent by ASTERISK` with no matching "We hang up" line | Asterisk itself (RTP timeout / refresh failure) – send me the trace |
| `INVITE rejected: 403/407` | your public IP is not in the Plivo trunk IP-ACL |
| `INVITE rejected: 404/480/484` | number format (`OUTBOUND_DIAL_PLUS=1`) or caller ID not owned by your Plivo account |

Remember: your public IP (`115.99.235.223`) changes on most home/office lines. When it changes, update
`external_media_address` / `external_signaling_address` (twice each) **and** the Plivo trunk ACL.

### Quick isolation test – does the trunk itself drop calls?
```bash
python scripts/call_out.py +91XXXXXXXXXX        # the bot calls your mobile directly (no transfer code involved)
```
If *this* call also drops 3 s after you answer, the problem is SIP/RTP/trunk (table above), not the transfer code.

### `TRANSFER_MODE=dialplan`
Hands the caller to `[hr-voicebot-outbound]` (`Dial(PJSIP/<n>@plivo-outbound)`). Use it only to compare behaviour:
no ring tone from us, no return-to-AI if the coordinator does not answer. The StasisEnd that this causes is now ignored
(it used to tear the call down).

## Tests
`python -m pytest tests/test_transfer.py tests/test_web_transfer.py tests/test_outbound.py` – runs the full flow against a fake ARI server.
