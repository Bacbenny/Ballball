// dekki-relay — Cloudflare Worker
// Resolves Footy Live sources via sportsembed handshake (protobuf + WASM crypto),
// decrypts the HLS playlist URL, and proxies playlist + segments with the
// correct Referer so IPTV clients can play without a browser.

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,HEAD,OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Relay-Token",
};

const FOOTYLIVE_STREAMS_URL = "https://footylive.vercel.app/api/streams/";
const EMBED_ORIGIN = "https://sportsembed.su";
const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36";

// ── helpers ────────────────────────────────────────────────────────────────

function jsonResp(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json; charset=utf-8" },
  });
}

function bytesToBase64(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

function base64ToBytes(value) {
  const binary = atob(value);
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

function base64UrlEncode(value) {
  return bytesToBase64(new TextEncoder().encode(value))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function base64UrlDecode(value) {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (value.length % 4)) % 4);
  return new TextDecoder().decode(base64ToBytes(padded));
}

function hexStr(bytes) {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function concatBytes(...arrays) {
  const size = arrays.reduce((t, a) => t + a.length, 0);
  const out = new Uint8Array(size);
  let off = 0;
  for (const a of arrays) {
    out.set(a, off);
    off += a.length;
  }
  return out;
}

function varint(n) {
  const bytes = [];
  let v = n;
  while (v > 0x7f) {
    bytes.push((v & 0x7f) | 0x80);
    v >>>= 7;
  }
  bytes.push(v);
  return new Uint8Array(bytes);
}

function protoField(field, value) {
  const body = new TextEncoder().encode(value);
  return concatBytes(new Uint8Array([(field << 3) | 2]), varint(body.length), body);
}

function encodeRequest({ category, slug, stream, matchId }) {
  return concatBytes(
    protoField(1, category),
    protoField(2, slug),
    protoField(3, stream),
    protoField(4, matchId),
  );
}

function pack(op, chunks) {
  const parts = [new Uint8Array([op])];
  for (const chunk of chunks) {
    const len = new Uint8Array(4);
    new DataView(len.buffer).setUint32(0, chunk.length, true);
    parts.push(len, chunk);
  }
  return concatBytes(...parts);
}

// ── WASM crypto ────────────────────────────────────────────────────────────

let wasmInstance = null;

async function getWasm(env) {
  if (wasmInstance) return wasmInstance;
  const { instance } = await WebAssembly.instantiate(env.STREAM_LOCK);
  wasmInstance = instance.exports;
  return wasmInstance;
}

async function wasmDispatch(env, input) {
  const wasm = await getWasm(env);
  const memory = new Uint8Array(wasm.memory.buffer);
  const view = new DataView(wasm.memory.buffer);
  const ptr = wasm.zonl3736033c71(input.length, 1);
  memory.set(input, ptr);
  const retptr = wasm.yojc788d654767(-8);
  wasm.juut545fd2befc(retptr, ptr, input.length);
  const outPtr = view.getUint32(retptr, true);
  const outLen = view.getUint32(retptr + 4, true);
  return memory.slice(outPtr, outPtr + outLen);
}

// ── embed handshake ─────────────────────────────────────────────────────────

async function resolveEmbed(env, embedUrl) {
  const url = new URL(embedUrl);
  const match = url.pathname.match(/^\/embed\/(\d+)\/([^/]+)\/([^/]+)\/(\d+)\/?$/);
  if (!match || url.hostname !== "sportsembed.su") throw new Error("Invalid embed URL");

  const [, matchId, slug, category, stream] = match;
  const body = encodeRequest({ matchId, slug, category, stream });
  const nonce = crypto.getRandomValues(new Uint8Array(32));

  const factor = await wasmDispatch(env, pack(0x17, [body, nonce]));
  if (factor.length !== 16) throw new Error("Invalid client factor");
  const proof = new TextDecoder().decode(await wasmDispatch(env, pack(0x29, [body, nonce, factor])));
  if (!/^[0-9a-f]{64}$/.test(proof)) throw new Error("Invalid client proof");

  const upstream = await fetch(`${EMBED_ORIGIN}/api/get`, {
    method: "POST",
    headers: {
      "Content-Type": "application/octet-stream",
      Origin: EMBED_ORIGIN,
      Referer: `${EMBED_ORIGIN}/embed/${matchId}/${slug}/${category}/${stream}`,
      "User-Agent": USER_AGENT,
      "x-client-nonce": bytesToBase64(nonce),
      "x-client-factor": bytesToBase64(factor),
      "x-client-proof": proof,
    },
    body,
  });
  if (!upstream.ok) throw new Error(`Embed /api/get returned ${upstream.status}`);

  const encrypted = new Uint8Array(await upstream.arrayBuffer());
  const live = upstream.headers.get("x-live") || "";
  const edge = base64ToBytes(upstream.headers.get("x-edge") || "");
  const bodyTag = base64ToBytes(upstream.headers.get("x-body-tag") || "");
  const keyHex = live.split("_").pop() || "";
  const key = Uint8Array.from(keyHex.match(/.{2}/g) || [], (h) => parseInt(h, 16));
  if (key.length !== 16 || edge.length !== 16 || bodyTag.length !== 8)
    throw new Error("Invalid embed response headers");

  const streamUrlBytes = await wasmDispatch(
    env,
    pack(0x3b, [encrypted, concatBytes(key, edge), nonce, factor, bodyTag]),
  );
  const streamUrl = new TextDecoder().decode(streamUrlBytes).trim();
  if (!streamUrl.startsWith("https://")) throw new Error("Embed did not return an HLS URL");

  return { streamUrl, embed: `${matchId}/${slug}/${category}/${stream}` };
}

// ── signed proxy URLs ──────────────────────────────────────────────────────

async function hmac(secret, value) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return hexStr(new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value))));
}

