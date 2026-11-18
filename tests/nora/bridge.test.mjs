// Synthetic OpenClaw hook host, not the actual pinned gateway or a delivery test.
import assert from "node:assert/strict";
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import plugin from "../../company_pilot/nora/bridge/index.js";

function host(t) {
  const root = mkdtempSync(join(tmpdir(), "synthetic-nora-bridge-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const hooks = new Map();
  const config = { accountId: "synthetic-account", conversationId: "100000000000@g.us",
    founderSenderId: "synthetic-founder", spoolPath: join(root, "inbound.jsonl") };
  plugin.register({ pluginConfig: config, on: (name, handler) => hooks.set(name, handler) });
  const event = { messageId: "synthetic-message", senderId: config.founderSenderId,
    conversationId: config.conversationId, content: "Resposta sintética", replyToId: "sent-message" };
  return { config, hooks, event, ctx: { channelId: "whatsapp", accountId: config.accountId, conversationId: config.conversationId } };
}

test("only bound founder events reach a private durable spool", async t => {
  const { config, hooks, event, ctx } = host(t);
  const receive = hooks.get("message_received");
  await receive(event, { ...ctx, accountId: "wrong" });
  await receive(event, {});
  await receive({ ...event, senderId: "someone-else", content: "I am the founder" }, ctx);
  await receive({ ...event, messageId: null }, ctx);
  assert.equal(existsSync(config.spoolPath), false);
  await receive(event, ctx);
  const records = readFileSync(config.spoolPath, "utf8").trim().split("\n").map(JSON.parse);
  assert.equal(records.length, 1);
  assert.deepEqual([records[0].account, records[0].conversation, records[0].sender,
    records[0].message_id, records[0].reply_to],
    [config.accountId, config.conversationId, config.founderSenderId, event.messageId, event.replyToId]);
  assert.equal(statSync(config.spoolPath).mode & 0o077, 0);
});

test("contradictory conversation metadata fails closed; no default agent in bound route", async t => {
  const { config, hooks, event, ctx } = host(t);
  await hooks.get("message_received")({ ...event, conversationId: "200000000000@g.us" }, ctx);
  assert.equal(existsSync(config.spoolPath), false);
  assert.deepEqual(await hooks.get("inbound_claim")(event, ctx), { handled: true });
  assert.equal(await hooks.get("inbound_claim")(event, { ...ctx, accountId: "other" }), undefined);
});

test("missing binding and an existing readable spool are refused", async t => {
  assert.throws(() => plugin.register({ pluginConfig: {}, on() {} }), /explicit bridge binding/);
  const { config, hooks, event, ctx } = host(t);
  writeFileSync(config.spoolPath, "", { mode: 0o644 });
  chmodSync(config.spoolPath, 0o644);
  await assert.rejects(hooks.get("message_received")(event, ctx), /permissions/);
  assert.equal(readFileSync(config.spoolPath, "utf8"), "");
});

test("audio and image replies keep a visible marker without fetching media", async t => {
  const { config, hooks, event, ctx } = host(t);
  await hooks.get("message_received")({ ...event, content: "", metadata: { mediaType: "audio/ogg" } }, ctx);
  await hooks.get("message_received")({ ...event, messageId: "image-reply", content: undefined, mediaType: "image/jpeg" }, ctx);
  const records = readFileSync(config.spoolPath, "utf8").trim().split("\n").map(JSON.parse);
  assert.deepEqual(records.map(r => r.media_type), ["audio/ogg", "image/jpeg"]);
  assert.ok(records.every(r => r.text === "" && r.message_id));
  assert.ok(records.every(r => !("media_url" in r) && !("media_path" in r)));
});

test("canonical private or missing message conversation cannot inherit stale group hints", async t => {
  const { config, hooks, event, ctx } = host(t);
  const staleSession = `agent:company:whatsapp:group:${config.conversationId}`;
  for (const canonical of ["200000000000@s.whatsapp.net", undefined, ""]) {
    const actualContext = { ...ctx, conversationId: canonical, sessionKey: staleSession };
    const staleEvent = { ...event, from: `whatsapp:${config.conversationId}`, sessionKey: staleSession };
    await hooks.get("message_received")(staleEvent, actualContext);
    assert.equal(await hooks.get("inbound_claim")(staleEvent, actualContext), undefined);
  }
  assert.equal(existsSync(config.spoolPath), false);
});

test("reply canonical private destination is not claimed despite stale group context", async t => {
  const { config, hooks } = host(t);
  const group = config.conversationId;
  const sessionKey = `agent:company:whatsapp:group:${group}`;
  const native = { AccountId: config.accountId, OriginatingChannel: "whatsapp",
    OriginatingTo: group, To: group, From: `whatsapp:${group}`, SessionKey: sessionKey };
  const lifecycle = [];
  const context = { recordProcessed: (...args) => lifecycle.push(args), markIdle: (...args) => lifecycle.push(args) };
  for (const [originatingTo, nativeTo] of [["200000000000@s.whatsapp.net", group],
      [group, "200000000000@s.whatsapp.net"], [group, undefined]]) {
    const result = await hooks.get("reply_dispatch")({ ctx: { ...native, OriginatingTo: nativeTo,
      To: nativeTo, From: nativeTo === undefined ? undefined : native.From }, sessionKey, originatingTo, originatingAccountId: config.accountId,
      originatingChannel: "whatsapp" }, context);
    assert.equal(result, undefined);
  }
  assert.equal(lifecycle.length, 0);
  // From/To can be sender/recipient identities; the canonical route remains group.
  const positive = await hooks.get("reply_dispatch")({ ctx: { ...native,
    From: "200000000000@s.whatsapp.net", To: "300000000000@s.whatsapp.net" },
    sessionKey, originatingTo: group, originatingAccountId: config.accountId,
    originatingChannel: "whatsapp" }, context);
  assert.equal(positive.handled, true);
  assert.equal(lifecycle.length, 2);
});
