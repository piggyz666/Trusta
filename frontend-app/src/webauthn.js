// Face / fingerprint / security key (WebAuthn). Browsers allow it on https or http://localhost only.
const toBytes = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), (c) => c.charCodeAt(0));
const toB64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

export function webauthnStatus() {
  if (typeof window === "undefined" || !window.PublicKeyCredential) return { ok: false, reason: "当前浏览器不支持刷脸 / 指纹验证" };
  if (!window.isSecureContext) return { ok: false, reason: "需要用 https 或 http://localhost 打开 TRUSTA 才能使用" };
  if (/^\d+\.\d+\.\d+\.\d+$|^\[/.test(location.hostname))
    return { ok: false, reason: `请改用 http://localhost:${location.port || 80} 打开（刷脸 / 指纹不支持 IP 地址）` };
  return { ok: true };
}

export async function createCredential(options) {
  const credential = await navigator.credentials.create({ publicKey: {
    ...options, challenge: toBytes(options.challenge), user: { ...options.user, id: toBytes(options.user.id) },
    excludeCredentials: (options.excludeCredentials || []).map((c) => ({ ...c, id: toBytes(c.id) })),
  } });
  return { id: toB64(credential.rawId), clientDataJSON: toB64(credential.response.clientDataJSON),
    attestationObject: toB64(credential.response.attestationObject) };
}

// Signs the challenge of ONE authorization; returns the string sent as verification.code with method WEBAUTHN.
export async function getAssertion(options) {
  const assertion = await navigator.credentials.get({ publicKey: {
    ...options, challenge: toBytes(options.challenge), allowCredentials: options.allowCredentials.map((c) => ({ ...c, id: toBytes(c.id) })),
  } });
  return JSON.stringify({ id: toB64(assertion.rawId), clientDataJSON: toB64(assertion.response.clientDataJSON),
    authenticatorData: toB64(assertion.response.authenticatorData), signature: toB64(assertion.response.signature) });
}