async function buildProxyUrl(origin, target, embed, secret, expires) {
  const payload = `${expires}|${embed}|${target}`;
  const sig = await hmac(secret, payload);
  const url = new URL(`${origin}/footylive/${embed.split("/")[0]}`);
  url.searchParams.set("u", base64UrlEncode(target));
  url.searchParams.set("e", base64UrlEncode(embed));
  url.searchParams.set("x", String(expires));
  url.searchParams.set("s", sig);
  return url.toString();
}

async function verifyProxy(requestUrl, env) {
  const url = new URL(requestUrl);
  const encodedTarget = url.searchParams.get("u");
  const encodedEmbed = url.searchParams.get("e");
  const expires = url.searchParams.get("x");
  const provided = url.searchParams.get("s");
  if (!encodedTarget || !encodedEmbed || !expires || !provided)
    throw new Error("Missing proxy parameters");
  if (Number(expires) < Date.now()) throw new Error("Expired stream URL");

  const target = base64UrlDecode(encodedTarget);
  const embed = base64UrlDecode(encodedEmbed);
  if (!target.startsWith("https://")) throw new Error("Invalid stream target");

  const expected = await hmac(env.RELAY_SECRET, `${expires}|${embed}|${target}`);
  if (provided !== expected) throw new Error("Invalid proxy signature");

  return { target, embed };
}

// ── HLS relay ───────────────────────────────────────────────────────────────

function absoluteUrl(value, base) {
  return new URL(value, base).toString();
}

function isPlaylist(contentType, body) {
  const head = body.toString("utf8", 0, Math.min(body.length, 256));
  if (head.includes("#EXTM3U")) return true;
  return contentType.includes("mpegurl") || (contentType.includes("text/plain") && head.includes("#EXT"));
}

async function rewritePlaylist(text, baseUrl, embed, origin, secret, expires) {
  const lines = text.split(/\r?\n/);
  const out = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      out.push(line);
      continue;
    }
    if (trimmed.startsWith("#")) {
      if (trimmed.includes('URI="') && !trimmed.includes('URI="data:')) {
        let rebuilt = line;
        const matches = [...line.matchAll(/URI="([^"]+)"/g)];
        for (const m of matches) {
          if (!m[1].startsWith("data:")) {
            const absolute = absoluteUrl(m[1], baseUrl);
            const proxied = await buildProxyUrl(origin, absolute, embed, secret, expires);
            rebuilt = rebuilt.replace(m[0], `URI="${proxied}"`);
          }
        }
        out.push(rebuilt);
      } else {
        out.push(line);
      }
      continue;
    }
    out.push(await buildProxyUrl(origin, absoluteUrl(trimmed, baseUrl), embed, secret, expires));
  }
  return out.join("\n");
}

