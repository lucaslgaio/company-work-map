# Inactive company Nora option on the existing VPS

This bundle prepares Q4 scheduled transport and Q3 operator installation. **It installs, enables, pairs and sends nothing.** It does not modify the personal Nora/GAIO gateway, cron jobs, profile, credentials or Mac services. It uses a dedicated `company-pilot` Linux user, OpenClaw profile/config/state, two new unit names and a separate loopback port on the existing host. This is an option for a later specific authorization, not a claim that the VPS is currently ready or that the route is connected.

## Verified integration and source boundary

The bridge is tested against installed OpenClaw `2026.7.1-2`, Node `22.23.2`, the actual hook runner and message mapper, and the actual `createReplyDispatchEvent` caller helper extracted from the pinned dispatcher. `reply_dispatch` reads trusted inbound identity from `event.ctx`; its second argument contains dispatcher/lifecycle callbacks. The bridge now completes that lifecycle and returns `handled`, `queuedFinal:false` and zero tool/block/final counts. Configured company account/conversation is claimed; unrelated/missing/conflicting identity passes through without a false claim. `message_received` still retains only the verified founder's text/media marker in the private spool. No model, live gateway or channel call occurs in the probe.

Source hashes and explicit probe: `tests/nora/openclaw-source-pin.json`, `tests/nora/pinned_bridge_probe.mjs`. Run only against the selected verified package:

```sh
node tests/nora/pinned_bridge_probe.mjs /ABSOLUTE/VERIFIED/OPENCLAW/PACKAGE
```

It refuses a version/hash mismatch and isolates its configuration/state before importing installed modules. This local E1 check does not prove real WhatsApp ingress/delivery, systemd operation, Linux packaging, target VPS capacity or a founder binding. Source pin changes require review, not disabling assertions. The hook's `allowConversationAccess` belongs under `plugins.entries.company-nora-bridge.hooks`, as the pinned normalization/schema code shows.

Root's model-free command is present and was exercised through its offline Nora-service tests before this invocation was placed in the unit:

```text
/opt/company-pilot/venv/bin/python -m company_pilot --config /etc/company-pilot/config.json nora watch
```

Those tests prove no inference import/construction, refusal of disabled/unverified routes, independent health, bounded polling/backoff, spool deduplication and signal stop. They are not proof of an installed target-host service. Pin the final reviewed root release that includes the command. The worker leaves semantic jobs queued for the operator's Mac and does not run Claude, Codex or another model.

## Staging, not installation

After choosing the exact reviewed code commit and verifying the Linux executable path, the delivered renderer creates a **new private staging directory only**:

```text
python3 deploy/nora-vps/render_bundle.py --output NEW_PRIVATE_STAGE \
  --openclaw-executable /ABSOLUTE/VERIFIED/LINUX/openclaw \
  --code-commit EXACT_REVIEWED_40_HEX_COMMIT
```

It rejects unsafe/personal executable paths and overwrites. Port `18849` is a design default, not an observed free port; verify it or explicitly choose another unused port. The renderer refuses known personal defaults `18789` and `18811`. It renders private unit/config/fragment/manifest files with **Nora disabled, route unverified and plugin disabled**. It creates no approval file, credential, complete gateway config or systemd installation. The fragment is deliberately not named `openclaw.json`: it is not a complete approved WhatsApp configuration.

| Isolated item | Prepared path / behavior |
| --- | --- |
| Reviewed source + Python environment | `/opt/company-pilot/source`, `/opt/company-pilot/venv`; source/installed CLI must match the reviewed commit |
| Runtime user and private state | `company-pilot`, `/var/lib/company-pilot`; never run as personal Nora's root user or copy its HOME |
| Dedicated OpenClaw namespace | Profile `company-pilot`, `OPENCLAW_STATE_DIR=/var/lib/company-pilot/openclaw`, `OPENCLAW_CONFIG_PATH=/etc/company-pilot/openclaw.json` |
| Separate units | `company-pilot-gateway.service`, `company-pilot-nora.service`; no replacement/override of `openclaw-gateway.service` |
| Inbound and durable cursor | `/var/lib/company-pilot/inbound/messages.jsonl`, `/var/lib/company-pilot/cursor/nora-inbox.json` |
| GitHub integrity mirror | `/var/lib/company-pilot/mirror`, private `company-state` branch; branches do not separate native permissions |
| Health | `/var/lib/company-pilot/health/nora.json`; analysis health is a separate unavailable-on-this-host file, never overwritten by Nora |
| Private credentials | Dedicated `/etc/company-pilot/gateway.env` and `github.env`; examples are empty, contain no secrets and grant nothing |
| Start gates | Gateway requires complete private config + `gateway-approved.json`; worker requires private config + `nora-approved.json` and still refuses disabled/unverified binding |

