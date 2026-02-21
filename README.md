# 🐯 Tiger Data Portal

Crypto insider activity tracker — like SEC Form 4, but for blockchain.

## Concept

Track vesting contract wallets of crypto project teams to monitor token movements (buying/selling), similar to insider trading disclosures in traditional finance.

## Architecture

```
backend/
  collectors/   # On-chain data collectors (Ethereum, Solana, etc.)
  models/       # MongoDB schemas
  api/          # FastAPI endpoints
frontend/       # Next.js dashboard
data/
  seeds/        # Initial 50 project seed data
docs/           # Documentation
```

## Database (MongoDB)

Collections:
- `projects` — Project info & metadata
- `vesting_contracts` — Vesting contract addresses per chain
- `wallets` — Beneficiary wallet addresses
- `wallet_labels` — Wallet → person/team mapping
- `transactions` — Token movement records

## Supported Chains

- Ethereum (primary)
- Solana
- Arbitrum / Base / Optimism
