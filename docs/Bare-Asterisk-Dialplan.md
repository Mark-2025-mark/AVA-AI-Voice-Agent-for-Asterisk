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

## Transfers

ERP Sync writes `tools.transfer.destinations` with `dialplan_context=ava-motostore` so receptionist `blind_transfer` dials `Local/7102@ava-motostore` (etc.).
