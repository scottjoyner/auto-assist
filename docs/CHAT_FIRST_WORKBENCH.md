# Chat-first Agent Workbench

## Purpose

The Agent Workbench is an additive operator surface that keeps conversation primary
while exposing execution context only when it is useful. It complements, rather
than replaces, the canonical `/control-room` fleet view and the Kipnerter native
mobile client.

The initial route is `/workbench`.

## Interaction model

The default surface is a Hermes conversation. A contextual drawer shows the
currently observed execution, recent sessions/dispatches, and fleet runtime
snapshot. On desktop the drawer is visible beside chat; on narrow screens it
becomes an overlay opened explicitly by the operator.

The workbench should answer "what is doing this work, where, and against which
task/repository?" without requiring the operator to leave the conversation.

## Existing boundaries reused

Chat uses the existing authenticated
`POST /api/v1/agent/chat/completions` boundary from
`mobile_agent_routes.py`.

That endpoint already:

- authenticates through the existing Tailnet identity / Basic-auth fallback;
- executes Hermes;
- defaults Hermes to the `assistx-router` provider;
- treats `agent:auto` as no model override;
- returns the Hermes session identifier without exposing a new router surface.

The workbench therefore does **not** call `/api/llm/stream` or
`/api/v1/model/chat/completions`, and it adds no runtime/model selection
authority.

The contextual drawer is read-only. It consumes:

- the existing `AssistXShell` control-room snapshot;
- `GET /api/sessions`;
- `GET /api/dispatches`.

It does not claim, approve, dispatch, migrate, promote, reconcile, or mutate
fleet state.

## Session behavior

Browser-side conversation messages are retained only in memory for the current
page. A conversation key and the latest Hermes session ID are stored in
`sessionStorage`, limiting them to the current browser tab/session. Requests
also resend the bounded conversation history, so the current slice does not rely
on server-side transcript persistence.

## Relationship to Kipnerter

Kipnerter remains the native phone surface for push, Activity/history, deep
links, and quick chat. The web workbench is the fuller operator surface for
inspection. The intended handoff is:

`Kipnerter Activity / Chat → execution detail → Open Workbench`

No attempt is made here to reproduce terminal, Git, or file mutation controls on
the phone.

## Acceptance for the first slice

The first slice is accepted when:

1. `/workbench` renders behind existing AssistX authentication.
2. Chat posts only to the Hermes Agent Auto boundary with `agent:auto`.
3. Hermes session and conversation headers are preserved.
4. Context instrumentation performs no mutation calls.
5. The drawer renders execution/session/fleet data and collapses to an overlay on
   narrow screens.
6. Existing shell, control-room, navigation, and UI regression tests remain
   green.

## Deliberately deferred

A later slice may add repository files, Git diff, logs, terminal, or explicit
Hermes-webapp integration. Those capabilities must first have a scoped
capability/auth contract. They should not be inferred from browser access and
must not widen claim, dispatch, approval, routing, model-admission, tool, or
mutation authority.

The next useful UX step is automatic conversation-to-execution binding: when a
chat turn creates or references a known task/run/repository, the drawer should
select that exact context instead of showing the newest active fleet event.
