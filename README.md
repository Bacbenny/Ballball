# Footy Resolver — HLS Stream Resolver for SportsEmbed

Supabase Edge Function (Deno) that resolves encrypted HLS stream URLs from sportsembed.su using a WASM-based handshake protocol.

## Architecture

The resolver performs a cryptographic handshake with sportsembed.su's `/api/get` endpoint:

1. Encodes match metadata (matchId, slug, category, stream) as a protobuf body
2. Generates a random 32-byte nonce
3. Uses a WASM module to compute a client factor and proof of work
4. Sends the handshake headers (`x-client-nonce`, `x-client-factor`, `x-client-proof`) to `/api/get`
5. Decrypts the response using the WASM module with the `x-live`, `x-edge`, and `x-body-tag` headers
6. Returns the real m3u8 URL on the wfty.st CDN

## Endpoints

All endpoints require `Authorization: Bearer <ANON_KEY>` and `apikey: <ANON_KEY>` headers.

| Endpoint | Method | Description |
|---|---|---|
| `/footy-resolver/healthz` | GET | Health check — confirms WASM is loaded |
| `/footy-resolver/playlist.m3u` | GET | M3U playlist of all live matches with `/raw` redirect URLs |
| `/footy-resolver/<matchId>/raw` | GET | 302 redirect to the real m3u8 URL on wfty.st CDN |
| `/footy-resolver/<matchId>/json` | GET | JSON response with resolved stream URL and expiry timestamp |

## Usage

### Get all live matches as M3U playlist

```
curl -H "Authorization: Bearer $ANON_KEY" -H "apikey: $ANON_KEY" \
  https://<project>.supabase.co/functions/v1/footy-resolver/playlist.m3u
```

### Resolve a single match (302 redirect)

```
curl -L -H "Authorization: Bearer $ANON_KEY" -H "apikey: $ANON_KEY" \
  https://<project>.supabase.co/functions/v1/footy-resolver/4668675/raw
```

### Resolve a single match (JSON)

```
curl -H "Authorization: Bearer $ANON_KEY" -H "apikey: $ANON_KEY" \
  https://<project>.supabase.co/functions/v1/footy-resolver/4668675/json
```

## How it works

The CDN (wfty.st) generates time-limited URLs (6-hour expiry) that are **not** IP-bound. Any client with a residential IP can fetch the m3u8 playlist and segments directly from the CDN. The `/raw` endpoint returns a 302 redirect so IPTV players (VLC, FFmpeg, etc.) follow the redirect and stream from their own IP.

Datacenter/cloud IPs (Google Cloud, AWS, Cloudflare Workers) are blocked by the CDN with 403 Forbidden. The resolver must run on a serverless platform whose egress IP is not on the CDN blocklist, or use the `/raw` redirect mode so the end client fetches from its residential IP.

## Deployment

The Edge Function is deployed via Supabase MCP tools. The WASM binary is embedded as base64 in `wasm.ts` (generated from `stream-lock.wasm`).

## File structure

```
supabase/functions/footy-resolver/
  index.ts          # Edge Function entry point
  wasm.ts           # WASM binary as base64 string
  wasm-base64.txt   # Raw base64 (used to generate wasm.ts)
```
