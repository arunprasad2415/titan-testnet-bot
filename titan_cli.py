#!/usr/bin/env python3
"""Titan testnet CLI farmer.

Run it, paste your EVM private key, answer a few questions, it trades.

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
  Titan Auto Trade Bot
  --------------------
  terminal only: session key minted from your wallet key,
  orders signed locally, no browser window.
"""


def readline(prompt):
    """input() that returns "" on EOF instead of raising, so a closed stdin exits cleanly."""
    try:
        return input(prompt)
    except EOFError:
        print()
        return ""


def ask(prompt, default, cast=float):
    raw = readline(f"  {prompt} [{default}]: ").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        print("    not a number, keeping default")
        return default


def mint_session(private_key, session_path=SESSION, proxy=None):
    """Same flow as titan_auth.py, but in-process and returns the client.

    The venue tracks the last userNonce per credential and rejects anything at or
    below it, so a replay answer means bump the nonce and sign again.
    """
    acct = Account.from_key(private_key)
    sk = SigningKey.generate()
    pub_hex = sk.verify_key.encode().hex()
    expiry = titan_auth.datetime.datetime.now(
        titan_auth.datetime.timezone.utc) + titan_auth.datetime.timedelta(days=7)
    base_nonce = int(time.time() * 1000) * 1_000_000

    last = None
    for attempt in range(4):
        user_nonce = base_nonce + attempt * 1_000_000_000
        canonical = "\n".join([
            "Daml-Perp-Session-Auth v=2",
            titan_auth.line("scheme", "evm"),
            titan_auth.line("credential", acct.address),
            titan_auth.line("sessionPubKey", pub_hex),
            titan_auth.line("expiry", titan_auth.iso(expiry)),
            titan_auth.line("maxNotional", titan_auth.dec10(100000)),
            titan_auth.line("userNonce", user_nonce),
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
            break
        except urllib.error.HTTPError as e:
            detail = e.read()[:200].decode("utf-8", "ignore")
            last = detail
            if "nonce_replay" in detail:
                print(f"      nonce already used, retry {attempt + 1}")
                time.sleep(1)
                continue
            if "rate_limited" in detail:
                print("  the venue rate limits session minting per address, wait a minute and retry")
            print("  auth failed:", e.code, detail)
            sys.exit(1)
    else:
        print("  auth failed after retries:", last)
        sys.exit(1)

    json.dump({
        "sessionId": data["sessionId"],
        "privateKey": sk.encode().hex(),
        "publicKeyHex": pub_hex,
        "expiresAtMs": int(titan_auth.datetime.datetime.fromisoformat(
            data["expiresAt"].replace("Z", "+00:00")).timestamp() * 1000),
        "address": acct.address,
    }, open(session_path, "w"), indent=1)
    os.chmod(session_path, 0o600)
    return TitanClient(session_path, proxy=proxy), acct.address, data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-key", action="store_true", help="store the wallet key for next time")
    ap.add_argument("--dry-run", action="store_true", help="show orders without sending")
    ap.add_argument("--use-session", action="store_true",
                    help="reuse the saved session.json instead of prompting for a key")
    ap.add_argument("--demo", action="store_true",
                    help="walk the settings wizard without an account (forces dry run)")
    a = ap.parse_args()
    if a.demo:
        a.dry_run = True

    print(BANNER)

    if a.use_session:
        if not os.path.exists(SESSION):
            print("  no saved session, run without --use-session")
            sys.exit(1)
        sess = json.load(open(SESSION))
        client = TitanClient(SESSION)
        address = sess.get("address", "?")
        print(f"  reusing session for {address}")
        return wizard(a, client, address)

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
    return wizard(a, client, address)


def wizard(a, client, address):
    print("[2/4] checking the account on Titan ...")
    if a.demo:
        print("      demo mode: skipping the account check")
        ready, me = True, {"demo": True}
    else:
        ready, me = client.account_ready()
        print("      " + ("account ok" if ready else "not ready") + ": " + json.dumps(me)[:140])
    if not ready:
        code = (me.get("code") if isinstance(me, dict) else None) or ""
        if code == "party_unbound" or "party_unbound" in json.dumps(me):
            print("""
  \u2717 this wallet has no Titan account yet.

    create the account first, one time, in a browser:
      1. open https://testnet.titanx.cc
      2. connect this exact wallet: """ + address + """
      3. click enable trading and sign once (that binds your Canton party)
      4. come back and run this script again

    nothing was traded and nothing was signed beyond the session.
""")
            sys.exit(2)
        print("  account check failed, run TITAN_DEBUG=1 ./run.sh and send the block")
        sys.exit(1)

    print("[3/4] balance")
    if a.demo:
        st, bal = 200, {"demo": "no balance in demo mode"}
        open_pos = []
    else:
        st, bal = client.balance()
        print("      ", st, json.dumps(bal)[:200])
        stp, pos = client.positions()
        open_pos = []
        if stp == 200:
            items = pos.get("items", pos.get("positions", [])) if isinstance(pos, dict) else pos
            if isinstance(items, list):
                open_pos = [p for p in items if str(p.get("size", "0")).strip("0.-") != ""]
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
    momentum = ask("momentum gate, % move in 20s", 0.01)
    mode = readline("  mode smart/hedge [smart]: ").strip().lower() or "smart"
    if mode not in ("smart", "hedge"):
        print("    unknown mode, using smart")
        mode = "smart"

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
  momentum    {momentum}% in 20s
  mode        {mode}
  dry run     {a.dry_run}
""")
    if readline("  start trading? [y/N]: ").strip().lower() != "y":
        print("  aborted")
        return

    if a.save_key:
        json.dump({"address": address, "privateKey": key}, open(WALLET, "w"), indent=1)
        os.chmod(WALLET, 0o600)
        print("  wallet key saved to", WALLET)

    if not a.demo:
        st, r = client.set_leverage("BTC-USDCX", lev)
        print("  leverage ->", st, json.dumps(r)[:100])

    ns = argparse.Namespace(market="BTC-USDCX", size=size, leverage=lev, tp=tp, sl=sl,
                            timeout=timeout, max_dd=dd, cycles=cycles, pause=3.0,
                            slip=0.0004, session=SESSION, dry_run=a.dry_run,
                            momentum=momentum)
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
