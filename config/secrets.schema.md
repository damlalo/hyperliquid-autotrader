# Secrets schema

All secrets are provided via environment variables. Never commit `.env` or real keys.

## Required (for trading)

| Variable | Description |
|----------|-------------|
| `HL_ACCOUNT_ADDRESS` | Hyperliquid account address (0x...). |
| `HL_API_WALLET_PRIVATE_KEY` | API wallet private key (hex) for signing. Create via https://app.hyperliquid.xyz/API . |

## Optional

| Variable | Description |
|----------|-------------|
| `POSTGRES_DSN` | Postgres connection string for store (e.g. `postgresql://user:pass@host:5432/db`). |
| `HL_INFO_URL` | Override info endpoint URL. |
| `HL_EXCHANGE_URL` | Override exchange endpoint URL. |
| `HL_WS_URL` | Override WebSocket URL. |
| `ALERT_WEBHOOK_URL` | Webhook for alerts (e.g. Slack/Discord). |
| `VAULT_ADDRESS` | If using a secrets vault later. |
