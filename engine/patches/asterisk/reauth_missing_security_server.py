"""Answer a 401 challenge that arrives without a Security-Server header.

Original: T-Mobile US re-challenges a mid-session re-REGISTER with a 401 that carries no
Security-Server header. Extended for 3HK: the initial registration 401 also lacks
Security-Server (non-standard). The sysmocom VoLTE code treats every 401 as an SA
negotiation, so the missing header fails the exchange.

Insert a fallback in handle_volte_unauthorized(): when the Security-Server header is
genuinely ABSENT (regardless of registration state), skip sec-agree negotiation and go
straight to VOLTE_STATE_RESPONSE so ast_sip_create_request_with_auth() answers the
challenge over the existing transport.
"""

import os
import sys
from pathlib import Path


SOURCE = Path(os.environ.get("AST_SRC", "/home/asterisk-build/asterisk")) \
    / "res/res_pjsip_outbound_registration.c"

MARKER = "PATCH reauth_missing_security_server"

ANCHOR = (
    '\t/* Get and select best candidate from "Security-Server" header. */\n'
    "\tif (volte_get_security_server(transport_state, response->rdata, &sec)) {\n"
)

FALLBACK = (
    "\t/* " + MARKER + ": a 401 without Security-Server. 3HK and T-Mobile US both omit\n"
    "\t * Security-Server on registration challenges.  Skip sec-agree negotiation and\n"
    "\t * answer the challenge directly over the tunnel transport.  This covers both\n"
    "\t * initial registration (no established SAs) and mid-session re-auth. */\n"
    "\t{\n"
    '\t\tstatic const pj_str_t str_security_server = { "Security-Server", 15 };\n'
    "\n"
    "\t\tif (!pjsip_msg_find_hdr_by_name(response->rdata->msg_info.msg, &str_security_server, NULL)) {\n"
    '\t\t\tast_log(LOG_NOTICE, "401 without Security-Server; skipping sec-agree, "\n'
    '\t\t\t\t\t"answering challenge directly over the tunnel.\\n");\n'
    "\t\t\tvolte_set_state(response->client_state, VOLTE_STATE_RESPONSE);\n"
    "\t\t\tret = 0;\n"
    "\t\t\tgoto out;\n"
    "\t\t}\n"
    "\t}\n"
    "\n"
)


def patch(source: str) -> str:
    if MARKER in source:
        return source

    fn_start = source.find("static int handle_volte_unauthorized(")
    if fn_start < 0:
        raise ValueError("handle_volte_unauthorized not found")

    anchor_at = source.find(ANCHOR, fn_start)
    if anchor_at < 0:
        raise ValueError("Security-Server anchor not found in handle_volte_unauthorized")
    if source.find(ANCHOR, anchor_at + 1) >= 0:
        raise ValueError("Security-Server anchor is not unique")

    return source[:anchor_at] + FALLBACK + source[anchor_at:]


try:
    original = SOURCE.read_text()
    updated = patch(original)
except (OSError, ValueError) as exc:
    print(f"reauth missing-Security-Server patch failed: {exc}", file=sys.stderr)
    raise SystemExit(1) from exc

if updated == original:
    print("re-auth without Security-Server already patched")
else:
    SOURCE.write_text(updated)
    print("patched handle_volte_unauthorized to skip sec-agree "
          "when a 401 has no Security-Server header (initial + re-auth)")
