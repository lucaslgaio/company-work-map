// Explicit local E1 probe. Usage: node pinned_bridge_probe.mjs OPENCLAW_PACKAGE_ROOT
// Uses the installed pinned runner/mappers and its actual caller event helper.
// No gateway, config/account API, message send, model or network is started.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import vm from "node:vm";
import test from "node:test";
import plugin from "../../company_pilot/nora/bridge/index.js";

const packageRoot = resolve(process.argv[2] ?? "MISSING_PINNED_OPENCLAW_ROOT");
const pin = JSON.parse(readFileSync(new URL("./openclaw-source-pin.json", import.meta.url), "utf8"));
assert.equal(JSON.parse(readFileSync(join(packageRoot, "package.json"), "utf8")).version, pin.version);
for (const [file, expected] of Object.entries(pin.files)) {
  assert.equal(createHash("sha256").update(readFileSync(join(packageRoot, file))).digest("hex"), expected,
    `Unverified pinned source: ${file}`);
}

// Isolate configuration/state BEFORE importing the installed library. Never read
// the user's actual gateway configuration or credentials in this probe process.
const isolatedState = mkdtempSync(join(tmpdir(), "company-nora-pinned-state-"));
process.env.OPENCLAW_STATE_DIR = isolatedState;
process.env.OPENCLAW_CONFIG_PATH = join(isolatedState, "not-configured.json");
process.on("exit", () => rmSync(isolatedState, { recursive: true, force: true }));
const runnerModule = await import(pathToFileURL(join(packageRoot, "dist/hook-runner-global-BmIrGlLG.js")));
const mappers = await import(pathToFileURL(join(packageRoot, "dist/message-hook-mappers-CLWKm8aG.js")));
const dispatchSource = readFileSync(join(packageRoot, "dist/dispatch-DnzGTpPs.js"), "utf8");
const helperStart = dispatchSource.indexOf("function createReplyDispatchEvent(params)");
const helperEnd = dispatchSource.indexOf("\nfunction ", helperStart + 1);
assert.ok(helperStart >= 0 && helperEnd > helperStart);
const createReplyDispatchEvent = vm.runInNewContext(`(${dispatchSource.slice(helperStart, helperEnd)})`, { Object });

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "company-nora-pinned-spool-"));
  t.after(() => { runnerModule.a(); rmSync(root, { recursive: true, force: true }); });
  const config = { accountId: "synthetic-company", conversationId: "100000000000@g.us",
    founderSenderId: "synthetic-founder", spoolPath: join(root, "inbound.jsonl") };
  const registry = { plugins: [{ id: plugin.id, status: "loaded" }], hooks: [], typedHooks: [] };
  plugin.register({ pluginConfig: config, on: (hookName, handler) => registry.typedHooks.push({
    pluginId: plugin.id, hookName, handler,
  }) });
  runnerModule.i(registry);
  const runner = runnerModule.t();
  const raw = { AccountId: config.accountId, Provider: "whatsapp", Surface: "whatsapp",
    OriginatingChannel: "whatsapp", OriginatingTo: config.conversationId,
    To: config.conversationId, From: "whatsapp:" + config.conversationId,
    SenderId: config.founderSenderId, MessageSid: "synthetic-inbound", Body: "Resposta sintética",
    SessionKey: "agent:company:whatsapp:group:" + config.conversationId };
  const lifecycle = [];
  // This is the actual lifecycle-context contract at dispatch-DnzGTpPs.js:1797,
  // deliberately WITHOUT message-context accountId/conversationId properties.
  const context = { cfg: {}, dispatcher: {}, abortSignal: undefined,
    recordProcessed: (...args) => lifecycle.push(["processed", ...args]),
    markIdle: (...args) => lifecycle.push(["idle", ...args]) };
  const replyEvent = (changes = {}, outer = {}) => createReplyDispatchEvent({
    ctx: { ...raw, ...changes }, sessionKey: raw.SessionKey, inboundAudio: false,
    shouldRouteToOriginating: false, originatingChannel: "whatsapp",
    originatingTo: config.conversationId, originatingAccountId: config.accountId,
    shouldSendToolSummaries: () => false, sendPolicy: "allow", ...outer,
  });
  return { config, runner, raw, context, replyEvent, lifecycle };
}

