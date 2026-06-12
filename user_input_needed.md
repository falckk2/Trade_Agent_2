# User Input Needed

_Last updated: 2026-06-11. Items the agents cannot complete without you,
ordered by impact. Tick them off / edit inline; agents should re-read this
file each session and remove completed items._

## 1. Create the first real TradingView alert (closes FABLE-017)

Everything on the bot side is live and verified — the only missing piece is
an alert that originates from TradingView itself.

- Webhook URL (current cloudflared quick tunnel, restarted 2026-06-12):
  `https://convenient-importantly-exciting-the.trycloudflare.com/webhook/tradingview`
  ⚠️ This URL changes if the tunnel restarts. Get the current one with:
  `journalctl --user -u cloudflared-tunnel | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | tail -1`
- The shared secret is in `.env` on this machine: `grep WEBHOOK_SECRET .env`
  (never commit it; it goes inside the alert's JSON message).
- Follow `docs/tradingview_webhook.md` section 3 for the alert JSON template
  and suggested MarketCipher conditions (use **Once Per Bar Close**).
- After the first alert fires: check `journalctl --user -u trade-agent` for
  `Webhook accepted` — then FABLE-017 can be promoted to Resolved.

## 2. VPS details (persistent endpoint + 24/7 operation)

You said you'll use a VPS for the initial stages. To deploy I need:

- [ ] VPS provider chosen + instance created (1 vCPU / 1 GB is enough)
- [ ] SSH access details (or run the steps yourself from
      `docs/tradingview_webhook.md` section 2b)
- [ ] A domain or subdomain with an A record pointing at the VPS
      (needed for TLS — TradingView only delivers to ports 80/443)

Deployment is then: clone repo, venv, `.env` (BloFin demo keys + a NEW
`WEBHOOK_SECRET`), caddy reverse proxy, `deploy/trade-agent.service`
(instructions in the file header).

## 3. Telegram credentials (verifies FABLE-011 alerting)

Operator alerts (drawdown halt, close-all failure, WS reconnect storm) are
wired but unverified — the notifier is a no-op without credentials.

- [ ] Create a bot via @BotFather → put `TELEGRAM_BOT_TOKEN` in `.env`
- [ ] Get your chat id (message the bot, then
      `https://api.telegram.org/bot<TOKEN>/getUpdates`) → `TELEGRAM_CHAT_ID`
- [ ] Restart: `systemctl --user restart trade-agent`; agents will then
      send a test alert to verify delivery.

## 4. Decision: archive pre-fix trade history?

Live all-time stats include **-$177.93 across 16 trades from the broken
pre-2026-06-10 era** (wrong fill prices, losing 5m config). Archiving them
(moved to `data/archive/`, not deleted) gives `scripts/performance_report.py`
a clean baseline that reflects only post-fix execution.

- [ ] Yes, archive / [ ] No, keep full history (default: keep)

## 5. Decision: webhook strategy weight

`tv_marketcipher_btc` shares BTC-USDT with `sma_crossover_btc`, so the
weighted composite dilutes webhook alerts to ~half strength (the test trade
still executed). Options:

- [ ] Keep as is (MarketCipher is one voice among equals — default)
- [ ] Raise its weight in `strategies.yaml` (alerts dominate the SMA)
- [ ] Run it on a symbol the SMAs don't trade (full strength, clean A/B)

## 6. Decision: automate the learning loop?

`scripts/performance_report.py` (live-vs-backtest divergence per strategy)
currently runs manually. Options: a weekly cron on this machine/VPS, or a
scheduled Claude routine that runs it AND proposes config changes from the
results. Say which (or neither) and it gets set up.
