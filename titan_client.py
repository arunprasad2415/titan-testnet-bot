#!/usr/bin/env python3
"""Titan Exchange testnet API client (Canton perp DEX).

Auth model, reverse engineered from the app bundle:
  session   ed25519 key registered once by a wallet signature (app UI)
  headers   X-Session-Id + X-Session-Sig  (action endpoints: POST /v1/orders, ...)
            X-Session-Id + X-Request-Ts + X-Request-Nonce + X-Request-Sig (plain GETs)

Canonical strings are exact; the server rejected a bad nonce and an unknown
session, which proves the format is parsed and verified.

Session file (JSON):  {"sessionId": "...", "privateKey": "<hex seed>", "expiresAtMs": 0}
"""
import datetime
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid

try:
    from curl_cffi import requests as _cffi      # Chrome TLS fingerprint, proxy aware
except ImportError:
    _cffi = None
try:
    import requests as _plain
except ImportError:
    _plain = None

from nacl.signing import SigningKey

BASE = "https://testnet-api.titanx.cc"
ORIGIN = "https://testnet.titanx.cc"
# the request canonical signs the API origin, not the app origin (verified live)
SIGN_ORIGIN = BASE
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/156.0.0.0 Safari/537.36")
EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

BASE_HEADERS = {
    "accept": "application/json",
    "user-agent": UA,
    "sec-ch-ua": '"Chromium";v="156", "Not:A-Brand";v="8"',
    "sec-ch-ua-platform": '"Windows"',
    "referer": ORIGIN + "/",
}


def iso_ts(dt=None):
    """JS G(): ISO-8601 UTC, milliseconds with trailing zeros trimmed."""
    dt = dt or datetime.datetime.now(datetime.timezone.utc)
    head = dt.strftime("%Y-%m-%dT%H:%M:%S")
    ms = f"{dt.microsecond // 1000:03d}".rstrip("0")
    return f"{head}.{ms}Z" if ms else f"{head}Z"


def dec10(value):
    """JS W(): decimal string forced to 10 fractional digits."""
    s = str(value).strip()
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    whole, _, frac = s.partition(".")
    whole = whole.lstrip("0") or "0"
    frac = (frac + "0" * 10)[:10]
    body = f"{whole}.{frac}"
    if whole == "0" and frac == "0" * 10:
        return body
    return ("-" if neg else "") + body


def line(name, value):
    """JS U(): 'name:' padded to 14 chars, then the value."""
    return f"{name}:".ljust(14) + str(value)


def req_line(name, value):
    """Request canonical uses a single space after the colon, no padding."""
    return f"{name}: {value}"


def price5(value):
    """Non-integer prices may carry at most 5 significant figures."""
    from decimal import Decimal, ROUND_HALF_UP
    d = Decimal(str(value))
    if d == 0:
        return "0.0"
    exp = d.adjusted() - 4
    q = Decimal(1).scaleb(exp)
    out = format(d.quantize(q, rounding=ROUND_HALF_UP), "f")
    if "." in out:
        out = out.rstrip("0").rstrip(".")
    return out


