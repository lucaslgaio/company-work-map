# Nora library and inactive channel adapter

This is the Q4/Q5 scheduling and source-attributed intake implementation. It has no HTTP/Django dependency, default memory store, default recipient or automatic live activation. Product-facing messages are in Brazilian Portuguese. The current brain choice is GitHub-backed storage; the earlier Linux brain-host recommendation in the working contract is superseded as a storage decision.

The operating model places analysis in Agent Room on the operator's Mac while it is open; no founder/team paid assistant account or new brain server is required by this module. **GitHub storage does not supply a running WhatsApp session or guarantee Nora's scheduled delivery.** Nora's runtime/route is a separate configuration decision. The injected worker must be running/online at a slot for that slot to execute; unavailable work stays pending and missed slots are not replayed. This code does not select/buy a VPS, change the operator's Nora, install a scheduled job or send a real message. GitHub Actions is not presented as the five-slot runtime. The root operator CLI/operating instructions own availability and supervisor installation on the authorized runtime.

## Entry points and repository seam

`Nora(company_id, repository, clock, sender)` accepts a timezone-aware clock callable, a repository implementing `transact(company_id, operation)` and a sender implementing `send(route, text)`. The callback changes `NoraState` and returns a result. Repository durability/atomicity must hold across processes: commit before return, rollback on error and conflict checking. Callbacks contain no external effects and can be retried against fresh state on CAS conflicts. The channel is invoked only after the SENDING marker has committed. A failed/indeterminate post-send commit leaves that marker for unknown-delivery recovery.

`BrainRepository(company_id, backend.transact_nora)` maps the brain's atomic Nora JSON callback to that interface. The callback receives and updates the current mutable Nora state dictionary (initially `{}`), returning a JSON change marker. The backend must durably commit the final evaluation before returning, fail on unconfirmed commit and skip a write/no-op audit commit when the dictionary is unchanged. `encode_state`/`decode_state` use JSON records with `schema_version: 1`; no pickle or separate production database. The SQLite/pickle store under tests is only a synthetic fixture.

The brain's adapter projects canonical questions into `NoraState.questions`. `put_question` is a convenience when needed. `missing_links` accepts only the frozen enum: `trigger`, `performer`, `input`, `action`, `result`, `purpose`, `placement`, `handoff`, `tracking_record`. Detail belongs in the prompt; an overlapping pending missing field for the same task cannot acquire a second question ID. Snapshot rounds preserve the versions and evidence actually asked about.

Ordinary `observation_ids` can grow every work cycle without triggering another ask. Only explicitly selected `revisit_evidence_ids` that materially change **this gap's context**, changed canonical gaps, answer/conflict IDs or an explicit resend request affect suppression. Revisit IDs must also exist in the observation support. The brain must not mark every new capture as relevant revisit evidence. Leader policy #153 adds exactly one reminder at the first available normal slot at least 24 hours after the initial accepted ask, with its prior ask time shown. It remains pending afterward, without another unchanged reminder. `reminder_after` can be disabled or increased, never shortened below 24 hours. Reserve one item for the oldest due reminder; fill the remaining capacity with oldest fresh questions, then other due reminders. A continuous fresh backlog cannot starve reminders, and the batch cap remains six. Resolved/superseded entries are excluded; overflow stays durable.

`tick()` schedules 06/10/14/18/22 America/Sao_Paulo, allowing at most five minutes of slot grace. No backfill, empty notification or unchanged repeat. It returns next scheduled time, pending/overflow counts and the durable round outcome. `next_scheduled_at()` supports the one-time founder walkthrough. It never authorizes an immediate onboarding round. The sender is checked again against the slot before invocation.

`receive(InboundEvent(...))` requires an exact configured account/conversation/founder sender. It returns a durable immutable `ReceivedAnswer`, with identity derived from the route. Same event with changed content is a conflict; a later received timestamp alone is a replay. New rounds display stable `Q-XXXXXXXX` question references across rounds. Quoted messages retain their original question/version basis; old P1/P2 labels resolve only within their exact quoted round. Every verified unquoted reply retains all pending candidate questions in that bound company conversation. Exactly one candidate can be linked directly; several candidates stay unassigned unless an unambiguous stable question reference is present. The code never guesses the latest round's P1. Night replies create no acknowledgment send. Audio/image markers retain MIME type/message ID and remain visibly `awaiting_text`, without fetching arbitrary media URLs. The initial pilot asks for written replies in PT-BR. Question/map status remains untouched. The brain consumes `state.inbox` through `record_answer`/interpretation idempotently; stale versions are preserved, then evaluated against current tasks.

