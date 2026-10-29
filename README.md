# Titan testnet CLI farmer

Terminal trader for the Titan Exchange testnet (Canton Network perpetual futures).
No browser after the one-time account creation: the session key is minted from an
EVM private key and every request is signed locally.

## How the auth works

The venue does not use cookies. It uses an ed25519 session key:

1. generate an ed25519 key pair, the public half is the session key
2. sign `Daml-Perp-Session-Auth v=2` with the EVM wallet (EIP-191 personal sign)
3. `POST /v1/auth/session` with `{scheme: "evm", message, signature}` -> `sessionId`
4. every later call carries either
   - `X-Session-Id` + `X-Session-Sig` for action endpoints, or
   - `X-Session-Id` + `X-Request-Ts` + `X-Request-Nonce` + `X-Request-Sig` for plain reads

Two details that cost hours to find:

- the signature is raw 65-byte hex, **no `0x` prefix** (prefixed answers `bad_signature_enc`)
- the request canonical signs the **API** origin, `https://testnet-api.titanx.cc`,
  and uses `key: value` with a single space, while action canonicals pad the key
  to 14 characters

## One-time prerequisite

An account must exist before this works. An account exists when a Canton party is
bound to the wallet, which happens the first time you open
<https://testnet.titanx.cc>, connect that wallet and enable trading (that flow
needs a Cloudflare Turnstile token, so it cannot be done headless).

The wallet must be one whose private key you can export.

The script checks this and stops with instructions if the party is not bound.

## Usage

```bash
python3 titan_cli.py                 # prompt for the key, then six settings
python3 titan_cli.py --save-key      # store the key once at wallet.json (mode 600)
python3 titan_cli.py --demo          # walk the wizard without an account (dry run)
```

Settings are asked one by one, Enter keeps the default:

```
size per leg (base units) [0.002]
leverage                  [5]
take profit, % ROE        [1.2]
stop loss, % ROE          [0.9]
timeout, seconds          [90]
max drawdown, % realised  [15]
cycles                    [10]
mode smart/hedge          [smart]
```

## Fee floor, read this before setting TP

Taker fee is 4.5 bps per side, so a round trip costs 0.09% of notional.

```
TP 0.4% ROE at 5x  = 0.08% price move  -> smaller than the fees, loses on every win
```

The script refuses a TP below `0.09 x leverage` ROE unless `--allow-thin-tp` is
passed. At 5x that floor is 0.45% ROE; 1.2% is a sane default.

## Files

```
titan_auth.py     mint a session from a private key (standalone)
titan_client.py   signing, orders, cancel, leverage, positions, balance
titan_cli.py      interactive entry point: key -> account check -> wizard -> trading
titan_smart.py    momentum entry, TP/SL per position, timeout, drawdown guard
titan_hedge.py    net-flat paired long/short volume farm for points
```

`session.json` and `wallet.json` hold secrets and are gitignored. Never commit them.

## Modes

- **smart** — samples the mark for 20 s, enters only when the move is at least
  0.02%, then manages the position until TP, SL or timeout. No random fallback:
  no signal means no trade.
- **hedge** — paired buy and sell around the mark so the position nets to zero.
  This is the volume farm: points score volume, so this trades cheaply instead of
  predicting direction.

Both stop on a `KILL` file in this directory and both honour `--max-notional`.


## Multiple accounts at once

```bash
python3 titan_multi.py --init                    # writes accounts.example.json
# fill in a privateKey per account (and optional proxy), then:
python3 titan_multi.py --config accounts.json
python3 titan_multi.py --config accounts.json --dry-run
python3 titan_multi.py --config accounts.json --only alice
```

Config:

```json
{
  "defaults": {"market": "BTC-USDCX", "size": 0.002, "leverage": 5, "tp": 1.2,
               "sl": 0.9, "timeout": 90, "max_dd": 15, "cycles": 0,
               "stealth": true, "momentum": 0.01},
  "accounts": [
    {"name": "alice", "privateKey": "0x...", "proxy": "http://user:pass@host:port"},
    {"name": "bob",   "privateKey": "0x...", "size": 0.001, "momentum": 0.02}
  ]
}
```

Behaviour:

- each account gets `sessions/<name>.json` (mode 600), re-minted automatically
  when it has under a day left
- every account runs in its own process, so one crash does not take the rest down
- starts are staggered 0.4 s apart, they should not look synchronised
- logs are prefixed `[name]`
- `cycles: 0` means run until stopped
- ctrl-c, or `touch KILL`, stops every worker

## Proxy support

`proxy` accepts `http://`, `https://` or `socks5://` (with optional `user:pass@`).
Transport is `curl_cffi` with Chrome impersonation, so the TLS fingerprint matches
a browser instead of Python's; plain `requests` gets a Cloudflare 403 on this API,
which is why the transport changed. Without `curl_cffi` it falls back to `requests`,
then to `urllib` (no proxy in that last case).
