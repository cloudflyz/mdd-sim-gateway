# Switching the production host to this fork

Operator runbook for moving a live MDD Sim Gateway install from the
"upstream tree + out-of-tree patch bundle" arrangement to this fork.

Written 2026-10-25 for host `mdd-sim` (192.168.5.241). Nothing here is executed by
an agent without an explicit go-ahead.

## Why

The deployed host currently runs an upstream checkout whose working tree carries
141 uncommitted changes, restored after every upgrade by
`/root/mdd-custom/restore-all.sh`. That works, but:

- `git HEAD` is not a usable diff base (it lags the tarball that overwrote the tree);
- every upgrade needs a patch-bundle run plus manual judgement about which patch
  is already applied;
- the engine image has to be rebuilt by hand and the container re-created by hand.

This fork keeps the same changes as normal commits, so an upgrade becomes
`git merge upstream/main`, and the engine image build is the only manual step.

## Key facts about the current host

| Item | Value |
| --- | --- |
| deploy mode | `local` (control plane native + systemd, engine in Docker) |
| code dir | `/root/mdd-sim-gateway` |
| data dir | `/root/mdd-sim-gateway/data` (218 MB) |
| data dir state file | `/etc/mdd-sim-gateway/data-dir` → `/root/mdd-sim-gateway/data` |
| services | `mdd-sim-gateway-control`, `mdd-sim-gateway-orchestrator` |
| engine container | `mdd-sim-gateway-engine-1` |
| auto-update | `update_mode: notify` (do **not** press update on this host) |

`install.sh` resolves the data directory in this order:

1. `MDD_DATA_DIR` from the environment, if set;
2. otherwise the path recorded in `/etc/mdd-sim-gateway/data-dir`;
3. otherwise `<repo>/data`.

Because step 2 exists, **installing the code into a new directory does not move
or recreate the data** — it keeps pointing at the existing one.

## Data that must survive

`data/` is gitignored and never part of a code switch:

- `config.yaml` (line config, dtmf_mode, notification channels) + its backups
- `auth.json`, `clients.json`, `certs/`, `esim-chip-cache.json`
- `mdd-sim-gateway.sqlite` (calls, messages, binary_sms, line_states, contacts)
- `instances/` (engine event logs), `media/`, `notifications/`, `uploads/`
- `backups/`, `audit/`, `lpac/`, `tools/`

### Baseline to compare after the switch

Captured 2026-10-25 00:29 on the live DB:

```
calls 41 · messages 5 · binary_sms 2 · line_states 16
message_identities 5 · message_reads 3 · line_keepalive 2
```

## Pre-flight

```sh
# 1. full copy of the data dir, outside the deploy tree
ts=$(date +%Y%m%d-%H%M%S)
tar -C /root/mdd-sim-gateway -czf /root/backups/mdd-data-$ts.tar.gz data
ls -lh /root/backups/mdd-data-$ts.tar.gz

# 2. record the code state, for the rollback path
cp -a /root/mdd-sim-gateway /root/mdd-sim-gateway.pre-fork-$ts   # or at least: keep the dir as-is

# 3. record the baseline numbers
python3 - <<'PY'
import sqlite3
c = sqlite3.connect('/root/mdd-sim-gateway/data/mdd-sim-gateway.sqlite')
for t in ('calls','messages','binary_sms','line_states','message_identities','message_reads'):
    print(t, c.execute('select count(*) from %s' % t).fetchone()[0])
PY
```

Note the current `timer`/`line` state too: `docker exec mdd-sim-gateway-engine-1
asterisk -rx 'pjsip show registrations'` should read `Registered`.

## Switch

Run on the gateway host unless stated otherwise.

```sh
# 0. gateway: export the fork as a clean tree (no .git, no credentials)
cd /root/mdd-sim-gateway-fork
git archive --format=tar.gz --output=/tmp/mdd-fork-$(git rev-parse --short HEAD).tar.gz selfuse

# 1. copy it to the production host
scp /tmp/mdd-fork-*.tar.gz mdd:/root/

# 2. on mdd: unpack next to, never on top of, the current tree
ssh mdd
mkdir -p /root/mdd-sim-gateway-fork
tar -xzf /root/mdd-fork-*.tar.gz -C /root/mdd-sim-gateway-fork

# 3. point the installer at the EXISTING data dir explicitly.
#    (The state file already says this; passing it again makes the intent obvious
#     and protects against the state file being reset by an earlier install.)
cd /root/mdd-sim-gateway-fork
MDD_DATA_DIR=/root/mdd-sim-gateway/data ./install.sh install --mode local

# 4. per-line DTMF transport (the fork no longer hardcodes it)
#    In data/config.yaml, on the 3HK line:
#        line:
#          sip:
#            dtmf_mode: info
#    Then let the control plane re-render and restart the engine:
systemctl restart mdd-sim-gateway-control
docker rm -f mdd-sim-gateway-engine-1      # control re-creates it on startup
systemctl restart mdd-sim-gateway-control
```

