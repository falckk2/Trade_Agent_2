# TradingView → Trade Agent webhook bridge (FABLE-017)

Routes TradingView alerts (e.g. MarketCipher conditions) into the bot as
normal strategy signals. The full pipeline applies: risk validation,
position sizing, exchange-side TP/SL, portfolio recording.

```
TradingView alert  ──POST──▶  reverse proxy :443 (TLS)  ──▶  bot :8080
                                                              │
                              WebhookSignalStrategy.inject() ◀┘
                                                              │
                              engine tick (≤30s later) consumes signal
                              → risk manager → order + TP/SL → portfolio
```

## Important constraints

- **TradingView only delivers webhooks to ports 80 and 443.** The receiver
  listens on plain HTTP (default 8080), so the VPS needs a reverse proxy
  with TLS in front of it. Caddy is the least-effort option (automatic
  Let's Encrypt certificates).
- **Webhook alerts require a paid TradingView plan** (any tier).
- **Webhook signals cannot be backtested.** There is no historical record of
  when MarketCipher would have fired. Treat this as a live-only strategy:
  start on the demo account, keep the weight conservative, and judge it by
  `scripts/performance_report.py` after it has a real track record.
- Signals are **consume-once** and expire after `max_age_seconds`
  (default 300 s) — an alert that arrives during an outage is dropped, not
  traded late.
- Each webhook strategy entry in `strategies.yaml` covers **exactly one
  symbol**; the receiver rejects alerts whose `symbol` doesn't match.

## 1. Bot configuration

`config/default.yaml`:

```yaml
webhook:
  enabled: true            # default is false
  host: "0.0.0.0"
  port: 8080
  path: "/webhook/tradingview"
  allowed_ips: []          # leave empty behind a reverse proxy (see below)
```

Generate a secret and put it in `.env` (the server refuses to start
without it — there is no unauthenticated mode):

```bash
echo "WEBHOOK_SECRET=$(openssl rand -hex 32)" >> .env
```

`config/strategies.yaml` already contains the bridge entry
(`tv_marketcipher_btc`, type `webhook`, BTC-USDT). Flip `enabled: true`
once the endpoint is reachable. Add one entry per symbol/alert-stream you
want to trade (e.g. a `tv_marketcipher_eth` for ETH-USDT).

## 2a. Testing from WSL — cloudflared quick tunnel

For testing without (or before) the VPS, a Cloudflare quick tunnel gives a
public HTTPS URL (port 443, TLS — satisfies TradingView's requirements)
that forwards to the local receiver. The binary is installed at
`~/.local/bin/cloudflared`. With the bot running:

```bash
~/.local/bin/cloudflared tunnel --url http://127.0.0.1:8080
```

It prints a URL like `https://random-words-1234.trycloudflare.com`; the
TradingView webhook URL is then
`https://random-words-1234.trycloudflare.com/webhook/tradingview`.

Caveats — testing only, not for unattended operation:
- The URL **changes on every tunnel restart** (alerts must be re-pointed).
- Quick tunnels have no uptime guarantee and die with the WSL session.
- The shared secret is still the real protection; the tunnel adds TLS.

## 2b. VPS setup (persistent endpoint)

Any small VPS works (1 vCPU / 1 GB is plenty). You need a domain or
subdomain pointing at it (an A record, e.g. `tv.example.com`) because
TLS — and therefore port 443 delivery — needs a hostname.

```bash
# firewall: only SSH + HTTP(S) exposed; the bot port stays internal
sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable

# caddy as reverse proxy with automatic TLS
sudo apt install -y caddy
```

`/etc/caddy/Caddyfile`:

```
tv.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

```bash
sudo systemctl reload caddy
```

Then deploy the bot on the VPS (clone, `.venv`, `.env` with BloFin demo
credentials + `WEBHOOK_SECRET`) and run it. The webhook URL for TradingView
is `https://tv.example.com/webhook/tradingview`; verify reachability with:

```bash
curl https://tv.example.com/health
# → {"status": "ok"}
```

### Supervision (FABLE-015)

The alert stream is only useful while the bot is running, so run it under
systemd: `deploy/trade-agent.service` (install instructions in the file
header cover both the VPS system unit and the local WSL user unit).
`Restart=always` brings the bot back after crashes; `TimeoutStopSec=90`
gives the shutdown drain time to close positions cleanly.

### IP allowlisting

Behind Caddy, `request.remote` is always 127.0.0.1, so keep
`allowed_ips: []` in the bot and restrict at the proxy if desired.
TradingView's published alert source IPs (verify against their current
docs before pinning):
`52.89.214.238`, `34.212.75.30`, `54.218.53.128`, `52.32.178.7`.

## 3. TradingView alert setup

For each MarketCipher condition you want to trade, create an alert on the
chart (right symbol + timeframe!):

1. Condition: e.g. *MarketCipher B → Buy Signal* (green dot) on BTCUSDT.P
2. Options: **Once Per Bar Close** (intra-bar repaints cause noise)
3. Expiration: open-ended
4. Notifications tab: tick **Webhook URL** →
   `https://tv.example.com/webhook/tradingview`
5. Message — exactly this JSON, one alert per action type:

```json
{
  "secret": "PASTE_WEBHOOK_SECRET_HERE",
  "strategy": "tv_marketcipher_btc",
  "symbol": "BTC-USDT",
  "action": "long",
  "strength": 0.8,
  "condition": "mcb_green_dot",
  "price": "{{close}}",
  "interval": "{{interval}}"
}
```

- `action`: `long`/`buy`, `short`/`sell`, or `close`/`exit`. Create a
  separate alert per direction (e.g. green dot → `long`, red dot →
  `close` or `short`).
- `strength`: 0–1; scales position size and must clear
  `risk.min_signal_strength`. Use higher values for higher-conviction
  conditions (e.g. green dot **with** money-flow confirmation).
- `condition`/`price`/`interval` are optional and stored in the trade
  metadata so `performance_report.py` can later split results per
  condition. `{{close}}` and `{{interval}}` are TradingView placeholders.

Suggested starter set (MarketCipher B, 1H, BTCUSDT.P):

| Alert condition              | action  | strength |
|------------------------------|---------|----------|
| Green dot below zero line    | `long`  | 0.8      |
| Green dot + MFI turning green| `long`  | 1.0      |
| Red dot above zero line      | `close` | 0.8      |
| Blood diamond                | `short` | 0.8      |

(Exact condition names depend on the MarketCipher version's alert list —
pick the closest matches.)

## 4. Verifying end-to-end

With the bot running and the strategy enabled, simulate an alert:

```bash
curl -X POST https://tv.example.com/webhook/tradingview \
  -H 'Content-Type: application/json' \
  -d '{"secret":"...","strategy":"tv_marketcipher_btc","symbol":"BTC-USDT","action":"long","strength":0.8}'
```

Expected: `{"status": "ok"}`, a log line `Webhook accepted: long BTC-USDT
for 'tv_marketcipher_btc'`, and within one engine tick (≤30 s) a signal /
order in the log and dashboard. Rejections return 401 (bad secret),
404 (unknown strategy), 400 (bad action / symbol mismatch / bad JSON),
403 (IP not allowlisted).
