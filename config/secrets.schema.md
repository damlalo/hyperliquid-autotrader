# Secrets schema

All secrets are provided via environment variables (or substituted in config via `${VAR}`). Never commit `.env` or real keys.

## Required (for trading)

| Variable | Description |
|----------|-------------|
| `HL_ACCOUNT_ADDRESS` | Hyperliquid account address (0x...). Used in config as `hyperliquid.account_address`. |
| `HL_API_WALLET_PRIVATE_KEY` | API wallet private key (hex) for signing. Create via https://app.hyperliquid.xyz/API . Used as `hyperliquid.api_wallet_private_key`. |

## Optional

| Variable | Description |
|----------|-------------|
| `POSTGRES_DSN` | Postgres connection string for store. Used in config as `storage.postgres_dsn`. |
| Override URLs | Config uses `hyperliquid.rest_url` and `hyperliquid.ws_url` (defaults: mainnet); override in YAML or via env if needed. |
| `ALERT_WEBHOOK_URL` | Webhook for alerts (e.g. Slack/Discord). |
| Vault | `hyperliquid.vault_address` in config if using a secrets vault. |
