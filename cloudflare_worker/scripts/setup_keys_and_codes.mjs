/**
 * Generates:
 *  1) Ed25519 keypair (private JWK for Cloudflare Worker secret + public JWK/base64url for Blip client)
 *  2) 10,000 unique single-use random PRO codes (codes_10000.txt + kv_bulk_10000.json for wrangler kv bulk put)
 *
 * Usage: node cloudflare_worker/scripts/setup_keys_and_codes.mjs
 */

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const secretsDir = path.resolve(__dirname, "..", ".secrets");
fs.mkdirSync(secretsDir, { recursive: true });

// 1. Ed25519 Keypair (WebCrypto JWK)
const kp = await crypto.webcrypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
const privJwk = await crypto.webcrypto.subtle.exportKey("jwk", kp.privateKey);
const pubJwk = await crypto.webcrypto.subtle.exportKey("jwk", kp.publicKey);
const adminToken = crypto.randomBytes(24).toString("hex");

fs.writeFileSync(path.join(secretsDir, "ed25519_private.jwk.json"), JSON.stringify(privJwk), "utf8");
fs.writeFileSync(path.join(secretsDir, "ed25519_public.jwk.json"), JSON.stringify(pubJwk, null, 2), "utf8");
fs.writeFileSync(path.join(secretsDir, "admin_token.txt"), adminToken, "utf8");

// 2. 10,000 random single-use codes
const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // no ambiguous 0/O, 1/I
function makeCode() {
  const bytes = crypto.randomBytes(12);
  let raw = "";
  for (let i = 0; i < 12; i++) {
    raw += ALPHABET[bytes[i] % ALPHABET.length];
  }
  return `BLIP-${raw.slice(0, 4)}-${raw.slice(4, 8)}-${raw.slice(8, 12)}`;
}

const codesSet = new Set();
while (codesSet.size < 10000) {
  codesSet.add(makeCode());
}
const codes = Array.from(codesSet);
fs.writeFileSync(path.join(secretsDir, "codes_10000.txt"), codes.join("\n") + "\n", "utf8");

const kvBulk = codes.map((c) => ({
  key: `code:${c}`,
  value: JSON.stringify({ used: false }),
}));
fs.writeFileSync(path.join(secretsDir, "kv_bulk_10000.json"), JSON.stringify(kvBulk), "utf8");

console.log(`Generated Ed25519 keys + 10,000 codes in ${secretsDir}`);
console.log(`Public key (x): ${pubJwk.x}`);