async function serveProxy(requestUrl, env) {
  const { target, embed } = await verifyProxy(requestUrl, env);
  const upstream = await fetch(target, {
    headers: { Accept: "*/*", Referer: `${EMBED_ORIGIN}/`, "User-Agent": USER_AGENT },
  });
  if (!upstream.ok) return jsonResp({ error: `Upstream returned ${upstream.status}` }, 502);

  const contentType = upstream.headers.get("content-type") || "";
  const body = await upstream.arrayBuffer();
  const bodyBytes = new Uint8Array(body);

  if (!isPlaylist(contentType, bodyBytes)) {
    return new Response(body, {
      status: 200,
      headers: {
        ...CORS,
        "Content-Type": contentType || "video/mp2t",
        "Cache-Control": "no-store",
      },
    });
  }

  const text = new TextDecoder().decode(bodyBytes);
  const origin = new URL(requestUrl).origin;
  const expires = Date.now() + 1800000;
  const rewritten = await rewritePlaylist(text, target, embed, origin, env.RELAY_SECRET, expires);
  return new Response(rewritten, {
    status: 200,
    headers: {
      ...CORS,
      "Content-Type": "application/vnd.apple.mpegurl",
      "Cache-Control": "no-store",
    },
  });
}

// ── Footy Live match resolution ─────────────────────────────────────────────

function pickSource(payload, matchId) {
  const streams = Array.isArray(payload?.streams) ? payload.streams : [];
  if (!streams.length) throw new Error(`No streams for match ${matchId}`);
  const source =
    streams.find((s) => s?.url && String(s.provider || "").toLowerCase().includes("watchfooty")) ||
    streams.find((s) => s?.url);
  if (!source?.url) throw new Error(`No embed URL for match ${matchId}`);
  return source.url;
}

async function handleMatch(request, env, matchId) {
  const requestUrl = new URL(request.url);

  if (requestUrl.searchParams.has("u")) {
    return serveProxy(request.url, env);
  }

  const api = await fetch(`${FOOTYLIVE_STREAMS_URL}${encodeURIComponent(matchId)}`, {
    headers: { Accept: "application/json", "User-Agent": USER_AGENT },
  });
  if (!api.ok) return jsonResp({ error: `Footy Live API returned ${api.status}` }, 502);
  const payload = await api.json();
  const embedUrl = pickSource(payload, matchId);

  const { streamUrl, embed } = await resolveEmbed(env, embedUrl);

  const playlist = await fetch(streamUrl, {
    headers: { Accept: "*/*", Referer: `${EMBED_ORIGIN}/`, "User-Agent": USER_AGENT },
  });
  if (!playlist.ok) return jsonResp({ error: `HLS upstream returned ${playlist.status}` }, 502);

  const text = await playlist.text();
  const expires = Date.now() + 1800000;
  const rewritten = await rewritePlaylist(text, streamUrl, embed, requestUrl.origin, env.RELAY_SECRET, expires);

  return new Response(rewritten, {
    status: 200,
    headers: {
      ...CORS,
      "Content-Type": "application/vnd.apple.mpegurl",
      "Cache-Control": "no-store",
    },
  });
}

// ── entry ────────────────────────────────────────────────────────────────────

export default {
  async fetch(req, env) {
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

    const url = new URL(req.url);

    if (url.pathname === "/healthz") {
      return jsonResp({
        ok: true,
        worker: "dekki-relay",
        resolver: "sportsembed-handshake",
        relay_secret_set: Boolean(env.RELAY_SECRET),
        wasm_loaded: Boolean(env.STREAM_LOCK),
      });
    }

    const footyMatch = url.pathname.match(/^\/footylive\/([^/]+)$/);
    if (footyMatch && (req.method === "GET" || req.method === "HEAD")) {
      try {
        return await handleMatch(req, env, decodeURIComponent(footyMatch[1]));
      } catch (error) {
        return jsonResp({ error: "Footy Live resolver failed", detail: String(error?.message || error) }, 502);
      }
    }

    return jsonResp({ error: "Not found. Use /healthz" }, 404);
  },
};