test("actual pinned reply caller is claimed with full result and completed lifecycle", async t => {
  const f = fixture(t);
  assert.equal(f.context.accountId, undefined);
  const result = await f.runner.runReplyDispatch(f.replyEvent(), f.context);
  assert.deepEqual(result, { handled: true, queuedFinal: false, counts: { tool: 0, block: 0, final: 0 } });
  assert.equal(f.lifecycle.filter(event => event[0] === "processed").length, 1);
  assert.equal(f.lifecycle.filter(event => event[0] === "idle").length, 1);
});

test("actual pinned caller passes unrelated account and conversation without lifecycle claims", async t => {
  const f = fixture(t);
  assert.equal(await f.runner.runReplyDispatch(f.replyEvent({ AccountId: "other" }, { originatingAccountId: "other" }), f.context), undefined);
  assert.equal(await f.runner.runReplyDispatch(f.replyEvent({
    To: "200000000000@g.us", OriginatingTo: "200000000000@g.us", From: "whatsapp:200000000000@g.us",
    SessionKey: "agent:other:whatsapp:group:200000000000@g.us",
  }, { originatingTo: "200000000000@g.us", sessionKey: "agent:other:whatsapp:group:200000000000@g.us" }), f.context), undefined);
  assert.equal(f.lifecycle.length, 0);
});

test("conflicting or missing trusted account/channel/group metadata is never claimed", async t => {
  const f = fixture(t);
  for (const event of [
    f.replyEvent({ AccountId: undefined }),
    f.replyEvent({}, { originatingAccountId: "other" }),
    f.replyEvent({}, { originatingTo: "200000000000@g.us" }),
    f.replyEvent({ OriginatingChannel: "telegram", Surface: "telegram", Provider: "telegram" }, { originatingChannel: "telegram" }),
  ]) assert.equal(await f.runner.runReplyDispatch(event, f.context), undefined);
  assert.equal(f.lifecycle.length, 0);
});

test("actual pinned mapper and runner preserve message/account/reply/media in private spool", async t => {
  const f = fixture(t);
  const canonical = mappers.n({ ...f.raw, Body: "", MediaType: "audio/ogg", ReplyToId: "synthetic-outbound" });
  await f.runner.runMessageReceived(mappers.u(canonical), mappers.l(canonical));
  const records = readFileSync(f.config.spoolPath, "utf8").trim().split("\n").map(JSON.parse);
  assert.equal(records.length, 1);
  assert.deepEqual([records[0].account, records[0].conversation, records[0].sender, records[0].message_id,
    records[0].reply_to, records[0].media_type], [f.config.accountId, f.config.conversationId,
    f.config.founderSenderId, "synthetic-inbound", "synthetic-outbound", "audio/ogg"]);
});

test("actual pinned inbound claim prevents default model only on configured company route", async t => {
  const f = fixture(t);
  const canonical = mappers.n(f.raw);
  assert.deepEqual(await f.runner.runInboundClaim(mappers.c(canonical), mappers.s(canonical)), { handled: true });
  const foreign = mappers.n({ ...f.raw, AccountId: "other-account" });
  assert.equal(await f.runner.runInboundClaim(mappers.c(foreign), mappers.s(foreign)), undefined);
});

test("actual pinned authoritative private routes override stale company group hints", async t => {
  const f = fixture(t);
  const privateConversation = "200000000000@s.whatsapp.net";
  const canonical = mappers.n({ ...f.raw, OriginatingTo: privateConversation });
  assert.equal(mappers.l(canonical).conversationId, privateConversation);
  await f.runner.runMessageReceived(mappers.u(canonical), mappers.l(canonical));
  assert.equal(existsSync(f.config.spoolPath), false);
  assert.equal(await f.runner.runInboundClaim(mappers.c(canonical), mappers.s(canonical)), undefined);
  assert.equal(await f.runner.runReplyDispatch(f.replyEvent({}, { originatingTo: privateConversation }), f.context), undefined);
  assert.equal(await f.runner.runReplyDispatch(f.replyEvent({ OriginatingTo: privateConversation }), f.context), undefined);
  assert.equal(f.lifecycle.length, 0);
});
