/**
 * Blip License & YooMoney/SBP Cloudflare Worker
 * Zero dependencies: uses native Web Crypto (Ed25519 + SHA-1) and Cloudflare KV.
 */

const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}

function bytesToBase64Url(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function constantTimeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function sha1Hex(text) {
  const buf = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function signLicensePayload(payloadObj, privateJwkStr) {
  const jwk = typeof privateJwkStr === "string" ? JSON.parse(privateJwkStr) : privateJwkStr;
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "Ed25519" }, false, ["sign"]);
  const payloadJson = JSON.stringify(payloadObj);
  const sigBuf = await crypto.subtle.sign("Ed25519", key, new TextEncoder().encode(payloadJson));
  return {
    payload: payloadJson,
    signature: bytesToBase64Url(new Uint8Array(sigBuf)),
    alg: "Ed25519",
  };
}

function randomOrderId() {
  const buf = new Uint8Array(8);
  crypto.getRandomValues(buf);
  const hex = Array.from(buf)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .toUpperCase();
  return `BLIP-${hex}`;
}

function buildYooMoneyPayUrl(wallet, amountRub, orderId) {
  const params = new URLSearchParams({
    receiver: String(wallet || "").trim(),
    "quickpay-form": "shop",
    targets: `Blip PRO Lifetime (${orderId})`,
    sum: String(amountRub),
    label: orderId,
  });
  return `https://yoomoney.ru/quickpay/confirm?${params.toString()}`;
}

