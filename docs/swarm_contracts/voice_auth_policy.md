# Voice Authentication and Authorization Policy

_Last updated: 2026-05-26_

## Purpose

This contract defines how Sophia identifies speakers, how AssistX authorizes actions, and how response voice selection is handled.

Speaker identity, authorization, and TTS voice choice are separate decisions.

---

## Speaker states

```text
authenticated_scott
scott_voice_unverified
admin_override
registered_user_authenticated
registered_user_unverified
unknown_speaker
```

## Required runtime fields

Every Sophia quick-input event should include:

```yaml
speaker_identity: optional string
speaker_confidence: optional float
auth_state: string
auth_reason: string
auth_method: voiceprint | override | registered_voiceprint | none
session_id: string
capture_id: string
utterance_id: optional string
```

---

## Auth state rules

### `authenticated_scott`

Set when voiceprint score exceeds the accepted Scott threshold and sanity checks pass.

### `scott_voice_unverified`

Set when text/device/session suggests Scott, but the voiceprint score is below threshold or unavailable.

### `admin_override`

Set when Scott intentionally supplies an approved fallback verification factor after voice authentication is uncertain. The current planning label is `admin-voice`, but this is a placeholder label, not a real secret to commit into source code.

The implementation must store any real fallback credential outside git in environment variables, secret storage, or a local encrypted config.

### `registered_user_authenticated`

Set when the speaker is recognized as a registered non-Scott user.

### `registered_user_unverified`

Set when a known user attempts to identify themselves, but verification is insufficient.

### `unknown_speaker`

Set when the speaker cannot be matched to Scott or another registered user.

---

## Unknown speaker registration

Unknown speakers may request registration.

Initial registration creates a limited profile:

```yaml
registered_user_id: string
display_name: string
status: pending_scott_approval
created_at: ISO-8601
voice_samples: list[ArtifactRef]
permissions: guest
```

No high-impact permissions are granted until Scott approves.

---

## Authorization matrix

| Auth State | Ask Questions | Submit Notes | Register User | Low-Risk Actions | High-Risk Actions |
|---|---:|---:|---:|---:|---:|
| `authenticated_scott` | yes | yes | yes | auto-approve | confirm/approve |
| `admin_override` | yes | yes | yes | auto-approve or fast-confirm | confirm/approve |
| `scott_voice_unverified` | yes | yes | no | confirm/approve | confirm/approve |
| `registered_user_authenticated` | yes | yes | n/a | Scott approval | Scott approval |
| `registered_user_unverified` | limited | yes | n/a | Scott approval | Scott approval |
| `unknown_speaker` | limited | limited | yes | Scott approval | Scott approval |

---

## Response voice policy

Sophia should respond with Scott clone voice regardless of speaker identity unless a future policy changes this.

```yaml
voice_response_policy:
  default_tts_voice: scott_clone
  authenticated_scott: scott_clone
  admin_override: scott_clone
  scott_voice_unverified: scott_clone
  registered_user_authenticated: scott_clone
  registered_user_unverified: scott_clone
  unknown_speaker: scott_clone
```

Response voice is not authorization.

---

## Low-risk actions

Low-risk actions may be auto-approved for Scott-authenticated sessions:

- create note
- draft text
- search memory
- summarize local context
- list tasks
- create draft task
- classify local file
- enqueue non-destructive historical ingest review
- ask local model endpoint for analysis

---

## High-risk actions

Require confirmation/approval:

- delete/move/rename files
- publish or send external messages
- change auth policy
- change network/system config
- run destructive shell commands
- expose local data outside Tailscale/LAN
- modify protected/evidence artifacts
- grant user permissions

---

## Neo4j event model

Recommended node:

```cypher
(:VoiceAuthDecision {
  decision_id,
  session_id,
  capture_id,
  utterance_id,
  auth_state,
  auth_method,
  speaker_identity,
  speaker_confidence,
  threshold_used,
  auth_reason,
  created_at
})
```

Recommended relationships:

```cypher
(:VoiceAuthDecision)-[:FOR_CAPTURE]->(:MediaCapture)
(:VoiceAuthDecision)-[:IDENTIFIED_AS]->(:Speaker)
(:UserIntent)-[:AUTHORIZED_BY]->(:VoiceAuthDecision)
```

---

## Implementation checklist

- [ ] Add runtime `auth_state` to Sophia responses/events.
- [ ] Add fallback verification flow using a non-committed secret.
- [ ] Add unknown speaker registration stub.
- [ ] Persist `VoiceAuthDecision` events.
- [ ] Add AssistX policy mapping from `auth_state` to approval rules.
- [ ] Add tests for fallback verification.
- [ ] Add tests ensuring unknown speakers cannot execute actions without Scott approval.
