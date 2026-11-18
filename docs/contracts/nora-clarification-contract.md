# Nora clarification contract — recommendation for consolidation

Status: internal development contract, not a deployed client service. Owner: codex-nora. Serves Q4 (scheduled, contextual clarification and pending handling) and Q5 (attributed answers revise evidence-linked tasks). Brain owns canonical knowledge and atomic revisions; Nora owns scheduling and channel adaptation. UUID identifiers and `schema_version: 1` follow the brain owner's proposed contract.

Implementation update: leader #140 selects GitHub storage and operator analysis in Agent Room on the operator's Mac while open, without a new brain server. Nora runtime/delivery remains separate. Leader #153 requires one bounded scheduled reminder after 24 hours, stable question references, candidate-preserving unquoted replies and visible media awaiting text. The current library README describes these implemented seams; the original architecture recommendation below is historical, not a claim of an installed Linux host or client route.

## Recommended route and evidence

Build a company-specific clarification worker beside the online brain, using a separate company-owned OpenClaw WhatsApp account and a narrow inbound bridge. Reuse the existing transport pattern: trusted inbound events → durable spool/inbox → brain; brain round → `openclaw message send --channel whatsapp --account … --target … --json`. Run the worker on the brain's Linux host so founder/team computers can sleep without stopping Nora. Desktop installation needs only the brain address, not a personal Nora runtime.

This recommendation is grounded in local primary code, not a live client connection:

| Evidence | What it establishes | Boundary |
| --- | --- | --- |
| `nora-agent` commit `fe28d9ff3a8a510cc8beed79c15943f656c24d45`, `src/channel/transport.ts:9–20,44–90` | Existing spool pull, sender/reply fields, CLI sends and nullable message receipts | No client setup, current gateway probe or delivery test |
| Same commit, `bridge/nora-ponte/index.js:24–64` | Explicit group/account selection and an inbound spool hook | Existing implementation admits groups, not a verified private-company route |
| Same commit, `src/channel/store.ts:53–117` | SQLite seen-message/outgoing/event pattern | Not the company-brain schema; do not import its GAIO workflow |
| Same commit, `src/channel/router.ts:125–173` | Reply correlation exists | It admits everyone in selected groups and has different text/audio addressing rules; do not reuse these as founder authentication |
| Vault `2026-11-11-codex-nora-replication-guide`, evidence section | Dated observation of the bridge and Mac process running | Historical evidence of the operator's installation; no permission to use his route, history, accounts or jobs for this company |

Use a dedicated founder/Nora conversation group with only verified participants for the initial implementation, because the inspected bridge supports explicit groups. The founder's exact identity, account and group are setup inputs; display names and message bodies never establish identity. A new company bridge must retain the account field which the existing transport projection omits. Pairing, registration and an authorized delivery test remain external dependencies. A private-chat adaptation is possible engineering, not already supported by this receipt. Do not silently switch the founder's route or copy the operator's personal memory, secrets, jobs, GAIO integrations or provider subscription.

The narrow worker does not require an autonomous general-purpose agent or a paid model for dispatch. Natural-language answer interpretation uses the brain's selected provider/contract. Provider/account authorization and actual interpretation quality remain integration dependencies, not resolved by this adapter.

## Canonical records and write seams

All records belong to one company and respect its evidence visibility. The worker receives only the founder-visible summaries needed for that round; links open authenticated brain views. The following names are proposed API/module seams for the brain owner to consolidate, not implemented endpoints.

