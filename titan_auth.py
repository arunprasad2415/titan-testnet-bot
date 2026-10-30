#!/usr/bin/env python3
"""Mint a Titan testnet trading session entirely from the CLI, no browser.

Flow:
  1. make (or load) an EVM key  -> that key is the account credential
  2. make an ed25519 session key -> that key signs every later API request
  3. sign the Daml-Perp-Session-Auth v=2 message with the EVM key (EIP-191)
  4. POST /v1/auth/session  -> sessionId + expiry
  5. write session.json (session id + ed25519 seed) and wallet.json (EVM key)

  python3 titan_auth.py --new            # create a fresh wallet + session
  python3 titan_auth.py                  # reuse the stored wallet, mint a new session
  python3 titan_auth.py --key 0xabc...   # use a specific EVM private key
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

from eth_account import Account
from eth_account.messages import encode_defunct
from nacl.signing import SigningKey

BASE = "https://testnet-api.titanx.cc"
ORIGIN = "https://testnet.titanx.cc"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/156.0.0.0 Safari/537.36")
WALLET = "/root/titan/wallet.json"
SESSION = "/root/titan/session.json"


def line(name, value):
    return f"{name}:".ljust(14) + str(value)


def iso(dt=None):
    dt = dt or datetime.datetime.now(datetime.timezone.utc)
    ms = f"{dt.microsecond // 1000:03d}".rstrip("0")
    head = dt.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{head}.{ms}Z" if ms else f"{head}Z"


def dec10(v):
    s = str(v).strip()
    whole, _, frac = s.partition(".")
    whole = whole.lstrip("0") or "0"
    frac = (frac + "0" * 10)[:10]
    return f"{whole}.{frac}"


def load_wallet(args):
    if args.key:
        acct = Account.from_key(args.key)
        return acct, False
    if args.new or not os.path.exists(WALLET):
        acct = Account.create()
        return acct, True
    data = json.load(open(WALLET))
    return Account.from_key(data["privateKey"]), False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", action="store_true", help="create a fresh EVM wallet")
    ap.add_argument("--key", help="hex private key to use instead of the stored one")
    ap.add_argument("--ttl-days", type=int, default=7)
    ap.add_argument("--max-notional", type=float, default=100000)
    a = ap.parse_args()

    acct, fresh = load_wallet(a)
    address = acct.address

    sk = SigningKey.generate()
    pub_hex = sk.verify_key.encode().hex()
    expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=a.ttl_days)
    # the app uses Date.now()*1e6 (microsecond scale); a smaller value can replay
    user_nonce = int(time.time() * 1000) * 1_000_000

    canonical = "\n".join([
        "Daml-Perp-Session-Auth v=2",
        line("scheme", "evm"),
        line("credential", address),
        line("sessionPubKey", pub_hex),
        line("expiry", iso(expiry)),
        line("maxNotional", dec10(a.max_notional)),
        line("userNonce", user_nonce),
        line("label", ""),
        line("scope", "full"),
    ])

    signed = acct.sign_message(encode_defunct(text=canonical))
    # the engine wants raw 65-byte hex, no 0x prefix (0x-prefixed answers bad_signature_enc)
    body = {"scheme": "evm", "message": canonical, "signature": signed.signature.hex()}

    req = urllib.request.Request(
        BASE + "/v1/auth/session",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "accept": "application/json",
                 "user-agent": UA, "sec-ch-ua": '"Chromium";v="156", "Not:A-Brand";v="8"',
                 "sec-ch-ua-platform": '"Windows"', "referer": ORIGIN + "/"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.load(r)
            status = r.status
    except urllib.error.HTTPError as e:
        raw = e.read()[:400].decode("utf-8", "ignore")
        print("auth failed:", e.code, raw)
        sys.exit(1)

    print("auth ok:", status, json.dumps(data)[:300])

    session_id = data.get("sessionId") or data.get("session", {}).get("sessionId")
    expires = data.get("expiresAt") or data.get("session", {}).get("expiresAt")
    if not session_id:
        print("no sessionId in response, aborting")
        sys.exit(1)

    os.makedirs("/root/titan", exist_ok=True)
    if fresh or a.key or a.new:
        with open(WALLET, "w") as f:
            json.dump({"address": address, "privateKey": acct.key.hex()}, f, indent=1)
        os.chmod(WALLET, 0o600)
        print("wallet saved:", WALLET, address)

    with open(SESSION, "w") as f:
        json.dump({
            "sessionId": session_id,
            "privateKey": sk.encode().hex(),
            "publicKeyHex": pub_hex,
            "expiresAtMs": int(datetime.datetime.fromisoformat(
                expires.replace("Z", "+00:00")).timestamp() * 1000) if expires else 0,
            "address": address,
        }, f, indent=1)
    os.chmod(SESSION, 0o600)
    print("session saved:", SESSION, "id", session_id, "expires", expires)


if __name__ == "__main__":
    main()
