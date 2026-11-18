import { closeSync, constants, fstatSync, fsyncSync, mkdirSync, openSync, writeSync } from "node:fs";
import { dirname, isAbsolute } from "node:path";

// Prepared adapter only. Never installed/enabled by this library. In a dedicated
// company instance, config supplies exact accountId/conversationId/founderSenderId
// and a private spoolPath. No history, job, tool or credential import is needed.
export default {
  id: "company-nora-bridge",
  name: "Company Nora clarification bridge",
  register(api) {
    const config = api.pluginConfig ?? {};
    const { accountId, conversationId, founderSenderId, spoolPath } = config;
    if (![accountId, conversationId, founderSenderId, spoolPath].every(v => typeof v === "string" && v.length)
        || !isAbsolute(spoolPath)) throw new Error("Complete explicit bridge binding required");
    mkdirSync(dirname(spoolPath), { recursive: true, mode: 0o700 });
    const isConversation = value => value === conversationId || value === `whatsapp:${conversationId}`;
    const optionalConversationsAgree = fields => fields.every(value => value == null || isConversation(value));
    const groupHintsAgree = fields => {
      const groups = fields.filter(v => typeof v === "string").flatMap(v => v.match(/[0-9]+@g\.us/gu) ?? []);
      return groups.every(v => v === conversationId);
    };
    // Canonical context is the route evidence. Session/from hints can contradict
    // it, but cannot replace a missing/private authoritative conversation.
    const bound = (event, ctx) => ctx?.channelId === "whatsapp" && ctx?.accountId === accountId
      && isConversation(ctx.conversationId)
      && optionalConversationsAgree([event?.conversationId, event?.metadata?.originatingTo])
      && groupHintsAgree([ctx.sessionKey, event?.sessionKey, event?.from]);
    const replyBound = event => {
      // Pinned reply_dispatch carries FinalizedMsgContext on event.ctx. Its second
      // argument is lifecycle/dispatcher context, not PluginHookMessageContext.
      const native = event?.ctx;
      if (!native || native.AccountId !== accountId) return false;
      const channel = native.OriginatingChannel ?? native.Surface ?? native.Provider;
      if (channel !== "whatsapp" || (event.originatingChannel != null && event.originatingChannel !== "whatsapp")
          || (event.originatingAccountId != null && event.originatingAccountId !== accountId)) return false;
      const canonical = native.OriginatingTo ?? native.To ?? native.From;
      return isConversation(canonical) && optionalConversationsAgree([event.originatingTo])
        && groupHintsAgree([native.To, native.From, native.SessionKey, event.sessionKey]);
    };
    api.on("message_received", async (event, ctx) => {
      if (!bound(event, ctx) || event.senderId !== founderSenderId
          || typeof event.messageId !== "string" || !event.messageId) return;
      const text = typeof event.content === "string" ? event.content : "";
      const rawMediaType = event.mediaType ?? event.metadata?.mediaType ?? event.metadata?.MediaType;
      const mediaType = typeof rawMediaType === "string" && /^[a-z0-9.+-]+\/[a-z0-9.+-]+$/iu.test(rawMediaType)
        ? rawMediaType : null;
      if (!text.trim() && !mediaType) return;
      const record = {
        schema_version: 1, account: ctx.accountId, conversation: conversationId,
        sender: event.senderId, message_id: event.messageId,
        received_at: new Date().toISOString(), text, media_type: mediaType,
        reply_to: typeof event.replyToId === "string" ? event.replyToId : null,
      };
      const fd = openSync(spoolPath,
        constants.O_WRONLY | constants.O_CREAT | constants.O_APPEND | (constants.O_NOFOLLOW ?? 0), 0o600);
      try {
        if (fstatSync(fd).mode & 0o077) throw new Error("Spool permissions must be private");
        const bytes = Buffer.from(JSON.stringify(record) + "\n");
        let offset = 0;
        while (offset < bytes.length) offset += writeSync(fd, bytes, offset);
        fsyncSync(fd);
      }
      finally { closeSync(fd); }
    });
    // The separate worker handles clarification; no general agent answers here.
    for (const hook of ["inbound_claim", "before_dispatch"])
      api.on(hook, async (event, ctx) => bound(event, ctx) ? { handled: true } : undefined);
    api.on("reply_dispatch", async (event, ctx) => {
      if (!replyBound(event)) return;
      ctx.recordProcessed("completed", { reason: "company_nora_delegated" });
      ctx.markIdle("message_completed");
      return { handled: true, queuedFinal: false, counts: { tool: 0, block: 0, final: 0 } };
    });
  },
};
