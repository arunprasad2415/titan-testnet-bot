# Titan Auto Trade Bot

A command line bot that trades the **Titan Exchange testnet** (perpetual futures on
Canton Network) and farms the weekly points program. You give it a wallet private
key, it asks a few questions, then it trades by itself. It can run 24/7 on a VPS and
handle several accounts at once.

No browser is needed while it runs. One browser step is needed **once**, to create
the account.

---

## Contents

1. [What you need before starting](#1-what-you-need-before-starting)
2. [Install](#2-install)
3. [Create the account (the only browser step)](#3-create-the-account-the-only-browser-step)
4. [First run, explained line by line](#4-first-run-explained-line-by-line)
5. [Running it 24/7](#5-running-it-247)
6. [Checking status](#6-checking-status)
7. [Stopping it properly](#7-stopping-it-properly)
8. [Several accounts at once](#8-several-accounts-at-once)
9. [Proxies](#9-proxies)
10. [How the money works (read this)](#10-how-the-money-works-read-this)
11. [Behaviour randomisation](#11-behaviour-randomisation)
12. [Every error you will meet, and the fix](#12-every-error-you-will-meet-and-the-fix)
13. [Security](#13-security)
14. [How the login actually works](#14-how-the-login-actually-works)
15. [Files in this repo](#15-files-in-this-repo)

---

## 1. What you need before starting

- A Linux machine (tested on Ubuntu 22.04, a 1 CPU VPS is enough).
- Python 3.10 or newer.
- An **EVM wallet whose private key you can export**. MetaMask, Rabby, a fresh
  `eth_account` key, anything. The Binance Agentic Wallet does **not** work here
  because it never lets you export the key.
- About 15 minutes.

You do **not** need real money. The testnet gives free test USDC from a faucet
inside the app.

---

## 2. Install

```bash
# 1. get the code (private repo, so use your GitHub token or copy the files over)
git clone https://github.com/arunprasad2415/titan-testnet-bot.git titan
cd titan

# 2. make a python environment
python3 -m venv .venv
. .venv/bin/activate

# 3. install what the bot needs
pip install eth-account pynacl curl_cffi
```

Check it worked:

```bash
python3 -c "import eth_account, nacl, curl_cffi; print('ok')"
```

If you see `ok`, setup is done.

> `run.sh`, `status.sh` and `flat.sh` call `/root/pw-venv/bin/python`. If you
> installed into a different environment, edit those three one-line scripts and
> point them at your python.

---

## 3. Create the account (the only browser step)

The venue will not let a wallet trade until a **Canton party** is bound to it. That
binding needs a Cloudflare Turnstile check, which only a real browser can pass. Do
it once, by hand:

1. Open <https://testnet.titanx.cc> in a normal browser.
2. Click **Connect wallet** and pick the wallet whose private key you can export.
3. Approve the signature request in your wallet.
4. Click **Enable trading** and sign again if it asks.
5. Claim the free test USDC with the faucet button inside the app.

You now have an account. Check the balance in the app, it should show a few thousand
test USDCx.

**Write down which wallet you used.** The bot needs that exact wallet's private key,
not any other.

---

## 4. First run, explained line by line

```bash
cd /root/titan
./run.sh
```

### Step 1 of 4, the key

```
  Titan Auto Trade Bot
  --------------------
  terminal only: session key minted from your wallet key,
  orders signed locally, no browser window.

  private key (hidden):
```

Paste the private key of the wallet from section 3. It starts with `0x`. Nothing is
echoed to the screen. Then:

```
  wallet 0x46FF0054d657e9E63E7dd34b8771C78D0BF7c1C2
[1/4] minting session key ...
      session 3442e28f468c6409...  expires 2026-11-05T19:21:49.293Z
```

The bot signs a login message with your key and gets back a session that lasts 7
days. Your private key is not sent anywhere, only the signature is.

### Step 2 of 4, does the account exist

```
[2/4] checking the account on Titan ...
      account ok: {"userParty": "young_quasar_85::122065b4...", "balance": "12451.9..."}
```

If instead you see this, the wallet has no account yet and you must do section 3:

```
  x this wallet has no Titan account yet.
    create the account first, one time, in a browser:
      1. open https://testnet.titanx.cc
      2. connect this exact wallet: 0x....
      3. click enable trading and sign once
      4. come back and run this script again
```

### Step 3 of 4, balance and open positions

```
[3/4] balance
       200 {"userParty": "...", "balance": "12451.9...", "freeBalance": "12451.9..."}
       open positions: 0
```

If it says `open positions: 1` or more, the bot stops. Close them in the app or run
`./flat.sh`, because the bot always starts from a flat account.

### Step 4 of 4, the settings

Press Enter to accept the default in brackets.

```
  size per leg (base units) [0.002]:      how much BTC per trade, 0.002 = about $160
  leverage [5]:                          5x is sane, 20x will liquidate you
  take profit, % ROE [1.2]:              close when profit reaches this
  stop loss, % ROE [0.9]:                close when loss reaches this
  timeout, seconds [90]:                 close anyway after this long
  max drawdown, % of realised [15]:      stop the bot if total loss reaches this
  cycles [10]:                           how many trades, then stop
  momentum gate, % move in 20s [0.01]:   how big a price move before it trades
  mode smart/hedge [smart]:              smart = follow the move, hedge = paired farm
```

Then it prints a summary and asks for confirmation:

```
  start trading? [y/N]: y
```

### What you will see while it trades

```
cycle 1: waiting for momentum
    waiting: move +0.0025% over 20s, gate 0.010%
  signal buy momentum 0.019% BTC-USDCX size 0.002409 gtc
  order buy -> 200 {"ok": true, "fills": 1, "orderId": "...", "fee": "0.0871"}
    roe +0.27%  (15s)
    roe +0.36%  (16s)
  closed by timeout, roe +0.47%, total +14.18%
  flatten BTC-USDCX +0.000540 -> 200
```

- `waiting` means the price has not moved enough yet. Normal, it can wait minutes.
- `signal` means it found a move and is about to trade.
- `order ... 200` means the venue accepted and filled it.
- `roe` is profit on the position, in percent of the margin used.
- `closed by` says why it closed: TP, SL, timeout, or kill.
- `flatten` closes leftover dust so nothing accumulates.

---

## 5. Running it 24/7

Run it as a systemd service so it survives reboots and crashes.

```bash
# 1. create the account config (one account)
cat > /root/titan/accounts.json <<'JSON'
{
  "defaults": {"market": "BTC-USDCX", "size_usd": 150, "leverage": 5, "tp": 1.2,
               "sl": 0.9, "timeout": 90, "max_dd": 15, "cycles": 0,
               "stealth": true, "momentum": 0.01},
  "accounts": [
    {"name": "main", "privateKey": "0xPUT_YOUR_KEY_HERE"}
  ]
}
JSON
chmod 600 /root/titan/accounts.json

# 2. create the service
cat > /etc/systemd/system/titan-farm.service <<'UNIT'
[Unit]
Description=Titan Auto Trade Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/titan
ExecStart=/root/pw-venv/bin/python /root/titan/titan_multi.py --config /root/titan/accounts.json
Restart=always
RestartSec=15
StandardOutput=append:/var/log/titan-farm.log
StandardError=append:/var/log/titan-farm.log

[Install]
WantedBy=multi-user.target
UNIT

# 3. start it, and start it again after every reboot
systemctl daemon-reload
systemctl enable --now titan-farm

# 4. keep the log from growing forever
cat > /etc/logrotate.d/titan-farm <<'LR'
/var/log/titan-farm.log {
    su root root
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
}
LR
```

`cycles: 0` means run forever. `size_usd` means "150 dollars of notional per leg",
so the same config works on BTC, ETH and SOL without recalculating size.

---

## 6. Checking status

```bash
cd /root/titan
./status.sh            # one screen
./status.sh --watch    # refresh every 5 seconds
```

```
Titan Auto Trade Bot
--------------------
service    active  up 8h 35m  restarts 0
balance    12376.41 USDCx
positions  [('BTC-USDCX', '0.0019200000', '78226.0000000000')]
points     322.00   rank 119216 of 215572
activity   323 cycles, 646 fills, 40.74 USDC fees
```

Raw views:

```bash
systemctl status titan-farm        # is it up, how many restarts
tail -f /var/log/titan-farm.log    # every order and every ROE tick
grep "order .* -> 200" /var/log/titan-farm.log | wc -l   # how many fills
```

`balance` includes unrealised profit or loss on an open position, so it moves
between checks. `positions` tells you whether you are flat.

---

## 7. Stopping it properly

Two steps. Stopping does **not** close the open trade.

```bash
systemctl stop titan-farm     # 1. stop trading
./flat.sh                     # 2. close whatever is open
./status.sh                   # 3. confirm: inactive, positions flat
```

Preview before closing:

```bash
./flat.sh --dry-run
BTC-USDCX -0.001920 -> buy 0.00192 @ 78355 (dry run)
```

Other commands:

```bash
systemctl start titan-farm      # resume, reuses the saved session
systemctl disable titan-farm    # do not start after a reboot
```

---

## 8. Several accounts at once

```bash
python3 titan_multi.py --init      # writes accounts.example.json
# edit it, one entry per account
python3 titan_multi.py --config accounts.json
python3 titan_multi.py --config accounts.json --only alice
python3 titan_multi.py --config accounts.json --dry-run
```

```json
{
  "defaults": {"market": "BTC-USDCX", "size_usd": 150, "leverage": 5, "tp": 1.2,
               "sl": 0.9, "timeout": 90, "max_dd": 15, "cycles": 0,
               "stealth": true, "momentum": 0.01},
  "accounts": [
    {"name": "alice", "privateKey": "0x...", "proxy": "http://user:pass@host:port"},
    {"name": "bob",   "privateKey": "0x...", "size_usd": 80, "momentum": 0.02}
  ]
}
```

How it behaves:

- each account gets `sessions/<name>.json`, mode 600, re-minted when under a day left
- each account runs in its own process, so one crash does not kill the others
- starts are staggered 0.4s apart, they should not open in lockstep
- logs are prefixed `[alice]` and `[bob]`
- `ctrl-c` or `touch KILL` stops every worker

Each account needs its own browser step from section 3, with its own wallet.

---

## 9. Proxies

Add `"proxy"` to an account, or nothing to go direct.

```json
{"name": "alice", "privateKey": "0x...", "proxy": "http://user:pass@1.2.3.4:8080"}
{"name": "bob",   "privateKey": "0x...", "proxy": "socks5://user:pass@1.2.3.4:1080"}
```

`http://`, `https://` and `socks5://` all work, with or without credentials.

The transport is `curl_cffi` impersonating Chrome, so the TLS fingerprint matches a
browser. Plain `requests` gets a Cloudflare 403 from this API, which is why it is
not used.

---

## 10. How the money works (read this)

Fees:

```
taker fee    4.5 bps per side   = 0.09% round trip
maker fee    1.5 bps per side   = 0.03% round trip
min notional 10 USDC per order
```

That fee is the whole game. A take-profit of `0.4% ROE at 5x` is a **0.08%** price
move, which is **smaller than the 0.09% you pay in fees**. That setting loses on
every win. The bot refuses it:

```
refusing: TP 0.4% ROE is below the fee floor 0.45% (2x 4.5 bps at 5x)
```

Safe starting points:

| Setting | Value | Why |
| --- | --- | --- |
| leverage | 5x | 20x gets liquidated by noise |
| TP | 1.2% ROE | 0.24% price move, clears fees with room |
| SL | 0.9% ROE | cuts before it hurts |
| timeout | 90s | gives the trade time to work |
| momentum gate | 0.01% | quiet markets do not trade |
| max drawdown | 15% | stops the bot before the fund is gone |

**Points are not profit.** The weekly points program scores volume, PnL and
streaks. Farming volume by trading back and forth pays fees for points, so expect
the USDC balance to go **down** while the points go up. The running `total` the bot
prints is the sum of per-cycle ROE percentages, not money, do not read it as profit.

Modes:

- `smart` follows the move: waits for momentum, takes one side, manages to TP/SL.
- `hedge` fires a buy and a sell around the mark so the position nets to zero. Pure
  volume farming, it costs fees and buys only points. The venue may filter
  self-cancelling volume out of the points pool.

---

## 11. Behaviour randomisation

`stealth: true` gives each account a personality derived from its name, then draws
every decision inside a cycle from that personality:

```
tls          curl_cffi impersonation per account (chrome, chrome124, safari, edge)
size         base x personality multiplier x per-cycle noise
gate         momentum threshold re-rolled each cycle, +/-50%
tif          maker or taker chosen per cycle from a per-account bias
market       each account keeps a preferred pool, re-picked every cycle
hold         TP/SL timeout scaled per cycle
skip         sometimes the signal is passed on entirely
closing      occasionally closes in two pieces seconds apart
pause        gaussian between cycles, plus a 20-120s idle now and then
jitter       0.3-3.5s hesitation before every entry
```

Two accounts started from the same config diverge immediately:

```
[alice] profile alice: safari17_0, size x0.87, pace x1.42, markets ['ETH-USDCX']
[bob]   profile bob:   chrome124,   size x1.57, pace x0.96, markets ['BTC-USDCX']
```

This defeats pattern filters, it does not change what a paired buy and sell is on
the venue's own books.

---

## 12. Every error you will meet, and the fix

**`nonce_replay` when minting a session**
The venue remembers the last login nonce per wallet. The bot uses microsecond scale
and retries with a bigger nonce automatically. If it still happens, wait a minute
and run again.

**`rate_limited` when minting a session**
Sessions are rate limited per wallet address. Do not loop the auth script. One
session lasts 7 days.

**`party_unbound`**
The wallet has no account. Do section 3 with that exact address.

**`bad_body` on `/v1/me`**
A quirk of that one route on some accounts. The bot no longer depends on it, it
reads balance and positions instead. If you see it elsewhere, run with
`TITAN_DEBUG=1 ./run.sh` and read the canonical it prints.

**`missing_headers`**
A write was sent with only one of the two required signatures. The client sends
both now. Only relevant if you edit the client.

**`body/tif must be equal to constant`**
Time-in-force must be lowercase: `ioc`, `gtc`, `alo`. Uppercase is rejected.

**`price carries 6 significant figures`**
Non-integer prices may carry at most 5 significant figures. The client rounds with
`price5()`.

**`size must be a positive multiple of 10^-N`**
Each market has its own `szDecimals`. The client reads it from `/v1/markets` and
rounds the size before sending.

**`notional below market minimum`**
Orders below 10 USDC are rejected. The bot bumps the size up to the minimum, or use
`size_usd` so it is sized in dollars instead of coins.

**Cloudflare 403 / "Just a moment..."**
The API sits behind Cloudflare. From some datacenter IPs it is blocked outright,
for example the Oracle free tier. Chrome impersonation gets past the fingerprint
check, a blocked IP stays blocked. Test the host:

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "accept: application/json" \
  -H "user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36" \
  -H 'sec-ch-ua: "Chromium";v="156", "Not:A-Brand";v="8"' \
  -H 'sec-ch-ua-platform: "Windows"' \
  -H "referer: https://testnet.titanx.cc/" \
  https://testnet-api.titanx.cc/v1/markets
```

**It sits on `waiting for momentum` forever**
The gate is too high for a quiet market. The bot reads the venue's own mark from
`/v1/markets/BTC-USDCX/mark`; the external fallback is Kraken because Coinbase's
spot endpoint is cached and reads as zero movement. Lower the gate with
`momentum: 0.005`.

**`account check failed`**
Run `TITAN_DEBUG=1 ./run.sh` and look at the canonical and headers it prints.

---

## 13. Security

- The private key is a **live key**. Anyone with it owns the account.
- Store it at mode 600, never in a git repo. `accounts.json`, `wallet.json` and
  `keys/` are gitignored for that reason.
- `session.json` and `sessions/*.json` hold an ed25519 session key that can trade
  but cannot withdraw. Still keep them private.
- Never paste a key into a chat, a screenshot or a public issue. If you already did,
  move the account to a fresh wallet.
- The bot never sends your private key anywhere. It signs locally and sends only
  signatures.

---

## 14. How the login actually works

Useful if you want to build your own client.

The venue does not use cookies. It uses an ed25519 session key:

1. generate an ed25519 key pair, the public half is the session key
2. sign `Daml-Perp-Session-Auth v=2` with the EVM wallet (EIP-191 personal sign)
3. `POST /v1/auth/session` with `{scheme: "evm", message, signature}` -> `sessionId`
4. every later call carries either
   - `X-Session-Id` + `X-Session-Sig` for action endpoints, or
   - `X-Session-Id` + `X-Request-Ts` + `X-Request-Nonce` + `X-Request-Sig` for reads
5. **writes need both families at once**, one alone answers `missing_headers`

Three details that cost hours:

- the auth signature is raw 65-byte hex, **no `0x` prefix** (prefixed answers
  `bad_signature_enc`)
- the request canonical signs the **API** origin `https://testnet-api.titanx.cc`,
  not the app origin
- action canonicals pad the key to 14 characters (`name:` + spaces), request
  canonicals use a single space (`name: value`)

Canonical shapes:

```
Daml-Perp-Session-Auth v=2
scheme:       evm
credential:   0x...
sessionPubKey:<64 hex>
expiry:       <ISO>
maxNotional:  <10 decimals>
userNonce:    <Date.now() * 1e6>
label:
scope:        full
```

```
Daml-Perp-Request v=2
aud: rest
origin: https://testnet-api.titanx.cc
session: <sessionId>
conn:
method: GET
path: /v1/me/positions
query:
body: <sha256 hex, e3b0c4... for empty>
ts: <ISO>
nonce: <int64>
expires:
```

---

## 15. Files in this repo

```
run.sh              launcher for the interactive bot
titan_cli.py        the wizard: key -> account check -> settings -> trade
titan_auth.py       mint a session from a private key, standalone
titan_client.py     signing, orders, cancel, leverage, positions, balance
titan_smart.py      the trading loop: momentum, TP/SL, timeout, drawdown guard
titan_hedge.py      paired buy/sell volume farm
titan_behavior.py   per-account personality, per-cycle randomisation
titan_multi.py      run several accounts at once, each with its own proxy
titan_worker.py     one account's worker process, spawned by titan_multi
titan_status.py     one-screen status
titan_flat.py       close every open position
status.sh           wrapper for titan_status.py
flat.sh             wrapper for titan_flat.py
accounts.json       your accounts and settings (gitignored, mode 600)
sessions/           per-account session keys (gitignored, mode 600)
```

---

## Quick reference

```bash
./run.sh                                          # interactive, one account
./status.sh                                       # what is it doing
tail -f /var/log/titan-farm.log                   # live trade log
systemctl stop titan-farm && ./flat.sh            # stop and close the trade
systemctl start titan-farm                        # resume
python3 titan_multi.py --config accounts.json     # several accounts
```