The units use `ProtectHome`, read-only system paths, private temp and a private writable state directory. They never use `--force` or `--allow-unconfigured`. These are static prepared settings, not an Ubuntu/systemd validation receipt. File-presence gates are operator controls, not human approval by themselves; never fabricate the approval record. Rendering also rejects the installed config/unit/state paths, including their macOS path aliases.

## Executable owner-side binding

The existing actor-enrollment path is reused rather than reimplemented. After native founder write access is already confirmed, the owner-side command is:

```text
ABSOLUTE_PYTHON -m company_pilot --config PRIVATE_CONFIG owner enroll-founder \
  --github-login VERIFIED_FOUNDER_LOGIN --request-reference VERIFIED_REQUEST_REF --event-id UUID
```

It verifies the owner/native role and records the founder; it does not invite anyone. The Nora service credential must attest as an existing configured `nora_actor` (normally the initial operating owner, with a company-repository credential rather than copied personal authentication). A newly chosen service identity is not silently enrolled or given founder/admin powers; it must first be explicitly configured in the company policy. The standard owner-side option needs no new actor/account. The binding command refuses an unconfigured operator or an unenrolled founder.

`company_pilot.nora.setup` is delivered code, not an instruction to engineer a binding path. It reuses root configuration/Brain lazily, checks authenticated owner/founder authority and configured Nora-actor scope, validates the complete typed route, and journals a stable event before mutation. Owner verification of the supplied request/identity reference is required; a string alone is not channel-authentication evidence.

Store the complete future route in a private JSON file (placeholders below are deliberately not valid identities):

```json
{"company_id":"CANONICAL_COMPANY_UUID","id":"ROUTE_UUID","revision":1,
 "account":"VERIFIED_ACCOUNT_ID","conversation":"VERIFIED_NUMERIC_GROUP_JID@g.us",
 "founder_sender":"VERIFIED_OPERATOR_SENDER_ID","founder_actor_id":"ENROLLED_FOUNDER_LOGIN",
 "group_name":"NOME_DO_GRUPO","enabled":false,
 "responders":[{"sender":"VERIFIED_RESPONDER_1_SENDER_ID","actor_id":"responder-1","name":"NOME_DE_QUEM_RESPONDE_1"},
               {"sender":"VERIFIED_RESPONDER_2_SENDER_ID","actor_id":"responder-2","name":"NOME_DE_QUEM_RESPONDE_2"}]}
```

The owner prepares it **inactive by default**:

```text
ABSOLUTE_PYTHON -m company_pilot.nora.setup --config PRIVATE_CONFIG bind \
  --route-file PRIVATE_ROUTE_JSON --request-reference VERIFIED_REQUEST_REF --event-id UUID
```

This records the exact canonical route and local ID/revision while leaving `enabled=false` and `route_verified=false`. It starts no service, contacts no WhatsApp account and sends no message. Changed payload under the same event conflicts; completed replay is read-only and cannot reverse a later local pause or replace a newer route. Reconfiguration uses the same route ID and the next revision.

Only after the later narrow activation authorization and real trusted binding verification, prepare the next route revision with `enabled:true`, use a **new** event UUID and add `--enable --verification-reference VERIFIED_BINDING_RECEIPT_REF` to that same command. It aligns the canonical/local gates but still starts or sends nothing; worker approval/readiness remains separate. Never use it to manufacture a connection receipt.

Unknown result exits with code 3, leaves local gates disabled and never automatically repeats a remote mutation. Preserve the private `nora-bindings/UUID.json` journal. Reconcile through the delivered command:

```text
ABSOLUTE_PYTHON -m company_pilot.nora.setup --config PRIVATE_CONFIG reconcile --event-id ORIGINAL_UUID
```

It finishes local alignment only when canonical route and immutable ledger result match the original actor/request/route hashes. This includes `prepared_local`: an older identical route does not prove this new request committed. Without positive evidence it remains `unknown_requires_review`; do not delete the journal or invent a new event to bypass uncertainty. The composed E1 SDK proof exercises inactive binding, idempotent head, explicit synthetic enabling and a lost post-commit receipt resolved without a remote retry. No actual company binding was applied.

## Exact prerequisites before any later activation

