# -*- coding: utf-8 -*-
"""抓一个 AuthCode，然后手工打候选 ExchangeToken 端点，定位正确的 (origin, path)。

背景：wild-work 用 UgHost(https://api.trae.ai) + "/trae/api/v3/oauth/ExchangeToken" -> 404。
从 www.trae.ai 的 bundle (async/9839) 反编译出两个真实方法：

    TraeExchangeToken   -> {base}/cloudide/api/v3/trae/oauth/ExchangeToken
    TraeExchangeTokenV2 -> {base}/trae/api/v3/oauth/ExchangeToken
    body = {ClientID, ClientSecret, RefreshToken, UserID,
            DeviceInfo, DeviceProof, AuthCode, CodeVerifier}

404 = 路由不存在，authCode 不会被消费，所以可以拿同一个 code 连打多个候选。

用法: .venv/bin/python trae-recon/probe/exchange_probe.py [socks5://host:port] [account_index]
"""
import json
import os
import sys
import time
import glob
import base64
import threading
import http.server
import socketserver
from urllib.parse import urlparse, urlencode, urlunparse, parse_qsl, unquote

import requests
from playwright.sync_api import sync_playwright

CK_FILE = "/Users/lan/dsh/trae-recon/accounts/trae-ck.jsonl"
WW = "http://127.0.0.1:7866"
STATE = "/Users/lan/dsh/wild-work/data/login-state.json"

PROXY = sys.argv[1] if len(sys.argv) > 1 else None
IDX = int(sys.argv[2]) if len(sys.argv) > 2 else 0

CAUGHT = []


class CB(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self):
        CAUGHT.append(self.path)
        p = b"<html><body>got it</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(p)))
        self.end_headers()
        self.wfile.write(p)

    def log_message(self, *a):
        pass


ORIGINS = ["https://ug-normal.us.trae.ai", "https://api.trae.ai"]
PATHS = ["/cloudide/api/v3/trae/oauth/ExchangeToken", "/trae/api/v3/oauth/ExchangeToken"]
PLATFORMS = ["IDE_PC", "SOLO_PC"]


def main():
    rows = [json.loads(l) for l in open(CK_FILE, encoding="utf-8") if l.strip()]
    ck = rows[IDX]

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), CB)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cb = f"http://127.0.0.1:{port}/authorize"

    requests.post(f"{WW}/api/login/cancel", timeout=10)
    time.sleep(1.5)
    r = requests.post(f"{WW}/api/login/start", json={"channel": "traework"}, timeout=25).json()
    if not r.get("auth_url"):
        print("❌", r)
        sys.exit(1)

    st = json.load(open(STATE))
    verifier = st.get("codeVerifier") or ""
    machine_id = st.get("machineId") or ""
    device_id = st.get("deviceId") or ""
    print(f"codeVerifier: {verifier[:40]}…")
    print(f"deviceId={device_id}  machineId={machine_id[:24]}…\n")

    u = urlparse(r["auth_url"])
    q = dict(parse_qsl(u.query))
    client_id = q.get("client_id")
    q.update({"auth_from": "trae", "login_channel": "ai_extension",
              "x_app_version": "99.99.99", "auth_callback_url": cb})
    url = urlunparse(u._replace(query=urlencode(q)))

    ch = glob.glob(os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/"
        "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"))
    kw = {"headless": True, "args": ["--disable-blink-features=AutomationControlled"]}
    if ch:
        kw["executable_path"] = ch[0]

    with sync_playwright() as p:
        b = p.chromium.launch(**kw)
        ckw = {"locale": "en-US", "viewport": {"width": 1280, "height": 800}}
        if PROXY:
            ckw["proxy"] = {"server": PROXY, "bypass": "127.0.0.1,localhost"}
        ctx = b.new_context(**ckw)
        ctx.add_cookies([{"name": k, "value": v, "domain": ".trae.ai",
                          "path": "/", "secure": True} for k, v in ck["cookies"].items()])
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(9000)

        c = pg.evaluate("""() => {
            let best = null;
            for (const el of document.querySelectorAll('button,a,div,span')) {
                const t = (el.innerText||'').trim();
                if (!t.startsWith('Log in and open') || t.length > 40) continue;
                const r = el.getBoundingClientRect();
                if (r.width <= 0) continue;
                const a = r.width*r.height;
                if (!best || a < best.a) best = {x:r.x+r.width/2, y:r.y+r.height/2, a};
            }
            return best;
        }""")
        if not c:
            print("❌ 没有登录按钮，页面:", pg.evaluate("()=>document.body.innerText.slice(0,120)"))
            b.close()
            return
        pg.mouse.click(c["x"], c["y"])
        for _ in range(25):
            pg.wait_for_timeout(1000)
            if CAUGHT:
                break
        b.close()
    srv.shutdown()

    if not CAUGHT:
        print("❌ 没收到回调")
        return

    qs = dict(parse_qsl(urlparse(CAUGHT[0]).query))
    info = json.loads(qs["authCodeInfo"])
    auth_code = info["AuthCode"]
    host = qs.get("host")
    print(f"🎯 AuthCode = {auth_code}")
    print(f"   回调 host = {host}")
    print(f"   userRegion= {qs.get('userRegion')}")
    print(f"   ExpireAt  = {info.get('ExpireAt')}\n")

    origins = [host] + [o for o in ORIGINS if o != host]
    sess = requests.Session()
    if PROXY:
        pxy = {"http": PROXY, "https": PROXY}
        sess.proxies = pxy

    print("候选矩阵 (404 = 路由不存在，code 不消费):")
    for origin in origins:
        for path in PATHS:
            for plat in PLATFORMS:
                body = {
                    "ClientID": client_id,
                    "ClientSecret": "-",
                    "AuthCode": auth_code,
                    "CodeVerifier": verifier,
                    "UserID": "",
                    "DeviceInfo": {
                        "DeviceID": device_id, "MachineID": machine_id,
                        "PlatformCode": plat, "DeviceType": "PC",
                        "DeviceName": "DESKTOP-TRAE", "DeviceModel": "20Y5A002XX",
                        "ClientVersion": "0.1.52", "OSInfo": "windows",
                        "OSVersion": "Windows 10 Pro",
                    },
                    "IDEVersion": "0.1.52",
                }
                try:
                    rr = sess.post(origin + path, json=body, timeout=30,
                                   headers={"Content-Type": "application/json"})
                    txt = rr.text[:200].replace("\n", " ")
                    flag = "✅" if rr.status_code == 200 else ("·" if rr.status_code == 404 else "❗")
                    print(f"  {flag} {rr.status_code:3d} {plat:8s} {origin}{path}")
                    if rr.status_code != 404:
                        print(f"        {txt}")
                    if rr.status_code == 200:
                        print("\n★★★ 命中：")
                        try:
                            print(json.dumps(rr.json(), indent=2, ensure_ascii=False)[:1600])
                        except Exception:
                            print(rr.text[:1600])
                        return
                except Exception as e:
                    print(f"  ✗ {origin}{path} :: {str(e)[:80]}")


if __name__ == "__main__":
    main()
