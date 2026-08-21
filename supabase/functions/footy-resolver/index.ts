import { WASM_BASE64 } from "./wasm.ts";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Client-Info, Apikey",
};

const FOOTYLIVE_MATCHES_URL = "https://footylive.vercel.app/api/matches";
const FOOTYLIVE_STREAMS_URL = "https://footylive.vercel.app/api/streams/";
const WATCHFOOTY_MATCH_URL = "https://api.watchfooty.st/api/v1/match/";
const EMBED_ORIGIN = "https://sportsembed.su";
const USER_AGENT =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36";

// ── WASM ──────────────────────────────────────────────
let wasmExports: any = null;
let wasmInit: Promise<any> | null = null;

async function getWasm(): Promise<any> {
  if (wasmExports) return wasmExports;
  if (!wasmInit) {
    wasmInit = (async () => {
      const binary = Uint8Array.from(atob(WASM_BASE64), (c) => c.charCodeAt(0));
      const { instance } = await WebAssembly.instantiate(binary, {});
      wasmExports = instance.exports;
      return wasmExports;
    })();
  }
  return await wasmInit;
}

function concatBytes(...arrays: Uint8Array[]): Uint8Array {
  const size = arrays.reduce((t, a) => t + a.length, 0);
  const out = new Uint8Array(size);
  let off = 0;
  for (const a of arrays) { out.set(a, off); off += a.length; }
  return out;
}

function pack(op: number, chunks: Uint8Array[]): Uint8Array {
  const parts: Uint8Array[] = [new Uint8Array([op])];
  for (const chunk of chunks) {
    const len = new Uint8Array(4);
    new DataView(len.buffer).setUint32(0, chunk.length, true);
    parts.push(len, chunk);
  }
  return concatBytes(...parts);
}

async function wasmDispatch(input: Uint8Array): Promise<Uint8Array> {
  const wasm = await getWasm();
  const ptr = wasm.zonl3736033c71(input.length, 1);
  new Uint8Array(wasm.memory.buffer).set(input, ptr);
  const retptr = wasm.yojc788d654767(-8);
  wasm.juut545fd2befc(retptr, ptr, input.length);
  const view = new DataView(wasm.memory.buffer);
  const outPtr = view.getUint32(retptr, true);
  const outLen = view.getUint32(retptr + 4, true);
  return new Uint8Array(wasm.memory.buffer).slice(outPtr, outPtr + outLen);
}

// ── Protobuf encoding ─────────────────────────────────
function varint(n: number): number[] {
  const bytes: number[] = [];
  let v = n;
  while (v > 0x7f) { bytes.push((v & 0x7f) | 0x80); v >>>= 7; }
  bytes.push(v);
  return bytes;
}

function encodeRequestBody(embed: {
  category: string; slug: string; stream: string; matchId: string;
}): Uint8Array {
  const parts: number[] = [];
  for (const [field, value] of [
    [1, embed.category], [2, embed.slug], [3, embed.stream], [4, embed.matchId],
  ] as [number, string][]) {
    const body = new TextEncoder().encode(value);
    parts.push((field << 3) | 2, ...varint(body.length), ...body);
  }
  return new Uint8Array(parts);
}

// ── Base64 helpers ─────────────────────────────────────
function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