1. Obtain the operator's explicit narrow authorization for this existing-host option, selected code/package hashes, new profile/user/units/port and the scoped company account. Capture a technical baseline and capacity/port checks without exporting personal configuration. Additional Node gateway capacity, pairing/session limits and the actual executable are unverified; do not silently purchase/upgrade a service.
2. Stage the reviewed release under the new user; validate the final rendered unit files on the target system. Create only new company directories. Provision a dedicated gateway secret and minimum necessary company-repository credential through the chosen secret store, with private file ownership. No personal auth/vault/history, credentials or broad administrator token belongs in the bundle or on participant devices.
3. After account/phone consent, obtain account ID, exact group/conversation and native founder sender from trusted metadata and confirm them with the owner. Do not infer identity from group name/text or copy a personal session. Create the complete dedicated profile's WhatsApp admission rules: only the intended company group/sender, unrelated private/group ingress denied, and no general agent route for company traffic. Validate that complete pinned config; the provided plugin fragment alone is insufficient. Pairing is a separate approved human step, not connection proof.
4. Verify the complete company profile, plugin hash/hook permission and real caller ordering under that isolated gateway. Use the delivered `python -m company_pilot.nora.setup` path above with the verified inputs; it populates the matching canonical route and private CLI ID/revision/receipt. The code independently checks company, founder enrollment, operator authority and revision; setting a boolean is not evidence of connectivity.
5. Review the exact rendered manifest and service/config diffs, record the authorized scope, then install/start **only the new gateway** through the separately authorized operator. Check that its isolated gateway/account is ready and the personal/GAIO services/jobs remain unchanged. Do not start the scheduler just because Type=simple reported a process: gateway/channel readiness must be verified first.
6. Under the specific authorization for the scheduled test recipient/round, enable only the verified plugin/route, record the real operator approval, and start the new model-free Nora worker. The unit's command and the root CLI route gates must all pass. Run `/opt/company-pilot/venv/bin/python -m company_pilot --config /etc/company-pilot/config.json nora health`; configured/stored health still says `not_probed` for channel connectivity and must not be relabelled connected.
7. At a normal Brasília slot, prove one real contextual batch, attributed text reply, durable spool/cursor intake, GitHub source/answer record and subsequent Mac interpretation/map revision. Check that a replay adds nothing, night intake sends no acknowledgment, unknown delivery is held and no missed slot backfills. Preserve the exact receipts; only then describe that path as tested.

There is one reminder after at least 24 hours, with a reserved oldest-reminder item; ordinary recurring observations do not repeat unchanged questions. A sender call remains after the durable CAS SENDING marker. Restart/timeout/ambiguous send remains unknown and cannot be retried blindly. These guarantees are unchanged by the deployment option.

## Availability, recovery and audit

While this VPS worker remains online, it can send questions already prepared in GitHub and record founder replies even with the operator's Mac unavailable. New semantic interpretations/questions and map changes still wait visibly for that Mac. If this worker/gateway itself is unavailable, the slot is missed; the next available normal slot is used, with no off-slot catch-up. Do not promise unconditional uptime from these templates.

If readiness fails, keep the new worker stopped. Recovery stops/disables only the two company units, preserving company spool/cursor/mirror and unknown-delivery records. It does not restart, kill, reconfigure or roll back personal Nora/GAIO, revoke an unrelated session, erase evidence or resend indeterminate messages. Reconcile unknown outcomes with verified receipts before releasing them. Approval remains required for destructive cleanup or account changes.

The private brain currently stores raw binding/message identity metadata as disclosed in `company_pilot/nora/README.md`; all repository readers can inspect it. The CLI message body is on local argv during a send. Gateway logs may contain company data and need the same access/retention controls. No template contains token values.

Investigation side effect: an initial general `openclaw --help` call attempted permission hardening of the personal state directory/database; the sandbox rejected it with EPERM. No repair/escalation was attempted. Subsequent installed-module probes isolate configuration/state in a private temporary directory. Public help is not assumed side-effect-free, and target checks must use the new namespace rather than personal state.

During the renderer's negative-path tests, macOS `/etc` and `/var` aliases initially escaped the resolved-path guard; attempted directory creation was sandbox-denied. Independent p32 review later found that direct canonical `/private/etc` and `/private/var/lib/company-pilot` destinations still reached an intercepted mkdir. The repaired guard normalizes the protected bases as well as checking lexical/resolved destinations, retaining explicit macOS aliases across hosts. Exact independent probes now reject before mkdir. No such installation path or approval file was created.

`verification.json` preserves the original preparation receipt. `repair-verification.json` records the focused p32 repair, exact baseline failures, repaired probe output, source hashes and E1 limits. It supersedes the original guard assertions for these repaired sources; it is not an activation receipt.
