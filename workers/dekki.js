// dekki-relay — Cloudflare Worker
// (Reserved for future relay needs — currently no active routes)
// Auth: X-Relay-Token header == RELAY_SECRET env binding

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

    return jsonResp({ error: "Not found. Use /healthz" }, 404);
  },
};
