# Company bridge 0.2.0 — prepared, not activated

`message_received`, `inbound_claim` and `before_dispatch` use the pinned message context: exact WhatsApp channel, account and group. Only the exact configured founder is spooled; the configured company group is claimed without a model response. Missing/conflicting identity never creates an intake record or a false unrelated-route claim.

`reply_dispatch` has a different pinned contract. Identity comes from `event.ctx` (FinalizedMsgContext), with consistent originating account/channel/group. The lifecycle context has `recordProcessed`/`markIdle`, not top-level account/group. A company claim completes those callbacks and returns `{handled:true, queuedFinal:false, counts:{tool:0,block:0,final:0}}`; it emits no acknowledgment. Unrelated routes return undefined.

Canonical conversation/destination fields must identify the configured group. A private or missing canonical conversation cannot inherit a group from a stale session/from hint. For replies the native canonical route follows the pinned mapper's `OriginatingTo ?? To ?? From` order; any explicit `event.originatingTo` must agree. Other `From`/`To` values may represent sender/recipient identities, so they are not universally treated as conversation IDs.

The explicit pinned probe uses the installed actual hook runner, actual message mapper and source-extracted actual reply-event helper from `dispatch-DnzGTpPs.js`. It is not a permissive substitute hook host. Its configuration/state is temporary and isolated; it starts no gateway/account session or model and sends no message. Exact package version and source hashes must match `tests/nora/openclaw-source-pin.json`.

```text
node tests/nora/pinned_bridge_probe.mjs /ABSOLUTE/VERIFIED/OPENCLAW/PACKAGE
```

This is still E1 local integration with synthetic identities. Actual account/group/sender formats, gateway hook admission/order, production spool consumption and authorized real delivery remain activation prerequisites. See `deploy/nora-vps/README.md` for the isolated inactive existing-VPS option. Do not copy personal Nora/GAIO configuration, authentication or history.
