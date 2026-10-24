"""Patch: Use remote-negotiated DTMF payload type for TX.

Some IMS P-CSCFs (3HK/Huawei) negotiate telephone-event with a different
payload type and sample rate than Asterisk offers. Asterisk offers 101/8000,
P-CSCF answers 103/16000. Asterisk always sends DTMF as type 101 (static
fallback), but P-CSCF only accepts type 103.

Fix: scan all 128 payload types via the public ast_rtp_codecs_get_payload()
API to find a remote-negotiated (RX) DTMF payload that differs from the TX
default, and use that instead.
"""

import os
import sys
from pathlib import Path

SOURCE = Path(os.environ.get("AST_SRC", "/home/asterisk-build/asterisk")) \
    / "res/res_rtp_asterisk.c"

MARKER = "PATCH dtmf_use_remote_payload"
ANCHOR = (
    "\t/* Grab the matching DTMF type payload */\n"
    "\tpayload = ast_rtp_codecs_payload_code_tx_sample_rate(ast_rtp_instance_get_codecs(instance), 0, NULL, AST_RTP_DTMF, sample_rate);\n"
    "\n"
    "\t/* If this returns -1, we are being asked to send digits for a sample rate that is outside\n"
    "\t   what was negotiated for. Fall back if possible. */\n"
    "\tif (payload == -1) {\n"
    "\t\treturn -1;\n"
    "\t}\n"
)

REPLACEMENT = (
    "\t/* Grab the matching DTMF type payload */\n"
    "\tpayload = ast_rtp_codecs_payload_code_tx_sample_rate(ast_rtp_instance_get_codecs(instance), 0, NULL, AST_RTP_DTMF, sample_rate);\n"
    "\n"
    "\t/* " + MARKER + ": Some IMS P-CSCFs (3HK/Huawei) answer with a different\n"
    "\t   * telephone-event payload type/rate than Asterisk offered (e.g. 103/16000\n"
    "\t   * instead of 101/8000). Asterisk always finds static PT 101 for TX, but the\n"
    "\t   * remote only accepts its own PT. Scan RX payloads for a DTMF type that\n"
    "\t   * differs from the TX default and prefer it. */\n"
    "\t{\n"
    "\t\tstruct ast_rtp_codecs *_codecs = ast_rtp_instance_get_codecs(instance);\n"
    "\t\tint _pt;\n"
    "\t\tfor (_pt = 96; _pt < 128; _pt++) {\n"
    "\t\t\tstruct ast_rtp_payload_type *_type = ast_rtp_codecs_get_payload(_codecs, _pt);\n"
    "\t\t\tif (_type && !_type->asterisk_format && _type->rtp_code == AST_RTP_DTMF\n"
    "\t\t\t    && _pt != payload) {\n"
    '\t\t\t\tast_log(LOG_NOTICE, "DTMF: using remote-negotiated payload %d instead of local %d\\n", _pt, payload);\n'
    "\t\t\t\tpayload = _pt;\n"
    "\t\t\t\tsample_rate = 8000;\n"
    "\t\t\t\tbreak;\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t}\n"
    "\n"
    "\t/* If this returns -1, fall back to any DTMF payload (sample_rate=0). */\n"
    "\tif (payload == -1) {\n"
    "\t\tpayload = ast_rtp_codecs_payload_code_tx_sample_rate(ast_rtp_instance_get_codecs(instance), 0, NULL, AST_RTP_DTMF, 0);\n"
    "\t\tif (payload == -1) {\n"
    "\t\t\treturn -1;\n"
    "\t\t}\n"
    '\t\t\tast_log(LOG_NOTICE, "DTMF: exact sample rate %d not negotiated, falling back to payload %d\\n", sample_rate, payload);\n'
    "\t\tsample_rate = 8000;\n"
    "\t}\n"
)


def patch(source: str) -> str:
    if MARKER in source:
        return source
    idx = source.find(ANCHOR)
    if idx < 0:
        raise ValueError("DTMF payload anchor not found")
    return source[:idx] + REPLACEMENT + source[idx + len(ANCHOR):]


try:
    original = SOURCE.read_text()
    updated = patch(original)
except (OSError, ValueError) as exc:
    print(f"DTMF remote payload patch failed: {exc}", file=sys.stderr)
    raise SystemExit(1) from exc

if updated == original:
    print("DTMF remote payload already patched")
else:
    SOURCE.write_text(updated)
    print("patched DTMF to prefer remote-negotiated payload type")