export async function handleRequest(request, env) {
  const url = new URL(request.url);
  const path = url.pathname;

  if (request.method === "GET" && path === "/health") {
    return json({
      ok: true,
      service: "blip-license-worker",
      product: env.PRODUCT_NAME || "Blip",
      min_amount_rub: Number(env.MIN_AMOUNT_RUB || 10),
    });
  }

  // 1. Create pending payment order & return YooMoney / SBP checkout URL for QR code
  if (request.method === "POST" && path === "/order/create") {
    let body;
    try {
      body = await request.json();
    } catch {
      return json({ ok: false, error: "invalid_json" }, 400);
    }
    const hwid = String(body?.hwid || "").trim();
    if (hwid.length < 16 || hwid.length > 128) {
      return json({ ok: false, error: "invalid_hwid" }, 400);
    }
    if (!env.YOOMONEY_WALLET) {
      return json({ ok: false, error: "wallet_not_configured" }, 500);
    }

    const amountRub = Number(env.MIN_AMOUNT_RUB || 10);
    const orderId = randomOrderId();
    const record = {
      order_id: orderId,
      hwid,
      tier: "pro",
      amount_rub: amountRub,
      status: "pending",
      created_at: new Date().toISOString(),
    };
    await env.BLIP_KV.put(`order:${orderId}`, JSON.stringify(record), { expirationTtl: 86400 });

    return json({
      ok: true,
      order_id: orderId,
      amount_rub: amountRub,
      currency: "RUB",
      pay_url: buildYooMoneyPayUrl(env.YOOMONEY_WALLET, amountRub, orderId),
      expires_in: 86400,
    });
  }

  // 2. YooMoney HTTP-notification webhook
  if (request.method === "POST" && path === "/yoomoney/webhook") {
    const formText = await request.text();
    const form = new URLSearchParams(formText);

    const notificationType = form.get("notification_type") || "";
    const operationId = form.get("operation_id") || "";
    const amount = form.get("amount") || "";
    const withdrawAmount = form.get("withdraw_amount") || amount;
    const currency = form.get("currency") || "";
    const datetime = form.get("datetime") || "";
    const sender = form.get("sender") || "";
    const codepro = form.get("codepro") || "false";
    const label = form.get("label") || "";
    const sha1Hash = (form.get("sha1_hash") || "").toLowerCase();
    const unaccepted = form.get("unaccepted") || "false";

    if (!env.YOOMONEY_SECRET || !env.ED25519_PRIVATE_JWK) {
      return json({ ok: false, error: "secrets_not_configured" }, 500);
    }

    const sigBase = [
      notificationType,
      operationId,
      amount,
      currency,
      datetime,
      sender,
      codepro,
      env.YOOMONEY_SECRET,
      label,
    ].join("&");
    const expectedHash = await sha1Hex(sigBase);
    if (!constantTimeEqual(expectedHash, sha1Hash)) {
      return json({ ok: false, error: "invalid_signature" }, 403);
    }

    if (codepro === "true" || unaccepted === "true") {
      return json({ ok: false, error: "payment_held_or_unaccepted" }, 400);
    }

    const minAmount = Number(env.MIN_AMOUNT_RUB || 10);
    const paidAmount = Number(withdrawAmount || amount || 0);
    if (!Number.isFinite(paidAmount) || paidAmount + 0.01 < minAmount) {
      return json({ ok: false, error: "insufficient_amount" }, 400);
    }

    if (!label) {
      return json({ ok: true, ignored: "empty_label" });
    }

    const orderKey = `order:${label}`;
    const rawOrder = await env.BLIP_KV.get(orderKey);
    if (!rawOrder) {
      return json({ ok: true, ignored: "unknown_order" });
    }
    const order = JSON.parse(rawOrder);
    if (order.status === "paid" && order.license) {
      return json({ ok: true, already_paid: true });
    }

    const licensePayload = {
      product: env.PRODUCT_NAME || "Blip",
      tier: "pro",
      hwid: order.hwid,
      order_id: label,
      operation_id: operationId,
      amount_rub: paidAmount,
      issued_at: new Date().toISOString(),
    };
    const license = await signLicensePayload(licensePayload, env.ED25519_PRIVATE_JWK);
    order.status = "paid";
    order.paid_at = licensePayload.issued_at;
    order.operation_id = operationId;
    order.license = license;

    await env.BLIP_KV.put(orderKey, JSON.stringify(order), { expirationTtl: 86400 * 7 });
    return json({ ok: true });
  }

  // 3. Poll order status from Blip app
  if (request.method === "GET" && path === "/order/status") {
    const orderId = (url.searchParams.get("order_id") || "").trim();
    const hwid = (url.searchParams.get("hwid") || "").trim();
    if (!orderId || !hwid) {
      return json({ ok: false, error: "missing_params" }, 400);
    }
    const rawOrder = await env.BLIP_KV.get(`order:${orderId}`);
    if (!rawOrder) {
      return json({ ok: false, status: "not_found" }, 404);
    }
    const order = JSON.parse(rawOrder);
    if (!constantTimeEqual(order.hwid || "", hwid)) {
      return json({ ok: false, error: "hwid_mismatch" }, 403);
    }
    if (order.status === "paid" && order.license) {
      return json({ ok: true, status: "paid", license: order.license });
    }
    return json({ ok: true, status: order.status || "pending" });
  }

  // 4. Activate a single-use random code from the 10,000 pool
  if (request.method === "POST" && path === "/code/activate") {
    let body;
    try {
      body = await request.json();
    } catch {
      return json({ ok: false, error: "invalid_json" }, 400);
    }
    const code = String(body?.code || "").trim().toUpperCase();
    const hwid = String(body?.hwid || "").trim();
    if (!code || hwid.length < 16) {
      return json({ ok: false, error: "invalid_params" }, 400);
    }
    if (!env.ED25519_PRIVATE_JWK) {
      return json({ ok: false, error: "secrets_not_configured" }, 500);
    }

    const codeKey = `code:${code}`;
    const rawCode = await env.BLIP_KV.get(codeKey);
    if (!rawCode) {
      return json({ ok: false, error: "invalid_code" }, 404);
    }
    const rec = JSON.parse(rawCode);
    if (rec.used && !constantTimeEqual(rec.hwid || "", hwid)) {
      return json({ ok: false, error: "code_already_used" }, 409);
    }

    const nowIso = new Date().toISOString();
    const licensePayload = {
      product: env.PRODUCT_NAME || "Blip",
      tier: "pro",
      hwid,
      code,
      issued_at: rec.issued_at || nowIso,
    };
    const license = await signLicensePayload(licensePayload, env.ED25519_PRIVATE_JWK);
    if (!rec.used) {
      await env.BLIP_KV.put(
        codeKey,
        JSON.stringify({ used: true, hwid, issued_at: nowIso }),
      );
    }
    return json({ ok: true, license });
  }

  // 5. Admin endpoint to seed batches of codes into KV
  if (request.method === "POST" && path === "/admin/seed-codes") {
    const auth = request.headers.get("authorization") || "";
    const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
    if (!env.ADMIN_TOKEN || !constantTimeEqual(token, env.ADMIN_TOKEN)) {
      return json({ ok: false, error: "unauthorized" }, 401);
    }
    let body;
    try {
      body = await request.json();
    } catch {
      return json({ ok: false, error: "invalid_json" }, 400);
    }
    const codes = Array.isArray(body?.codes) ? body.codes : [];
    let added = 0;
    for (const c of codes.slice(0, 500)) {
      const norm = String(c || "").trim().toUpperCase();
      if (!norm) continue;
      await env.BLIP_KV.put(`code:${norm}`, JSON.stringify({ used: false }));
      added += 1;
    }
    return json({ ok: true, added });
  }

  return json({ ok: false, error: "not_found" }, 404);
}

export default {
  fetch: handleRequest,
};