class TitanClient:
    def __init__(self, session_path, base=BASE, timeout=20, proxy=None, impersonate="chrome"):
        self.base = base
        self.timeout = timeout
        self.proxy = proxy
        self.http = None
        self.http_kind = None
        if _cffi is not None:
            self.http = _cffi.Session(impersonate=impersonate)
            if proxy:
                self.http.proxies = {"http": proxy, "https": proxy}
            self.http_kind = "curl_cffi"
        elif _plain is not None:
            self.http = _plain.Session()
            self.http.trust_env = False
            if proxy:
                self.http.proxies = {"http": proxy, "https": proxy}
            self.http_kind = "requests"
        s = json.load(open(session_path))
        self.session_id = s["sessionId"]
        seed = bytes.fromhex(s["privateKey"])
        self.sk = SigningKey(seed)
        self.pub_hex = self.sk.verify_key.encode().hex()
        self.expires_at_ms = int(s.get("expiresAtMs") or 0)
        self.nonces = {}

    # ---------- signing ----------
    def _nonce(self, scope):
        n = max(self.nonces.get(scope, 0) + 1, int(time.time() * 1000))
        self.nonces[scope] = n
        return n

    def _sign(self, canonical):
        return self.sk.sign(canonical.encode()).signature.hex()

    def _request_headers(self, method, path, query="", body=b""):
        ts = iso_ts()
        nonce = str(self._nonce("request"))
        body_sha = hashlib.sha256(body).hexdigest() if body else EMPTY_SHA
        canonical = "\n".join([
            "Daml-Perp-Request v=2",
            req_line("aud", "rest"),
            req_line("origin", SIGN_ORIGIN),
            req_line("session", self.session_id),
            req_line("conn", ""),
            req_line("method", method),
            req_line("path", path),
            req_line("query", query),
            req_line("body", body_sha),
            req_line("ts", ts),
            req_line("nonce", nonce),
            req_line("expires", ""),
        ])
        h = dict(BASE_HEADERS)
        h.update({"X-Session-Id": self.session_id, "X-Request-Ts": ts,
                  "X-Request-Nonce": nonce, "X-Request-Sig": self._sign(canonical)})
        self.last_canonical = canonical
        return h

    def _action_headers(self, canonical):
        h = dict(BASE_HEADERS)
        h.update({"X-Session-Id": self.session_id, "X-Session-Sig": self._sign(canonical)})
        return h

    # ---------- transport ----------
    def _signed_write(self, method, path, body, canonical):
        """Every write needs both signatures: the action one names the credential,
        the request one covers the body. One alone answers missing_headers."""
        payload = json.dumps(body, separators=(",", ":")).encode()
        hdrs = self._action_headers(canonical)
        req = self._request_headers(method, path, "", payload)
        hdrs.update({k: v for k, v in req.items() if k.startswith("X-Request")})
        return self._call(method, path, body=body, headers=hdrs)

    def _call(self, method, path, query="", body=None, headers=None):
        url = self.base + path + (("?" + query) if query else "")
        data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        hdrs = headers or self._request_headers(method, path, query, data or b"")
        if data:
            hdrs["content-type"] = "application/json"
        if os.environ.get("TITAN_DEBUG"):
            print(f"--- {method} {path} ---")
            print("canonical:\n" + getattr(self, "last_canonical", "(action-signed)"))
            print("headers:", {k: v for k, v in hdrs.items() if k.lower().startswith("x-")})
        if self.http is not None:
            try:
                r = self.http.request(method, url, data=data, headers=hdrs,
                                      timeout=self.timeout)
                return r.status_code, (r.json() if r.content else {})
            except Exception as e:
                return 0, {"error": {"code": "transport", "message": str(e)[:200]}}

        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw[:200].decode("utf-8", "ignore")}

    # ---------- public ----------
    def markets(self):
        return self._call("GET", "/v1/markets")

    def market(self, symbol):
        return self._call("GET", f"/v1/markets/{symbol}")

    def mark(self, symbol):
        """Live mark/bid/ask straight from the venue."""
        return self._call("GET", f"/v1/markets/{symbol}/mark")

    # ---------- account ----------
    def me(self):
        return self._call("GET", "/v1/me")

    def balance(self):
        return self._call("GET", "/v1/me/balance")

    def positions(self):
        return self._call("GET", "/v1/me/positions")

    def fills(self, limit=50):
        return self._call("GET", "/v1/me/fills", f"limit={limit}")

    def account_ready(self):
        """The account exists when balance answers 200. /v1/me is unreliable
        (answers bad_body on some accounts), so do not gate on it."""
        st, bal = self.balance()
        if st == 200:
            return True, bal
        st2, pos = self.positions()
        if st2 == 200:
            return True, {"positions": pos}
        code = (bal.get("error") or {}).get("code") if isinstance(bal, dict) else None
        return False, {"balance": (st, bal), "positions": (st2, pos), "code": code}

    def points_history(self):
        return self._call("GET", "/v1/me/points/history")

    def faucet(self):
        return self._call("POST", "/v1/me/faucet", body={},
                          headers=self._action_headers(self._canonical_generic("faucet")))

    def set_leverage(self, market, leverage):
        nonce, ts = self._nonce("leverage"), iso_ts()
        canonical = "\n".join([
            "Daml-Perp-Leverage v=1",
            line("sessionId", self.session_id),
            line("market", market),
            line("leverage", leverage),
            line("nonce", nonce),
            line("ts", ts),
        ])
        body = {"leverage": leverage, "nonce": nonce, "ts": ts}
        return self._signed_write("PUT", f"/v1/me/leverage/{market}", body, canonical)

    def cancel(self, order_id):
        nonce, ts = self._nonce("cancel"), iso_ts()
        canonical = "\n".join([
            "Daml-Perp-Cancel v=1",
            line("sessionId", self.session_id),
            line("orderId", order_id),
            line("nonce", nonce),
            line("ts", ts),
        ])
        body = {"nonce": nonce, "ts": ts}
        return self._call("POST", f"/v1/me/orders/{order_id}/cancel", body=body,
                          headers=self._action_headers(canonical))

    def order(self, market, side, size, price, otype="limit", tif="ioc",
              reduce_only=False, client_order_id="", tp=None, sl=None):
        nonce, ts = self._nonce("order"), iso_ts()
        zero = dec10("0")
        canonical = "\n".join([
            "Daml-Perp-Order v=1",
            line("sessionId", self.session_id),
            line("market", market),
            line("side", side),
            line("type", otype),
            line("size", dec10(size)),
            line("price", dec10(price)),
            line("tif", tif),
            line("reduceOnly", "true" if reduce_only else "false"),
            line("tpTrigger", zero), line("tpLimit", zero), line("tpSize", zero),
            line("slTrigger", zero), line("slLimit", zero), line("slSize", zero),
            line("clientOrderId", client_order_id),
            line("nonce", nonce),
            line("ts", ts),
        ])
        body = {
            "market": market, "side": side, "type": otype,
            "size": dec10(size), "price": dec10(price), "tif": tif,
            "reduceOnly": reduce_only,
            "nonce": nonce, "ts": ts,
        }
        # tp/sl are only attached when asked for; zero-filled objects are rejected
        # with bad_attached
        if tp is not None:
            body["tp"] = tp
        if sl is not None:
            body["sl"] = sl
        # the engine wants the action signature (credential) AND the request
        # signature over the body; one without the other answers missing_headers
        return self._signed_write("POST", "/v1/orders", body, canonical)

    def _canonical_generic(self, action):
        nonce, ts = self._nonce(action), iso_ts()
        return "\n".join([
            "Daml-Perp-Request v=2",
            line("aud", "rest"),
            line("origin", SIGN_ORIGIN),
            line("session", self.session_id),
            line("conn", ""),
            line("method", "POST"),
            line("path", f"/v1/me/{action}"),
            line("query", ""),
            line("body", EMPTY_SHA),
            line("ts", ts),
            line("nonce", nonce),
            line("expires", ""),
        ])


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/titan/session.json")
    c = TitanClient(path)
    for name, fn in (("markets", c.markets), ("me", c.me), ("balance", c.balance),
                     ("positions", c.positions)):
        print(name, "->", fn())