`reconcile_unknown` is an administrative seam, never founder-prose authority. A verified receipt or positively verified-unsent disposition must include an evidence ID. Reconciliation sends nothing; a released question waits for the next slot. Caller authorization remains the integration's responsibility.

## Run and health integration

Root owns packaging, CLI and installation. The intended wrapper supplies the selected backend and verified route, then:

```python
from datetime import datetime, timezone
from threading import Event
from company_pilot.nora import BrainRepository, Nora, health, run
from company_pilot.nora.openclaw import OpenClawSender

repository = BrainRepository(company_id, backend.transact_nora)
sender = OpenClawSender(absolute_verified_openclaw_path, verified_enabled_route)
nora = Nora(company_id, repository, lambda: datetime.now(timezone.utc), sender)
# Install the wrapper under the explicitly chosen host's supervisor; SIGTERM sets stop.
stop = Event()
run(nora, stop, poll_seconds=15, report=metadata_only_report)
```

`health(nora)` reports route configuration, pending questions, media awaiting text, unknown/stale attempts and the next Brasília slot. It explicitly returns `channel_connectivity: not_probed`; configured/enabled is not linked or delivered. It does not monitor operator availability. `run` emits only result metadata/error types, not bodies/raw exceptions, and stops via `Event`. No supervisor or live channel was activated here. Root's CLI must expose run/health and report its actual availability; no always-on claim follows from installing this library.

Python >=3.11 is required. Most Unix hosts provide the IANA timezone database; Windows/runtime bundles without one need `tzdata` installed by root packaging. `zoneinfo` failure is visible and never falls back to a fixed offset. Only the separate OpenClaw adapter needs its own Node/runtime. No LLM key is used by scheduling; answer interpretation/account handling belongs to the brain worker.

## OpenClaw adapter and evidence limits

`OpenClawSender` requires an absolute executable and an exact enabled binding. Construction/import sends nothing. It invokes argv directly with no shell, bounds execution to at most thirty seconds and never selects a fallback account. Missing/unexecutable binary is known-unsent. Nonzero exit, timeout, missing receipt or mismatched receipt is unknown, because an external effect may already have happened. This CLI has no client idempotency key: exactly-once remote delivery is not claimed.

Data disclosure: this implementation persists raw route account/group/founder sender IDs and actor ID, plus inbound message IDs, sender/account/group, received time, original text and optional MIME marker in the private brain. Every collaborator with repository read access can read them; branches provide no access separation. It stores no WhatsApp credentials or media bytes. Moving bindings operator-local is a future integration option, not an implemented confidentiality boundary. The CLI message body appears in local process argv during the send, as in the inspected transport; logs do not include it, but local process inspection can.

`bridge/` is a prepared company-specific group hook adapted from primary local `nora-agent` code at `fe28d9f`. It retains account identity, only spools the configured founder in the exact account/group, refuses contradictory group metadata and denies broad spool permissions. Hook availability, sender metadata format and actual CLI output require verification against the selected pinned gateway before activation. It is not installed or a tested production route. An authenticated spool reader must call `inbound_from_record` then `receive`, advancing its durable cursor only after intake commits; malformed lines need visible quarantine rather than silent discard. Brain/route integration owns that reader/cursor.

Run the synthetic checks:

```sh
python3 -m unittest discover -s tests/nora -p 'test_*.py'
node --test tests/nora/bridge.test.mjs
```

Tests use a fake sender, a local SQLite transaction fixture and a synthetic hook host. They cover concurrency, commit/crash failures, unknown delivery, safe retries, recipient/identity denials, recurring-source suppression, overflow, quiet times, stale/correcting answers and literal argv. They do not prove live WhatsApp delivery, a deployed GitHub backend, real company facts, OS supervisor installation or answer-to-map interpretation. Those are separate integration/acceptance proofs.
