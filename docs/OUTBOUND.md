# Outbound calls: the HR bot phones people through Plivo (+91 80 3145 1001)

## 1. Plivo console (one time)
Zentrunk -> Outbound Trunks -> your trunk (`55329168785806490.zt.plivo.com`)
- **Authentication:** add your server's public IP (`115.99.235.223`) to the IP Access Control List.
  (Or create a credentials list and use `outbound_auth` in `pjsip.conf`.)
- **Caller ID:** `918031451001` must be a number on your Plivo account.
- India routes may carry extra rules (regulation, per-trunk limits). Ask Plivo support if calls are rejected.

## 2. Asterisk (one time)
```bash
sudo bash asterisk_config/install_asterisk_config.sh      # copies pjsip/extensions/ari/http and reloads
sudo asterisk -rx "pjsip show endpoint plivo-outbound"    # must exist
sudo asterisk -rx "pjsip show aor plivo-outbound-aor"     # contact = the Plivo termination URI
```
Router: UDP 5080 (SIP) and RTP 10000-20000 forwarded to the Asterisk host (same as for inbound).
Change the ARI password in `ari.conf` AND `ARI_PASSWORD` in `.env`.

## 3. `.env` (project root) - add
```
OUTBOUND_ENABLED=1
OUTBOUND_CALLER_ID=918031451001
OUTBOUND_TRUNK_ENDPOINT=plivo-outbound
OUTBOUND_DEFAULT_COUNTRY_CODE=91
OUTBOUND_ALLOWED_PREFIXES=91
OUTBOUND_MAX_CALLS=2
OUTBOUND_RING_TIMEOUT=30
OUTBOUND_DIAL_PLUS=0
OUTBOUND_API_HOST=127.0.0.1
OUTBOUND_API_PORT=8090
OUTBOUND_API_KEY=<generate: python3 -c "import secrets;print(secrets.token_urlsafe(32))">
```

## 4. Run and test
```bash
cd hr_voice_agent_asterisk_pipeline
./run_webcall.sh                                           # starts agent.py (inbound + web call + outbound API)
python scripts/call_out.py +91XXXXXXXXXX                   # call YOUR OWN mobile first
python scripts/call_out.py +91XXXXXXXXXX --type leave_verification --employee 1007
```
Watch SIP while testing: `sudo asterisk -rx "pjsip set logger on"` then `sudo asterisk -rvvv`.

## Call types
| type | prompt | needs |
|---|---|---|
| `assistant` | normal HR assistant, introduces itself | number |
| `leave_verification` | verifies pending leave, asks reason | employee ID |
| `scheduled_reminder` / `policy_acknowledgement` | delivers `--reminder`, records acknowledgement | employee ID |

## Troubleshooting
| Symptom | Likely cause |
|---|---|
| Agent log `Asterisk refused the call (4xx)` | ARI user/password mismatch or endpoint name wrong |
| SIP log shows `403 Forbidden` / `407` from Plivo | public IP not in Plivo ACL (or credentials missing) |
| Call connects then drops a few seconds after the coordinator answers | `docs/COORDINATOR_TRANSFER.md` (SIP trace + verdict table) |
| `404` / `480` / `484` from Plivo | number format: try `OUTBOUND_DIAL_PLUS=1`, or caller ID not owned |
| Rings but no voice either way | RTP ports not forwarded / `external_media_address` wrong |
| `not answered (busy / rejected / no answer ...)` in agent log | callee side; check the SIP log for the reason |

## Coordinator transfer (caller -> human at +91 8925355704)
See **docs/COORDINATOR_TRANSFER.md** (flow, settings, and how to find out who hangs up if a transferred call drops).
Works for phone calls and web-demo calls. Config files to install: `sudo bash asterisk_config/install_asterisk_config.sh`.
