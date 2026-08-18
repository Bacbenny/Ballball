// dekki-relay — Cloudflare Worker
// Resolves Footy Live sources at click time and redirects to one best source.

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type,X-Relay-Token,Authorization",
};

function jsonResp(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

const FOOTYLIVE_MATCHES_URL = "https://footylive.vercel.app/api/matches";

function qualityRank(quality) {
  const value = String(quality || "").toUpperCase().replace(/\s/g, "");
  if (["4K", "2160P", "UHD", "FHD", "1080P", "1080"].includes(value)) return 0;
  if (["HD", "720P", "720"].includes(value)) return 1;
  if (["SD", "480P", "480", "360P", "360"].includes(value)) return 2;
  return 3;
}

function providerRank(source) {
  const provider = String(source?.provider || source?.source || "").toLowerCase();
  if (provider.includes("watchfooty") || provider.startsWith("wf-")) return 0;
  if (provider.includes("streamed")) return 1;
  if (provider.includes("cdn")) return 2;
  return 3;
}

function chooseSource(sources) {
  const candidates = [];
  const seen = new Set();
  for (const [index, source] of (sources || []).entries()) {
    if (!source || typeof source !== "object") continue;
    const streamUrl = String(source.url || "").trim();
    if (!streamUrl || seen.has(streamUrl)) continue;
    seen.add(streamUrl);
    candidates.push({ source, index });
  }
  candidates.sort((a, b) =>
    qualityRank(a.source.quality) - qualityRank(b.source.quality) ||
    providerRank(a.source) - providerRank(b.source) ||
    a.index - b.index
  );
  return candidates[0]?.source || null;
}

function absoluteUrl(value) {
  const raw = String(value || "").trim();
  return raw ? new URL(raw, FOOTYLIVE_MATCHES_URL).toString() : "";
}

async function resolveFootyLive(matchId) {
  const response = await fetch(FOOTYLIVE_MATCHES_URL, {
    headers: {
      Accept: "application/json",
      "User-Agent": "dekki-footylive-relay/1.0",
    },
  });
  if (!response.ok) {
    return jsonResp({ error: "Footy Live API unavailable" }, 502);
  }

  const payload = await response.json();
  const matches = Array.isArray(payload) ? payload : payload?.matches;
  const match = (matches || []).find(
    (item) => String(item?.id || "") === String(matchId)
  );
  if (!match) return jsonResp({ error: "Match not found" }, 404);

  const source = chooseSource(match.sources);
  const streamUrl = absoluteUrl(source?.url);
  if (!streamUrl || !/^https?:$/.test(new URL(streamUrl).protocol)) {
    return jsonResp({ error: "No stream is available yet" }, 404);
  }

  return Response.redirect(streamUrl, 302);
}

export default {
  async fetch(req, env) {
    if (req.method === "OPTIONS") return new Response(null, { headers: CORS });

    const url  = new URL(req.url);
    const path = url.pathname.replace(/\/$/, "") || "/";

    if (path === "/healthz") {
      const sec = env.RELAY_SECRET || "";
      return jsonResp({
        ok: true,
        worker: "dekki-relay",
        relay_secret_set: !!sec,
      });
    }

    const footyMatch = path.match(/^\/footylive\/([^/]+)$/);
    if (footyMatch && (req.method === "GET" || req.method === "HEAD")) {
      try {
        return await resolveFootyLive(decodeURIComponent(footyMatch[1]));
      } catch (error) {
        return jsonResp({ error: "Footy Live resolver failed" }, 502);
      }
    }

    return jsonResp({ error: "Not found. Use /healthz" }, 404);
  },
};
