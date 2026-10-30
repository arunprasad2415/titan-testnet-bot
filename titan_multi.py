#!/usr/bin/env python3
"""Run several Titan accounts at once, each with its own session and optional proxy.

  python3 titan_multi.py --init                  # write accounts.example.json
  python3 titan_multi.py --config accounts.json  # run every account in parallel
  python3 titan_multi.py --config accounts.json --dry-run

Config shape:

{
  "defaults": {"market": "BTC-USDCX", "size": 0.002, "leverage": 5, "tp": 1.2,
               "sl": 0.9, "timeout": 90, "max_dd": 15, "cycles": 0,
               "stealth": true, "momentum": 0.01},
  "accounts": [
    {"name": "alice", "privateKey": "0x...", "proxy": "http://user:pass@host:port"},
    {"name": "bob",   "privateKey": "0x...", "size": 0.001}
  ]
}

Each account gets sessions/<name>.json (mode 600). Sessions are reused until they
are within a day of expiry, then re-minted from the key. Cycles 0 means run until
you stop it. A KILL file in this directory stops every worker.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SESS_DIR = os.path.join(HERE, "sessions")
KILL = os.path.join(HERE, "KILL")
EXAMPLE = {
    "defaults": {"market": "BTC-USDCX", "size": 0.002, "leverage": 5, "tp": 1.2,
                 "sl": 0.9, "timeout": 90, "max_dd": 15, "cycles": 0,
                 "stealth": True, "momentum": 0.01},
    "accounts": [
        {"name": "alice", "privateKey": "0xREPLACE", "proxy": ""},
        {"name": "bob", "privateKey": "0xREPLACE", "proxy": ""},
    ],
}


def load(path):
    cfg = json.load(open(path))
    defaults = cfg.get("defaults", {})
    out = []
    for acc in cfg.get("accounts", []):
        merged = dict(defaults)
        merged.update({k: v for k, v in acc.items() if k != "name"})
        merged["name"] = acc["name"]
        out.append(merged)
    return out


def ensure_session(acc):
    """Mint a session for this account if missing or close to expiry."""
    from titan_cli import mint_session
    from titan_auth import SESSION as _unused  # noqa: F401  (import side effect only)
    path = os.path.join(SESS_DIR, f"{acc['name']}.json")
    key = acc.get("privateKey", "")
    if os.path.exists(path):
        try:
            s = json.load(open(path))
            left = (s.get("expiresAtMs", 0) / 1000) - time.time()
            if left > 86400 and s.get("address"):
                print(f"[{acc['name']}] session reused, {left/3600:.0f}h left")
                return path
        except Exception:
            pass
    if not key or key.endswith("REPLACE"):
        print(f"[{acc['name']}] no private key in config, cannot mint a session")
        return None
    print(f"[{acc['name']}] minting session ...")
    client, address, sess = mint_session(key, session_path=path, proxy=acc.get("proxy") or None)
    print(f"[{acc['name']}] session ok for {address}")
    return path


def stream(prefix, proc, color=None):
    for line in proc.stdout:
        line = line.rstrip("\n")
        if line:
            print(f"[{prefix}] {line}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="run a single account by name")
    ap.add_argument("--add", nargs=2, metavar=("NAME", "KEY"), help="add or replace a wallet")
    ap.add_argument("--proxy", help="proxy for the wallet being added")
    ap.add_argument("--remove", help="remove a wallet by name")
    ap.add_argument("--list", action="store_true", help="show the wallets in the config")
    a = ap.parse_args()

    cfg_path = os.path.join(HERE, "accounts.json")

    if a.add or a.remove or a.list:
        cfg = json.load(open(cfg_path)) if os.path.exists(cfg_path) else dict(EXAMPLE)
        accs = cfg.setdefault("accounts", [])
        if a.add:
            name, key = a.add
            entry = {"name": name, "privateKey": key}
            if a.proxy:
                entry["proxy"] = a.proxy
            accs[:] = [x for x in accs if x.get("name") != name] + [entry]
            json.dump(cfg, open(cfg_path, "w"), indent=1)
            os.chmod(cfg_path, 0o600)
            print(f"added {name}" + (f" via {a.proxy.split('@')[-1]}" if a.proxy else " (direct)"))
        if a.remove:
            accs[:] = [x for x in accs if x.get("name") != a.remove]
            json.dump(cfg, open(cfg_path, "w"), indent=1)
            os.chmod(cfg_path, 0o600)
            print(f"removed {a.remove}")
        if a.list or not (a.add or a.remove):
            print(f"{len(accs)} wallet(s) in {cfg_path}:")
            for x in accs:
                px = (x.get("proxy") or "direct").split("@")[-1]
                print(f"  {x.get('name'):10} {px}")
        return

    if a.init:
        path = os.path.join(HERE, "accounts.example.json")
        json.dump(EXAMPLE, open(path, "w"), indent=2)
        print("wrote", path, "- fill in privateKey per account, then: --config", path)
        return

    if not a.config:
        print("need --config accounts.json (or --init to create a template)")
        sys.exit(1)

    accounts = load(a.config)
    if a.only:
        accounts = [x for x in accounts if x["name"] == a.only]
    if not accounts:
        print("no accounts in config")
        sys.exit(1)

    os.makedirs(SESS_DIR, exist_ok=True)
    if os.path.exists(KILL):
        print("KILL file present, remove it first")
        sys.exit(1)

    procs = []
    for acc in accounts:
        if a.dry_run:
            acc["dry_run"] = True
            path = os.path.join(SESS_DIR, f"{acc['name']}.json")
        else:
            path = ensure_session(acc)
            if not path:
                continue
        cfg = {k: v for k, v in acc.items() if k not in ("privateKey",)}
        cfg["index"] = accounts.index(acc)
        cmd = [sys.executable, os.path.join(HERE, "titan_worker.py"),
               "--session", path, "--config", json.dumps(cfg)]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
        procs.append((acc["name"], p))
        threading.Thread(target=stream, args=(acc["name"], p), daemon=True).start()
        time.sleep(0.4)      # stagger the starts, they should not look synchronised

    if not procs:
        print("nothing started")
        sys.exit(1)

    print(f"\n{len(procs)} account(s) running: " + ", ".join(n for n, _ in procs))
    print("stop with ctrl-c, or touch KILL\n")

    stop = threading.Event()

    def shutdown(*_):
        stop.set()
        open(KILL, "w").write("stop\n")
        for _, p in procs:
            if p.poll() is None:
                p.terminate()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    reported = set()
    try:
        while not stop.is_set():
            for n, p in procs:
                rc = p.poll()
                if rc is not None and n not in reported:
                    reported.add(n)
                    if rc == 3:
                        print(f"!! [{n}] stopped: proxy unreachable", flush=True)
                    elif rc != 0:
                        print(f"!! [{n}] exited with code {rc}", flush=True)
                    else:
                        print(f"[{n}] finished", flush=True)
            alive = [n for n, p in procs if p.poll() is None]
            if not alive:
                break
            time.sleep(2)
    finally:
        shutdown()
        for n, p in procs:
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()
        if os.path.exists(KILL):
            os.remove(KILL)
        print("\nall workers stopped")


if __name__ == "__main__":
    main()
