#!/usr/bin/env python3
"""Titan testnet CLI farmer.

Run it, paste your EVM private key, answer six questions, it trades.

    python3 titan_cli.py

The account has to exist first. An account exists when a Canton party is bound
to the wallet, which happens the first time you open testnet.titanx.cc, connect
that wallet and enable trading. This script checks for that and refuses to
pretend otherwise: if the party is not bound it tells you to create the account
and stops.

Everything after that is terminal only: session minted from the key, orders
signed locally, no browser.
"""
import argparse
import getpass
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eth_account import Account                       # noqa: E402
from eth_account.messages import encode_defunct       # noqa: E402
from nacl.signing import SigningKey                   # noqa: E402

import titan_auth                                     # noqa: E402
from titan_client import TitanClient, BASE, UA        # noqa: E402
import urllib.request, urllib.error                   # noqa: E402

SESSION = "/root/titan/session.json"
WALLET = "/root/titan/wallet.json"
BANNER = """
  Titan testnet CLI farmer
  ------------------------
  terminal only: session key minted from your wallet key,
  orders signed locally, no browser window.
"""


def ask(prompt, default, cast=float):
    raw = input(f"  {prompt} [{default}]: ").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        print("    not a number, keeping default")
        return default


def mint_session(private_key):
    """Same flow as titan_auth.py, but in-process and returns the client."""
    acct = Account.from_key(private_key)
    sk = SigningKey.generate()
    pub_hex = sk.verify_key.encode().hex()
    expiry = titan_auth.datetime.datetime.now(
        titan_auth.datetime.timezone.utc) + titan_auth.datetime.timedelta(days=7)
    canonical = "\n".join([
        "Daml-Perp-Session-Auth v=2",
        titan_auth.line("scheme", "evm"),
        titan_auth.line("credential", acct.address),
        titan_auth.line("sessionPubKey", pub_hex),
        titan_auth.line("expiry", titan_auth.iso(expiry)),
        titan_auth.line("maxNotional", titan_auth.dec10(100000)),
        titan_auth.line("userNonce", int(time.time() * 1000)),
        titan_auth.line("label", ""),
        titan_auth.line("scope", "full"),
    ])
    sig = acct.sign_message(encode_defunct(text=canonical)).signature.hex()
    body = {"scheme": "evm", "message": canonical, "signature": sig}
    req = urllib.request.Request(
        BASE + "/v1/auth/session", data=json.dumps(body).encode(), method="POST",
        headers={"content-type": "application/json", "accept": "application/json",
                 "user-agent": UA, "sec-ch-ua": '"Chromium";v="156", "Not:A-Brand";v="8"',
                 "sec-ch-ua-platform": '"Windows"',
                 "referer": "https://testnet.titanx.cc/"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode("utf-8", "ignore")
        if "rate_limited" in detail:
            print("  the venue rate limits session minting per address, wait a minute and retry")
        print("  auth failed:", e.code, detail)
        sys.exit(1)

    json.dump({
        "sessionId": data["sessionId"],
        "privateKey": sk.encode().hex(),
        "publicKeyHex": pub_hex,
        "expiresAtMs": int(titan_auth.datetime.datetime.fromisoformat(
            data["expiresAt"].replace("Z", "+00:00")).timestamp() * 1000),
        "address": acct.address,
    }, open(SESSION, "w"), indent=1)
    os.chmod(SESSION, 0o600)
    return TitanClient(SESSION), acct.address, data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-key", action="store_true", help="store the wallet key for next time")
    ap.add_argument("--dry-run", action="store_true", help="show orders without sending")
    ap.add_argument("--demo", action="store_true",
                    help="walk the settings wizard without an account (forces dry run)")
    a = ap.parse_args()
    if a.demo:
        a.dry_run = True

    print(BANNER)

    key = None
    if a.save_key and os.path.exists(WALLET):
        key = json.load(open(WALLET))["privateKey"]
        print("  using saved wallet key")
    if not key:
        key = getpass.getpass("  private key (hidden): ").strip()
    if not key:
        print("  no key, exit")
        sys.exit(1)
    if not key.startswith("0x"):
        key = "0x" + key
    try:
        acct = Account.from_key(key)
    except Exception:
        print("  that is not a valid EVM private key")
        sys.exit(1)
    print(f"  wallet {acct.address}")

    print("[1/4] minting session key ...")
    client, address, sess = mint_session(key)
    print(f"      session {sess['sessionId'][:16]}...  expires {sess['expiresAt']}")

    print("[2/4] checking the account on Titan ...")
    if a.demo:
        print("      demo mode: skipping the account check")
        st, me = 200, {"demo": True}
    else:
        st, me = client.me()
    if st == 403 and (me.get("error") or {}).get("code") == "party_unbound":
        print("""
  ✗ this wallet has no Titan account yet.

    create the account first, one time, in a browser:
      1. open https://testnet.titanx.cc
      2. connect this exact wallet: """ + address + """
      3. click enable trading and sign once (that binds your Canton party)
      4. come back and run this script again

    nothing was traded and nothing was signed beyond the session.
""")
        sys.exit(2)
    if st != 200:
        print("  account check failed:", st, json.dumps(me)[:200])
        sys.exit(1)
    print("      account ok:", json.dumps(me)[:160])

    print("[3/4] balance")
    if a.demo:
        st, bal = 200, {"demo": "no balance in demo mode"}
    else:
        st, bal = client.balance()
    print("      ", st, json.dumps(bal)[:200])
    st, pos = (200, {"positions": []}) if a.demo else client.positions()
    open_pos = []
    if st == 200:
        body = pos.get("positions", pos) if isinstance(pos, dict) else pos
        if isinstance(body, list):
            open_pos = [p for p in body if str(p.get("size", "0")).strip("0.-") != ""]
        print(f"       open positions: {len(open_pos)}")
    if open_pos:
        print("  ✗ a position is already open, close it first so the bot starts flat")
        sys.exit(3)

    print("[4/4] settings, Enter keeps the default")
    size = ask("size per leg (base units)", 0.002)
    lev = ask("leverage", 5, int)
    tp = ask("take profit, % ROE", 1.2)
    sl = ask("stop loss, % ROE", 0.9)
    timeout = ask("timeout, seconds", 90, int)
    dd = ask("max drawdown, % of realised", 15.0)
    cycles = ask("cycles", 10, int)
    mode = input("  mode smart/hedge [smart]: ").strip().lower() or "smart"

    floor = 0.09 * lev
    if mode == "smart" and tp < floor:
        print(f"\n  ✗ TP {tp}% ROE is under the fee floor {floor:.2f}% at {lev}x.")
        print("    two taker legs cost 0.09% of notional, so a smaller TP loses on every win.")
        sys.exit(4)

    print(f"""
  market      BTC-USDCX
  size        {size} per leg
  leverage    {lev}x
  TP / SL     {tp}% / {sl}% ROE
  timeout     {timeout}s
  max DD      {dd}%
  cycles      {cycles}
  mode        {mode}
  dry run     {a.dry_run}
""")
    if input("  start trading? [y/N]: ").strip().lower() != "y":
        print("  aborted")
        return

    if a.save_key:
        json.dump({"address": address, "privateKey": key}, open(WALLET, "w"), indent=1)
        os.chmod(WALLET, 0o600)
        print("  wallet key saved to", WALLET)

    ns = argparse.Namespace(market="BTC-USDCX", size=size, leverage=lev, tp=tp, sl=sl,
                            timeout=timeout, max_dd=dd, cycles=cycles, pause=3.0,
                            slip=0.0004, session=SESSION, dry_run=a.dry_run)
    if mode == "hedge":
        import titan_hedge
        sys.argv = ["titan_hedge.py", "--market", "BTC-USDCX", "--size", str(size),
                    "--cycles", str(cycles), "--max-notional", str(size * 80000 * cycles * 2)]
        if a.dry_run:
            sys.argv.append("--dry-run")
        titan_hedge.main()
    else:
        import titan_smart
        titan_smart.Smart(ns).run()


if __name__ == "__main__":
    main()