function base64ToBytes(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

// ── Embed resolution ──────────────────────────────────
async function resolveEmbed(embedUrl: string): Promise<{
  streamUrl: string; embed: string;
}> {
  const url = new URL(embedUrl);
  const m = url.pathname.match(/^\/embed\/(\d+)\/([^/]+)\/([^/]+)\/(\d+)\/?$/);
  if (!m || url.hostname !== "sportsembed.su") throw new Error("Invalid embed URL");

  const matchId = m[1], slug = m[2], category = m[3], stream = m[4];
  const body = encodeRequestBody({ matchId, slug, category, stream });
  const nonce = crypto.getRandomValues(new Uint8Array(32));

  const factor = await wasmDispatch(pack(0x17, [body, nonce]));
  if (factor.length !== 16) throw new Error("Invalid client factor");

  const proofBytes = await wasmDispatch(pack(0x29, [body, nonce, factor]));
  const proof = new TextDecoder().decode(proofBytes);
  if (!/^[0-9a-f]{64}$/.test(proof)) throw new Error("Invalid client proof");

  const upstream = await fetch(EMBED_ORIGIN + "/api/get", {
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
  if (key.length !== 16 || edge.length !== 16 || bodyTag.length !== 8) {
    throw new Error("Invalid embed response headers");
  }

  const streamUrlBytes = await wasmDispatch(
    pack(0x3b, [encrypted, concatBytes(key, edge), nonce, factor, bodyTag]),
  );
  const streamUrl = new TextDecoder().decode(streamUrlBytes).trim();
  if (!streamUrl.startsWith("https://")) throw new Error("Embed did not return an HLS URL");

  return { streamUrl, embed: `${matchId}/${slug}/${category}/${stream}` };
}

// ── Source picking ─────────────────────────────────────
function pickSources(payload: any, matchId: string): string[] {
  const streams = Array.isArray(payload.streams) ? payload.streams : [];
  const valid = streams.filter((s: any) => s && s.url);
  if (!valid.length) throw new Error(`No streams for match ${matchId}`);
  const watch = valid.filter((s: any) =>
    String(s.provider || "").toLowerCase().includes("watchfooty"),
  );
  return (watch.length ? watch : valid).map((s: any) => s.url);
}

async function fetchMatchPayload(matchId: string): Promise<any> {
  const headers = { Accept: "application/json", "User-Agent": USER_AGENT };
  try {
    const api = await fetch(FOOTYLIVE_STREAMS_URL + encodeURIComponent(matchId), { headers });
    if (api.ok) {
      const payload = await api.json();
      if (Array.isArray(payload?.streams) && payload.streams.length) return payload;
    }
    throw new Error("Footy Live API returned no streams");
  } catch {
    const api2 = await fetch(WATCHFOOTY_MATCH_URL + encodeURIComponent(matchId), { headers });
    if (!api2.ok) throw new Error(`WatchFooty API returned ${api2.status}`);
    const match = await api2.json();
    const streams = Array.isArray(match?.streams) ? match.streams : [];
    if (!streams.length) throw new Error("WatchFooty API returned no streams");
    return { matchTitle: match.title || "", streams };
  }
}

// ── JSON helper ────────────────────────────────────────
function jsonResp(body: any, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json; charset=utf-8" },
  });
}

// ── Main handler ───────────────────────────────────────
Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

  const url = new URL(req.url);
  const path = url.pathname.replace(/^\/footy-resolver/, "");

  // Health check
  if (path === "/healthz" || path === "") {
    return jsonResp({
      ok: true,
      worker: "footy-resolver",
      resolver: "sportsembed-handshake",
      wasm_loaded: true,
    });
  }

  // M3U playlist: /footy-resolver/playlist.m3u — returns M3U of all live matches
  if (path === "/playlist.m3u") {
    try {
      const api = await fetch(FOOTYLIVE_MATCHES_URL, {
        headers: { Accept: "application/json", "User-Agent": USER_AGENT },
      });
      if (!api.ok) return jsonResp({ error: "Failed to fetch matches" }, 502);
      const payload = await api.json();
      const matches = Array.isArray(payload) ? payload : (payload.matches || []);
      const live = matches.filter((m: any) =>
        String(m.status || "").toLowerCase() === "live",
      );

      const origin = `https://${url.hostname}/functions/v1/footy-resolver`;
      let m3u = "#EXTM3U\n";
      for (const m of live) {
        const mid = m.id;
        const home = m.homeTeam?.name || "Home";
        const away = m.awayTeam?.name || "Away";
        const title = `${home} vs ${away}`;
        m3u += `#EXTINF:-1,${title}\n`;
        m3u += `${origin}/${mid}/raw\n`;
      }
      return new Response(m3u, {
        status: 200,
        headers: {
          ...corsHeaders,
          "Content-Type": "audio/x-mpegurl",
          "Cache-Control": "no-store",
        },
      });
    } catch (err: any) {
      return jsonResp({ error: "Playlist failed", detail: String(err?.message || err) }, 502);
    }
  }

  // Raw mode: /<id>/raw — 302 redirect to real m3u8
  const rawMatch = path.match(/^\/([^/]+)\/raw$/);
  if (rawMatch && (req.method === "GET" || req.method === "HEAD")) {
    try {
      const matchId = decodeURIComponent(rawMatch[1]);
      const payload = await fetchMatchPayload(matchId);
      const sources = pickSources(payload, matchId);
      let resolved: { streamUrl: string; embed: string } | null = null;
      const failures: string[] = [];
      for (const embedUrl of sources) {
        try {
          resolved = await resolveEmbed(embedUrl);
          break;
        } catch (err: any) {
          failures.push(`${embedUrl} ${String(err?.message || err)}`);
        }
      }
      if (!resolved) {
        return jsonResp({
          error: "Raw redirect failed",
          detail: `All ${sources.length} sources failed: ${failures.join("; ")}`,
        }, 502);
      }
      return new Response(null, {
        status: 302,
        headers: { ...corsHeaders, Location: resolved.streamUrl, "Cache-Control": "no-store" },
      });
    } catch (err: any) {
      return jsonResp({
        error: "Raw redirect failed",
        detail: String(err?.message || err),
      }, 502);
    }
  }

  // JSON mode: /<id>/json — return resolved URL as JSON
  const jsonMatch = path.match(/^\/([^/]+)\/json$/);
  if (jsonMatch && (req.method === "GET" || req.method === "HEAD")) {
    try {
      const matchId = decodeURIComponent(jsonMatch[1]);
      const payload = await fetchMatchPayload(matchId);
      const sources = pickSources(payload, matchId);
      let resolved: { streamUrl: string; embed: string } | null = null;
      const failures: string[] = [];
      for (const embedUrl of sources) {
        try {
          resolved = await resolveEmbed(embedUrl);
          break;
        } catch (err: any) {
          failures.push(`${embedUrl} ${String(err?.message || err)}`);
        }
      }
      if (!resolved) {
        return jsonResp({
          error: "Resolve failed",
          detail: `All ${sources.length} sources failed: ${failures.join("; ")}`,
        }, 502);
      }
      return jsonResp({
        ok: true,
        matchId,
        streamUrl: resolved.streamUrl,
        embed: resolved.embed,
        expires: Date.now() + 21600000,
      });
    } catch (err: any) {
      return jsonResp({
        error: "Resolve failed",
        detail: String(err?.message || err),
      }, 502);
    }
  }

  return jsonResp({
    error: "Not found",
    usage: {
      health: "/footy-resolver/healthz",
      playlist: "/footy-resolver/playlist.m3u",
      raw: "/footy-resolver/<matchId>/raw",
      json: "/footy-resolver/<matchId>/json",
    },
  }, 404);
});
