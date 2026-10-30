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
SESSION = "/root/titan/sessions/main.json"
SERVICE = "titan-farm"


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
    for l in st.get("last", []):
        lines.append("  " + l)
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
