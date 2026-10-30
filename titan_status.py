#!/usr/bin/env python3
"""One-screen status for the Titan Auto Trade Bot.

  python3 titan_status.py            # full status
  python3 titan_status.py --watch    # refresh every 5s
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from titan_client import TitanClient   # noqa: E402

LOG = "/var/log/titan-farm.log"
SERVICE = "titan-farm"


def pick_session():
    """First wallet in accounts.json, else main.json, else any session file."""
    cfg = "/root/titan/accounts.json"
    if os.path.exists(cfg):
        try:
            for acc in json.load(open(cfg)).get("accounts", []):
                p = f"/root/titan/sessions/{acc.get('name')}.json"
                if os.path.exists(p):
                    return p
        except Exception:
            pass
    for cand in ("/root/titan/sessions/main.json", "/root/titan/session.json"):
        if os.path.exists(cand):
            return cand
    return "/root/titan/session.json"


SESSION = pick_session()


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:
        return ""


def service_state():
    state = sh(f"systemctl is-active {SERVICE}") or "unknown"
    since = sh(f"systemctl show {SERVICE} -p ActiveEnterTimestamp --value")
    restarts = sh(f"systemctl show {SERVICE} -p NRestarts --value")
    up = ""
    if since:
        try:
            t = time.mktime(time.strptime(since, "%a %Y-%m-%d %H:%M:%S %Z"))
            secs = max(0, time.time() - t)
            up = f"{int(secs//3600)}h {int(secs%3600//60)}m"
        except Exception:
            up = since
    return state, up, restarts


def log_stats():
    if not os.path.exists(LOG):
        return {}
    txt = open(LOG, errors="ignore").read()
    fees = [float(x) for x in re.findall(r'"fee": "([0-9.]+)"', txt)]
    return {
        "fills": len(fees),
        "fees": round(sum(fees), 2),
        "cycles": txt.count("closed by"),
        "entries": txt.count("order buy -> 200") + txt.count("order sell -> 200"),
        "last": [l for l in txt.strip().splitlines()[-3:]],
    }


def wallets():
    """One line per wallet in accounts.json."""
    cfg_path = "/root/titan/accounts.json"
    out = []
    if not os.path.exists(cfg_path):
        return out
    try:
        cfg = json.load(open(cfg_path))
    except Exception:
        return out
    for acc in cfg.get("accounts", []):
        name = acc.get("name", "?")
        sess = f"/root/titan/sessions/{name}.json"
        proxy = acc.get("proxy") or ""
        if not os.path.exists(sess):
            out.append((name, "no session yet", "", "run it once to mint"))
            continue
        try:
            c = TitanClient(sess, proxy=proxy or None)
            _, bal = c.balance()
            _, pos = c.positions()
            items = [(p["market"], p["size"]) for p in pos.get("items", [])]
            out.append((name, bal.get("balance", "?"), "flat" if not items else str(items),
                        "via " + proxy.split("@")[-1] if proxy else "direct"))
        except Exception as e:
            out.append((name, "error", str(e)[:40], ""))
    return out


def account():
    if not os.path.exists(SESSION):
        return None
    try:
        c = TitanClient(SESSION)
        _, bal = c.balance()
        _, pos = c.positions()
        _, pts = c._call("GET", "/v1/me/points")
        items = [(p["market"], p["size"], p.get("entryPrice")) for p in pos.get("items", [])]
        return {"balance": bal.get("balance"), "positions": items,
                "points": pts.get("total"), "rank": pts.get("rank"),
                "parties": pts.get("rankedParties")}
    except Exception as e:
        return {"error": str(e)[:120]}


def render():
    state, up, restarts = service_state()
    acc = account() or {}
    st = log_stats()
    lines = [
        "Titan Auto Trade Bot",
        "--------------------",
        f"service    {state}" + (f"  up {up}" if up else "") + (f"  restarts {restarts}" if restarts else ""),
        f"balance    {acc.get('balance', '?')} USDCx",
        f"positions  {acc.get('positions') if acc.get('positions') else 'flat'}",
        f"points     {acc.get('points', '?')}   rank {acc.get('rank', '?')} of {acc.get('parties', '?')}",
        f"activity   {st.get('cycles', 0)} cycles, {st.get('fills', 0)} fills, {st.get('fees', 0)} USDC fees",
    ]
    if os.path.exists("/root/titan/PROXY_ALERT"):
        last = open("/root/titan/PROXY_ALERT").read().strip().splitlines()
        if last:
            lines.insert(2, "ALERT      " + last[-1][:100])
    ws = wallets()
    if len(ws) > 1:
        lines.append("wallets")
        for name, bal, pos, extra in ws:
            lines.append(f"  {name:10} {bal:>16}  {pos}  {extra}")
    lines.append("log")
    for l in st.get("last", []):
        lines.append("  " + l.strip())
    return "\n".join(lines)


def main():
    watch = "--watch" in sys.argv
    if not watch:
        print(render())
        return
    try:
        while True:
            print("\033[H\033[J" + render(), flush=True)
            time.sleep(5)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