| Record / operation | Required contents and behavior |
| --- | --- |
| `uncertainty` / question creation during task creation or refinement | `id, company_id, task_id, base_version, observation_ids, missing_field_or_connection, context_key, activity_summary, prompt, status, revision`. Each unresolved field/edge gets an uncertainty; a question may collect related uncertainty IDs. Deduplicate by company + task + semantic missing link, never by prose alone. The task remains provisional. |
| `question` | UUID, linked uncertainty IDs, task/version, source observation IDs, missing fields/connections, question revision, creation time. States: `pending`, `partially_answered`, `resolved`, `superseded`. Delivery state is separate. Any state outside these is invalid. No missing link disappears merely because it was not selected for a concise round. |
| `POST /v1/nora/rounds/claim` (internal service scope) | Clock-controlled atomic claim for company + exact scheduled UTC slot + verified route revision. Returns one frozen round containing related contextual groups, question revisions and source links; otherwise no-work/not-due. Unique company/slot prevents parallel duplicate rounds. |
| `round` | UUID, slot UTC, timezone, route revision, ordered question revisions, content hash, safe rendered text and durable delivery state. States: `prepared`, `sending`, `accepted`, `known_unsent`, `unknown`, `expired`, `empty`. No empty notification. |
| `POST /v1/nora/rounds/{id}/delivery` | Append attempt and actual account/destination/content hash, start/finish timestamps, provider message ID and outcome. Receipt means channel acceptance, not founder reading. A null/malformed receipt is not success. |
| `POST /v1/nora/inbound` (dedicated adapter credential) | Trusted account + conversation + sender + message ID, received time, exact text and reply-to ID; each must match configured company/route/founder membership. Server derives actor ID from the binding; payload `author` is never trusted. Preserve source event. No generic write/query authority. |
| `POST /v1/questions/{id}/answers` (brain seam) | Immutable answer UUID, source inbound ID, question revision, original text, attributed actor, reception time, interpreted fields and unresolved/conflicting fields. Browser founder answers may use the same seam with session identity. A reply covering several questions creates linked answer parts under one source message. |
| Atomic answer → map revision | Save answer, validate current task version, append revision with answer/observation support and update resolvable fields/edges/question states in one transaction. Return answer ID + task/version + remaining gaps. Repeated event with identical content returns same IDs; same identity with changed content conflicts. |

Answer reception is independent of the outbound schedule: replies at night can be saved and reflected in the brain without a proactive reply/receipt message. Founder prose is accepted; no recurring JSON, task form, log or manual summary is required. Quoted round/message IDs plus question labels correlate the answer; an ambiguous multi-question answer is saved unattributed-to-a-question and leaves affected items pending for the next useful scheduled round. Never guess which question was answered.

The brain interpreter may propose only fields supported by that answer. A partial answer resolves only its covered gaps. A correction preserves both answers and the previous task version. Concurrent task changes require re-interpretation against current evidence; no stale blind overwrite. Conflicts remain attributed and provisional and create/update the relevant uncertainty. A founder's account is labelled reported evidence; it does not become an observed fact. Establish a task only when required trigger/performer/input/action/result/purpose/connections are supported and its material gaps/conflicts are resolved.

## Rounds, silence and delivery recovery

Recommended exact anchor, for leader consolidation: **06:00, 10:00, 14:00, 18:00 and 22:00 `America/Sao_Paulo`**. This is a four-hour series anchored at 06:00, omitting the 02:00 occurrence during quiet hours. Quiet interval is `[00:00,06:00)`, derived through IANA timezone conversion, never the machine timezone or a fixed UTC offset. Persist UTC slot IDs and the IANA schedule. Clock injection is required for tests.

Run the due-check regularly; allow a proposed five-minute slot grace period for process wake-up, while still inside active hours. A late or missed slot expires and its questions remain eligible for the next scheduled round. Do not replay a backlog of missed rounds after downtime. Recheck quiet hours, authorization, route and question revisions immediately before starting transport; if changed, expire/rebuild at the next slot. Mark a claimed round `sending` durably before invoking the CLI, so a crashed process cannot claim it as unsent.

Choose oldest unsent/meaningfully changed questions, grouping by learned process/task context and observed activity. Each item states what happened, a supporting source link and the specific missing connection. Proposed concise cap: six questions per round, with overflow visibly pending and carried forward by age; this cap is a design setting, not a company fact. Each contextual group combines related gaps without imposing a company taxonomy. An unchanged pending item is not automatically resent: compare a semantic digest of missing links + explicitly selected evidence that changes this uncertainty's context + prior answer/contradiction, rather than all observations, task timestamp or generated wording. Recurring observations remain supporting records but do not justify another ask. Revisit when relevant new evidence, a partial/conflicting answer, a changed missing connection or an explicit founder request makes the question useful again. Requested resends still wait for the next scheduled round. Empty/no-change rounds stay silent; the brain always shows pending items and delivery problems.

