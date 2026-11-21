import test from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import { handleRequest } from "../src/index.js";

function makeMemoryKV() {
  const store = new Map();
  return {
    async get(key) {
      return store.has(key) ? store.get(key) : null;
    },
    async put(key, value) {
      store.set(key, String(value));
    },
    _store: store,
  };
}

async function makeEnv() {
  const kp = await crypto.webcrypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const privJwk = await crypto.webcrypto.subtle.exportKey("jwk", kp.privateKey);
  return {
    env: {
      BLIP_KV: makeMemoryKV(),
      YOOMONEY_WALLET: "410010000000000",
      YOOMONEY_SECRET: "test_yoomoney_secret",
      ED25519_PRIVATE_JWK: JSON.stringify(privJwk),
      ADMIN_TOKEN: "adm_secret_123",
      MIN_AMOUNT_RUB: "300",
      PRODUCT_NAME: "Blip",
    },
    pubKey: kp.publicKey,
  };
}

function sha1Hex(str) {
  return crypto.createHash("sha1").update(str, "utf8").digest("hex");
}

function base64UrlToBytes(b64u) {
  const b64 = b64u.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((b64u.length + 3) % 4);
  return new Uint8Array(Buffer.from(b64, "base64"));
}

test("order create -> YooMoney webhook -> Ed25519 verified license", async () => {
  const { env, pubKey } = await makeEnv();
  const hwid = "a".repeat(64);

  // 1. Create order
  const createRes = await handleRequest(
    new Request("https://worker.test/order/create", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ hwid }),
    }),
    env,
  );
  assert.equal(createRes.status, 200);
  const created = await createRes.json();
  assert.equal(created.ok, true);
  assert.match(created.order_id, /^BLIP-[0-9A-F]{16}$/);
  assert.ok(created.pay_url.includes("yoomoney.ru/quickpay/confirm"));
  assert.ok(created.pay_url.includes(created.order_id));

  // 2. Reject invalid webhook signature
  const badHook = await handleRequest(
    new Request("https://worker.test/yoomoney/webhook", {
      method: "POST",
      body: new URLSearchParams({
        notification_type: "p2p-incoming",
        operation_id: "op1",
        amount: "300.00",
        currency: "643",
        datetime: "2026-11-15T05:00:00Z",
        sender: "41001111",
        codepro: "false",
        label: created.order_id,
        sha1_hash: "0".repeat(40),
      }).toString(),
    }),
    env,
  );
  assert.equal(badHook.status, 403);

  // 3. Valid YooMoney webhook
  const fields = {
    notification_type: "p2p-incoming",
    operation_id: "op_valid_1",
    amount: "300.00",
    withdraw_amount: "300.00",
    currency: "643",
    datetime: "2026-11-15T05:00:00Z",
    sender: "41001111",
    codepro: "false",
    label: created.order_id,
  };
  const sigStr = [
    fields.notification_type,
    fields.operation_id,
    fields.amount,
    fields.currency,
    fields.datetime,
    fields.sender,
    fields.codepro,
    env.YOOMONEY_SECRET,
    fields.label,
  ].join("&");
  fields.sha1_hash = sha1Hex(sigStr);

  const okHook = await handleRequest(
    new Request("https://worker.test/yoomoney/webhook", {
      method: "POST",
      body: new URLSearchParams(fields).toString(),
    }),
    env,
  );
  assert.equal(okHook.status, 200);

  // 4. Poll status & verify Ed25519 signature
  const statusRes = await handleRequest(
    new Request(`https://worker.test/order/status?order_id=${created.order_id}&hwid=${hwid}`),
    env,
  );
  assert.equal(statusRes.status, 200);
  const statusData = await statusRes.json();
  assert.equal(statusData.status, "paid");
  assert.equal(statusData.license.alg, "Ed25519");

  const verified = await crypto.webcrypto.subtle.verify(
    "Ed25519",
    pubKey,
    base64UrlToBytes(statusData.license.signature),
    new TextEncoder().encode(statusData.license.payload),
  );
  assert.equal(verified, true);
});

test("single-use code activation allows same HWID reinstall and blocks other HWID", async () => {
  const { env, pubKey } = await makeEnv();
  await env.BLIP_KV.put("code:BLIP-TEST-1234-ABCD", JSON.stringify({ used: false }));

  const hwid1 = "1".repeat(64);
  const hwid2 = "2".repeat(64);

  const r1 = await handleRequest(
    new Request("https://worker.test/code/activate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ code: "blip-test-1234-abcd", hwid: hwid1 }),
    }),
    env,
  );
  assert.equal(r1.status, 200);
  const d1 = await r1.json();
  const okSig = await crypto.webcrypto.subtle.verify(
    "Ed25519",
    pubKey,
    base64UrlToBytes(d1.license.signature),
    new TextEncoder().encode(d1.license.payload),
  );
  assert.equal(okSig, true);

  // Same HWID reinstall -> allowed
  const rSame = await handleRequest(
    new Request("https://worker.test/code/activate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ code: "BLIP-TEST-1234-ABCD", hwid: hwid1 }),
    }),
    env,
  );
  assert.equal(rSame.status, 200);

  // Different HWID -> blocked 409
  const rOther = await handleRequest(
    new Request("https://worker.test/code/activate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ code: "BLIP-TEST-1234-ABCD", hwid: hwid2 }),
    }),
    env,
  );
  assert.equal(rOther.status, 409);
});
