# Bare Asterisk + AVA (no FreePBX)

Motostore / Centralita deploys Asterisk and AVA on the **same** host. Do **not** use FreePBX `from-internal` / `extensions_custom.conf`, and do **not** change the separate FS PBX server for this flow.

## Multi-agent context

```asterisk
; /etc/asterisk/extensions.conf  (or #include "extensions_ava.conf")
[ava-motostore]
exten => 7101,1,NoOp(Recepcionista Motostore)
 same => n,Set(AI_AGENT=recepcionista_motostore)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()

exten => 7102,1,NoOp(Ventas Motostore)
 same => n,Set(AI_AGENT=ventas_motostore)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()

exten => 7103,1,NoOp(Postventa Motostore)
 same => n,Set(AI_AGENT=postventa_motostore)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()

; Inbound DID → Goto(ava-motostore,7101,1)
```

Then: `asterisk -rx 'dialplan reload'`

## Snippet API

`GET /api/agents/{slug}/dialplan?context=ava-motostore` returns the stanza for that agent’s extension.

## Transfers (agent → agent)

ERP Sync writes `tools.transfer.destinations` with `type: extension`, `target: 7102|7103`, and
`dialplan_context` matching the context above (`ava-motostore` or `from-fspbx`).

`blind_transfer` uses ARI `continue_in_dialplan` (not SIP originate). The same Asterisk
`channel_id` leaves Stasis, runs `Set(AI_AGENT=…)` on the destination extension, and
re-enters Stasis. AVA keeps the `CallSession`, stops the previous provider, rebinds
tools/prompt to the new Agent slug, and starts a new provider session.

You do **not** need PJSIP endpoints for 7101/7102/7103 for this path. Those dialplan
extensions are enough.

## Deploy / update this fork on Debian

**Run these from the git checkout**, not from `/root`. On Motostore AVA the tree is:

`/usr/local/src/AVA-AI-Voice-Agent-for-Asterisk`

(`agent check` shows mounts from that path). If you run `agent update` / `git` from `~`,
you get `fatal: no es un repositorio git`.

```bash
cd /usr/local/src/AVA-AI-Voice-Agent-for-Asterisk

# Prefer the maintained updater when pointing at your fork/ref:
agent update --ref cursor/voice-agent-ava-handoff-e987 --include-ui --local-changes=retain

# Or pull the fork branch and recreate engine + UI:
git fetch origin
git checkout cursor/voice-agent-ava-handoff-e987
git pull --ff-only origin cursor/voice-agent-ava-handoff-e987
docker compose -p asterisk-ai-voice-agent up -d --build --force-recreate ai_engine admin_ui
agent check
```

After deploy, place a test call: recepcionista → “pásame con ventas” → confirm ventas
greets (logs should show `Agent handoff re-entry` / `Agent handoff rebound`, not
`Caller already in progress` alone).