Unique round keys and answer IDs provide internal idempotency. **The inspected CLI has no client-supplied idempotency key. Exactly-once delivery across a crash is therefore not established.** A timeout, process crash after starting send or missing receipt becomes `unknown`, not a blind retry. Suppress that payload until reconciled with a channel message ID or an explicit recovery decision. Only positively known-unsent attempts can retry within the same slot grace window. This favors avoiding unchanged duplicates and exposes possible missed delivery. The adapter must not call a different destination or fall back to another personal account.

## One-time founder first use

After installation, show the next scheduled Nora time in Brasília and the founder's local timezone. Ask the founder to do one real task, confirm its observation/provisional entry in the online brain, then answer Nora's contextual batch at that next scheduled round. Open the linked task afterward and inspect the saved answer, changed purpose/connections, retained observation and remaining gaps. Setup performed after the 22:00 round waits until 06:00; an activity ingested just after a slot waits until the next slot. Explain the wait explicitly. No immediate onboarding message or separate recurring logging step is introduced.

If no unclear connection exists, show that fact and use another real task needing clarification; do not manufacture a missing business fact. If transport is unconfigured or a round has unknown delivery, say that the Nora part remains incomplete and identify the recovery step. An optional browser reply is useful recovery, but does not prove real Nora delivery.

## Implementation ownership, prerequisites and acceptance

Proposed Python seams: `pilot/nora/schedule.py`, `rounds.py`, `transport.py`, `inbound.py`; own tests under `tests/nora/`. Brain owns database migrations, task/evidence/interview/interpretation/auth and HTTP transaction handlers; Nora supplies scheduler/adapter functions against these seams after consolidation. No Node runtime dependency in the brain: a separate pinned OpenClaw service supplies the channel CLI/hooks. The installed OpenClaw version must be checked against the bridge contract; a similarly named/latest package is not equivalent proof.

Before client activation, configure a company-owned host/account, HTTPS brain and adapter credential, explicit founder membership/account/group, least-privilege bridge ingress, WhatsApp device linking, persistent DB/inbox, supervised worker and a visible route-health check. Existing code uses SSH and a Mac worker; same-host Linux execution is a proposed port requiring tests. Client registration, service cost/ownership, route activation and real send recipient require operador/founder authorization. This room does not alter existing Nora or buy/activate service.

| Reviewer cycle | Required proof |
| --- | --- |
| C1–C3 | Desktop observation reaches brain, provisional task and every unresolved link creates a tracked uncertainty/question atomically; worker downtime cannot lose questions. |
| C4 | Inject clock at quiet boundaries, a normal slot, just after slot, a missed slot and restart. One grouped batch; unchanged unanswered questions remain pending without repeat. |
| C5–C7 | Verified founder reply → one immutable answer → task n+1 with n, observations and answer retrievable; reject wrong account/company/sender/reply and changed-payload replay. |
| C8 | Later contradictory correction preserves both answers, leaves conflict visible and schedules a useful new clarification without silently establishing unsupported fields. |
| Channel failure | Crash between send and receipt → unknown/no blind resend; no empty rounds, no overnight sends, no fallback destination. Concurrent scheduler claims produce one round. |

`python3 docs/contracts/nora_contract_probe.py` is an **E1 synthetic clock/selection probe only**. It opens no network and uses no company facts. A later simulated transport/inbound test can prove application integration; a deployed remote brain is E3; only an authorized real delivery + real founder reply ingested back is E4. Neither the probe nor the operator's historical Nora evidence establishes E3/E4 for this pilot. This recommendation stops at the agreed contract/feasibility boundary.