Why the container is re-created deliberately: `systemctl restart
mdd-sim-gateway-orchestrator` does **not** replace the engine container, and a
running container can keep the previous image even after a new one is built.
Always verify the image id afterwards.

## Verification (all of it, not a subset)

```sh
# services
systemctl is-active mdd-sim-gateway-control mdd-sim-gateway-orchestrator

# engine container really is the new image
docker inspect mdd-sim-gateway-engine-1 --format '{{.Image}}'
docker inspect mdd-sim-gateway/engine:latest --format '{{.Id}}'

# compiled patches present
docker exec mdd-sim-gateway-engine-1 grep -c 'skipping sec-agree' \
  /usr/lib/asterisk/modules/res_pjsip_outbound_registration.so
docker exec mdd-sim-gateway-engine-1 grep -c 'remote-negotiated payload' \
  /usr/lib/asterisk/modules/res_rtp_asterisk.so

# IMS
docker exec mdd-sim-gateway-engine-1 asterisk -rx 'pjsip show registrations'

# DTMF transport actually applied
docker exec mdd-sim-gateway-engine-1 asterisk -rx 'pjsip show endpoint volte_ims' | grep dtmf_mode

# LAN RTP pin (never 10/8)
docker exec mdd-sim-gateway-engine-1 ip route show | grep -E '192\.168|172\.16'
docker exec mdd-sim-gateway-engine-1 ip route show | grep -c '^10\.0\.0\.0/8'

# data intact
python3 - <<'PY'
import sqlite3
c = sqlite3.connect('/root/mdd-sim-gateway/data/mdd-sim-gateway.sqlite')
for t in ('calls','messages','binary_sms','line_states','message_identities','message_reads'):
    print(t, c.execute('select count(*) from %s' % t).fetchone()[0])
PY
```

Then functional checks, in this order:

1. one outgoing call, held past 60 s, audio both ways
2. one DTMF test against an IVR (language switch is the clearest signal)
3. one incoming SMS, and one outgoing SMS
4. WebUI loads, existing call/SMS history is visible

## Rollback

Any failure before the old tree is removed is a one-minute revert:

```sh
cd /root/mdd-sim-gateway          # the untouched old checkout
MDD_DATA_DIR=/root/mdd-sim-gateway/data ./install.sh install --mode local
docker rm -f mdd-sim-gateway-engine-1
systemctl restart mdd-sim-gateway-control
```

If the database itself looks wrong (it should not — nothing in the switch writes
to it), restore the pre-flight copy:

```sh
systemctl stop mdd-sim-gateway-control mdd-sim-gateway-orchestrator
tar -C /root -xzf /root/backups/mdd-data-<ts>.tar.gz   # restores /root/mdd-sim-gateway/data
systemctl start mdd-sim-gateway-control
```

Keep `/root/mdd-sim-gateway` and the tar until the fork has run clean for a few
days.

## Do not

- **Do not press "update" in the WebUI.** The built-in updater downloads the
  upstream release and overlays the checkout; on this host it would replace the
  fork tree. Keep `update_mode: notify` and update by merging `upstream/main`
  instead.
- **Do not run `install.sh uninstall --purge`.** It deletes the data directory.
- **Do not `git clean` / `git checkout` inside the old tree** — its HEAD predates
  the tarball that overwrote it, so git's view is wrong.
- **Do not keep the old patch bundle as the primary mechanism.** After the fork
  is proven, retire `restore-all.sh` so there are not two sources of truth. Keep
  it around (unused) for one upgrade cycle.

## After it has proven itself

1. delete `/root/mdd-custom`, `/root/mdd-3hk-patches`, `/root/mdd-bark-patches`,
   `/root/mdd-sms-code-patches` (their content now lives in the fork)
2. keep `/root/mdd-pristine` for quick diffs against upstream tags
3. to upgrade: on the gateway `git fetch upstream --tags && git merge upstream/main`,
   run `gitleaks git . --config .gitleaks.toml`, export, and repeat the switch
   section with `install.sh reload`
