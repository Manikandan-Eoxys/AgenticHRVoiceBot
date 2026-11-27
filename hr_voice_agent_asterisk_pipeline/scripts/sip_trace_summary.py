#!/usr/bin/env python3
"""scripts/sip_trace_summary.py - answer ONE question: "who ended the call, and why?"

Usage
  1. Capture:   sudo asterisk -rx "pjsip set logger on"
                sudo asterisk -rvvvvv | tee /tmp/sip_trace.log        # place the failing test call, Ctrl-C
  2. Analyse:   python3 scripts/sip_trace_summary.py /tmp/sip_trace.log

It lists every SIP request/response that matters (INVITE, 180/183/200, ACK, BYE, CANCEL, re-INVITE, UPDATE, 4xx-6xx)
with its DIRECTION (RECEIVED from Plivo / SENT by Asterisk), the peer, the Call-ID and any Reason / Warning header,
then prints a verdict for each call.
"""
import re
import sys
from collections import OrderedDict

HDR = re.compile(r"<---\s*(Received|Transmitting)\s+SIP\s+(request|response)\s*\(\d+ bytes\)\s*(?:from|to)\s+(\S+)\s*--->")
START = re.compile(r"^(?:SIP/2\.0\s+(\d{3})\s*(.*)|([A-Z]+)\s+sip:\S+\s+SIP/2\.0)")


def parse(path):
    msgs, cur = [], None
    with open(path, errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            m = HDR.search(line)
            if m:
                if cur:
                    msgs.append(cur)
                cur = {"dir": "RECEIVED" if m.group(1) == "Received" else "SENT", "peer": m.group(3), "lines": [], "first": None}
                continue
            if cur is not None:
                if line.strip() == "" and cur["first"] is not None and cur["lines"] and cur.get("body_started"):
                    pass
                if cur["first"] is None and line.strip():
                    sm = START.match(line.strip())
                    if sm:
                        cur["first"] = line.strip()
                        cur["status"] = int(sm.group(1)) if sm.group(1) else None
                        cur["method"] = sm.group(3) or None
                    continue
                cur["lines"].append(line)
        if cur:
            msgs.append(cur)
    return [m for m in msgs if m.get("first")]


def hdr(m, name):
    for l in m["lines"]:
        if l.lower().startswith(name.lower() + ":"):
            return l.split(":", 1)[1].strip()
    return ""


def main(path):
    calls = OrderedDict()
    for m in parse(path):
        cid = hdr(m, "Call-ID") or "?"
        cseq = hdr(m, "CSeq")
        calls.setdefault(cid, []).append((m, cseq))
    for cid, items in calls.items():
        print("=" * 100)
        print(f"Call-ID {cid}")
        verdict = []
        answered = False
        for m, cseq in items:
            label = m["first"][:70]
            extra = ""
            for h in ("Reason", "Warning", "Session-Expires", "Require", "Supported"):
                v = hdr(m, h)
                if v and (h in ("Reason", "Warning") or m.get("method") in ("INVITE", "UPDATE")):
                    extra += f"  [{h}: {v}]"
            print(f"  {m['dir']:9} {m['peer']:28} {label}{extra}   CSeq={cseq}")
            if m["status"] == 200 and "INVITE" in cseq:
                answered = True
            if m.get("method") == "BYE":
                who = "Plivo / the far end" if m["dir"] == "RECEIVED" else "ASTERISK (our side)"
                verdict.append(f"BYE sent by {who}" + (f" - {hdr(m, 'Reason')}" if hdr(m, 'Reason') else ""))
            if m.get("method") in ("UPDATE",) or (m.get("method") == "INVITE" and answered):
                verdict.append(f"mid-call {m['method']} {m['dir']}: a session-timer refresh / re-INVITE. If unanswered, the call is dropped")
            if m["status"] and m["status"] >= 400 and "INVITE" in cseq:
                verdict.append(f"INVITE rejected: {m['first']}")
        if not any("BYE" in v for v in verdict) and answered:
            verdict.append("call answered, no BYE in this capture (still up or capture ended early)")
        print("  VERDICT:" if verdict else "  (no verdict)")
        for v in verdict:
            print("   -", v)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
