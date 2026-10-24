# Local changes in this fork

Self-use fork of [MddIdd/mdd-sim-gateway](https://github.com/MddIdd/mdd-sim-gateway).
No pull requests are sent upstream. Changes are kept as ordinary commits on top of
upstream tags so `git merge upstream/main` stays clean.

Baseline: upstream **v1.14.0**.

## 1. Engine: 3HK VoWiFi (`feat(engine)`)

| File | Change |
| --- | --- |
| `engine/patches/asterisk/reauth_missing_security_server.py` | restore the patch: the 3HK P-CSCF omits `Security-Server` on its 401, registration otherwise aborts |
| `engine/patches/asterisk/dtmf_sample_rate_fallback.py` | new: send DTMF with the remote-negotiated telephone-event payload type/rate |
| `engine/swu_ike.py` | pin RFC1918 destinations to the LAN gateway (never `10.0.0.0/8`) |
| `engine/templates/pjsip.conf.j2` | `sec_encryption=no`; DTMF transport now configurable per line |

Why the LAN pin: upstream v1.14.0 replaced the CIDR pin with a source-based
policy rule, which only fixes **reply** traffic. Container-**originated** RTP
toward a LAN browser still took the `/1` tunnel route (the source is not chosen
yet at lookup time), so the browser received no audio and the carrier cleared the
call after ~15 s with `BYE ... Reason: SIP;cause=200;text="RTP Timeout"`.
Evidence: `Sent RTP packet to <browser>` count was 0 before the fix and >1500
during a working call.

## 2. Notifications: Bark + SMS code (`feat(notify)`)

Adds a Bark push channel (with per-message random IV; a fixed IV would leak
plaintext-prefix equality under CBC) and verification-code extraction that puts
the code in the notification title and in Bark's `copy` field.

## 3. DTMF transport as a line setting (`refactor(dtmf)`)

`dtmf_mode` is no longer hardcoded. `control/app/config.py` normalizes it
(`rfc4733|info|inband|auto`, anything else → empty = Asterisk default) and
`engine/render.py` exposes it to the template:

```yaml
line:
  sip:
    dtmf_mode: info   # 3HK; omit for the PJSIP default (rfc4733)
```

For this 3HK line the value **must** be `info`: the P-CSCF ignores the RFC4733
telephone-event we send even when 101/8000 is negotiated (30 DTMF packets
reached the carrier media port, IVR stayed silent). With SIP INFO the IVR reacts
immediately.

## Deployment note

After pulling this fork onto an existing install, set `dtmf_mode: info` on the
affected line in `data/config.yaml` and restart the control plane so the engine
re-renders `pjsip.conf`. Nothing else needs manual editing.

## Verification log (live, 3HK mcc454/mnc003, Oct 2026)

- `pjsip show registrations` → `Registered`
- `rtp set debug on` → bidirectional AMR RTP with `10.5.x`, plus
  `telephone-event` in both directions
- calls held past 60 s; hangup reason `User Triggered`
- DTMF accepted by an IVR after switching to SIP INFO
- `restore-all.sh --check` (local patch bundle) → all green
