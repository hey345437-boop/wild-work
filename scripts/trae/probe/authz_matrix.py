# -*- coding: utf-8 -*-
"""auth_from 矩阵探测：找出哪个 auth_from 能绕过 TraeWork 的地区墙。

来源（反编译）：
  chunk 9934  ->  <C scope="solo" loginPlatform="solo" platformCode="SOLO_PC" blockTTP={true} />
  chunk 7093  ->  <C scope="trae" loginPlatform="trae" platformCode="IDE_PC"  />   // 无 blockTTP
  chunk 6597  ->  C 组件本体：
        W || (er && M)                  ? <Spinner/>
      : (blockTTP && isUS(StoreCountry))? <Unavailable/>
      :                                   <登录卡片/按钮>

  即：blockTTP 是路由级常量，唯一决定「美国账号是否被墙」。
  本脚本用同一个 CK + 同一个 client_id，只变 auth_from，看落到哪个渲染分支。

用法: .venv/bin/python trae-recon/probe/authz_matrix.py [socks5://host:port] [account_index]
"""
import json
import os
import sys
import time
import glob
import socket
import threading
import http.server
import socketserver
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, parse_qsl

import requests
from playwright.sync_api import sync_playwright

CK_FILE = "/Users/lan/dsh/trae-recon/accounts/trae-ck.jsonl"
WW = "http://127.0.0.1:7866"

PROXY = sys.argv[1] if len(sys.argv) > 1 else None
IDX = int(sys.argv[2]) if len(sys.argv) > 2 else 0

VARIANTS = [
    # (auth_from, login_channel)
    ("solo",      "ai_extension"),   # 基线：已知 -> TraeWork Unavailable
    ("trae",      "ai_extension"),   # 用户观察到 traecode -> trae 的重定向目标
    ("traecode",  "ai_extension"),
    ("ide",       "ai_extension"),
    ("traework",  "ai_extension"),
    ("solo",      "native_ide"),     # native_ide 基线
]

HITS = []


class CB(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _r(self, m):
        b = b""
        if m == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            b = self.rfile.read(n) if n else b""
        HITS.append((m, self.path, b.decode("utf-8", "replace")))
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(b"OK")

    do_GET = lambda self: self._r("GET")
    do_POST = lambda self: self._r("POST")
    do_OPTIONS = lambda self: self._r("OPTIONS")

    def log_message(self, *a):
        pass


def main():
    rows = [json.loads(l) for l in open(CK_FILE, encoding="utf-8") if l.strip()]
    ck = rows[IDX]

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), CB)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cb = f"http://127.0.0.1:{port}/authorize"

    print(f"账号 {IDX}: {ck['email'][:20]}… uid={ck['uid']} region={ck.get('region')}")
    print(f"代理: {PROXY or '直连'}   回调: {cb}\n")

    requests.post(f"{WW}/api/login/cancel", timeout=10)
    time.sleep(1.2)
    r = requests.post(f"{WW}/api/login/start", json={"channel": "traework"}, timeout=25).json()
    base = r.get("auth_url", "")
    if not base:
        print("❌", r)
        sys.exit(1)
    u = urlparse(base)
    baseq = dict(parse_qsl(u.query))

    ch = glob.glob(os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/"
        "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"))
    kw = {"headless": True, "args": ["--disable-blink-features=AutomationControlled"]}
    if ch:
        kw["executable_path"] = ch[0]

    results = []
    with sync_playwright() as p:
        b = p.chromium.launch(**kw)
        ckw = {"locale": "en-US", "viewport": {"width": 1280, "height": 800}}
        if PROXY:
            ckw["proxy"] = {"server": PROXY}
        ctx = b.new_context(**ckw)
        ctx.add_cookies([{"name": k, "value": v, "domain": ".trae.ai",
                          "path": "/", "secure": True} for k, v in ck["cookies"].items()])
        pg = ctx.new_page()

        for af, lc in VARIANTS:
            q = dict(baseq)
            q["auth_from"] = af
            q["login_channel"] = lc
            q["auth_callback_url"] = cb
            url = urlunparse(u._replace(query=urlencode(q)))

            HITS.clear()
            print(f"{'='*72}\nauth_from={af}  login_channel={lc}")
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print("  goto:", str(e)[:90])
            time.sleep(11)

            landed = pg.url
            body = pg.evaluate(
                "() => (document.body && document.body.innerText || '').replace(/\\s+/g,' ').trim()")
            btns = pg.evaluate(
                "() => [...document.querySelectorAll('button')].map(b=>b.innerText.trim()).filter(Boolean)")

            print(f"  落点 : {landed.replace('https://www.trae.ai','')[:110]}")
            print(f"  正文 : {body[:170] or '<空>'}")
            print(f"  按钮 : {btns}")
            print(f"  回调 : {HITS if HITS else '无'}")

            verdict = "?"
            if "Unavailable" in body:
                verdict = "❌ 地区墙 (blockTTP=true 路由)"
            elif HITS:
                verdict = "🎯 回调命中！"
            elif body.strip():
                verdict = "✅ 渲染了登录卡片"
            else:
                verdict = "⚠️  空白页"
            print(f"  判定 : {verdict}")
            results.append((af, lc, verdict, landed, body[:90], bool(HITS)))

            if HITS:
                for m, path, bd in HITS:
                    print(f"     -> {m} {path[:70]}  {bd[:200]}")
                break

        pg.goto("about:blank")
        b.close()

    print("\n" + "=" * 72)
    print("汇总")
    for af, lc, v, land, bd, hit in results:
        print(f"  {af:10s} / {lc:13s}  {v}")
    srv.shutdown()


if __name__ == "__main__":
    main()
