# -*- coding: utf-8 -*-
"""把 AuthCode 换成 token，然后把响应里的**每个** token 字段拿去打真接口，定位该用哪个。

现状：ExchangeToken 返回的 Result.Token（JWT, source=refresh_token）当 Cloud-IDE-JWT 用，
      GetUserInfo 回 401/20310 "The user is not logged in"，GetUserToken 回 401/20101
      "Token invalid"。说明它只是 OAuth 客户端令牌，不是会话令牌。
      响应里还有 UserJwt 等字段没试过。

用法: .venv/bin/python trae-recon/probe/token_matrix.py [socks5://host:port] [account_index]
"""
import json
import os
import sys
import time
import glob
import threading
import http.server
import socketserver
from urllib.parse import urlparse, urlencode, urlunparse, parse_qsl

import requests
from playwright.sync_api import sync_playwright

CK_FILE = "/Users/lan/dsh/trae-recon/accounts/trae-ck.jsonl"
WW = "http://127.0.0.1:7866"

PROXY = sys.argv[1] if len(sys.argv) > 1 else None
IDX = int(sys.argv[2]) if len(sys.argv) > 2 else 0
# requests 这个 venv 没装 PySocks；好在 trae API 直连可达（CheckLogin 实测 200），
# 代理只给浏览器用就够了。
PROXIES = None

CAUGHT = []


class CB(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self):
        CAUGHT.append(self.path)
        p = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(p)))
        self.end_headers()
        self.wfile.write(p)

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

    requests.post(f"{WW}/api/login/cancel", timeout=10)
    time.sleep(1.5)
    r = requests.post(f"{WW}/api/login/start", json={"channel": "traework"}, timeout=25).json()
    if not r.get("auth_url"):
        print("❌", r)
        sys.exit(1)
    st = json.load(open("/Users/lan/dsh/wild-work/data/login-state.json"))
    verifier, machine_id, device_id = st.get("codeVerifier"), st.get("machineId"), st.get("deviceId")

    u = urlparse(r["auth_url"])
    q = dict(parse_qsl(u.query))
    client_id = q.get("client_id")
    q["auth_callback_url"] = cb          # 拦截到我自己这，保住 authCode
    url = urlunparse(u._replace(query=urlencode(q)))
    print(f"回调已改到本地: {cb}")

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
            let best=null;
            for (const el of document.querySelectorAll('button,a,div,span')) {
                const t=(el.innerText||'').trim();
                if(!t.startsWith('Log in and open')||t.length>40) continue;
                const r=el.getBoundingClientRect(); if(r.width<=0) continue;
                const a=r.width*r.height; if(!best||a<best.a) best={x:r.x+r.width/2,y:r.y+r.height/2,a};
            } return best;
        }""")
        if not c:
            print("❌ 无登录按钮:", pg.evaluate("()=>document.body.innerText.slice(0,150)"))
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
        print("❌ 无回调")
        return
    qs = dict(parse_qsl(urlparse(CAUGHT[0]).query))
    info = json.loads(qs["authCodeInfo"])
    auth_code = info["AuthCode"]
    host = qs.get("host")
    print(f"AuthCode={auth_code}  host={host}\n")

    body = {
        "ClientID": client_id, "ClientSecret": "-", "AuthCode": auth_code,
        "CodeVerifier": verifier, "UserID": "",
        "DeviceInfo": {"DeviceID": device_id, "MachineID": machine_id, "PlatformCode": "IDE_PC",
                       "DeviceType": "PC", "DeviceName": "DESKTOP-TRAE", "DeviceModel": "20Y5A002XX",
                       "ClientVersion": "2.3.88407", "OSInfo": "windows",
                       "OSVersion": "Windows 10 Pro"},
        "IDEVersion": "2.3.88407",
    }
    rr = requests.post(host + "/cloudide/api/v3/trae/oauth/ExchangeToken",
                       json=body, timeout=40, proxies=PROXIES)
    print(f"ExchangeToken -> {rr.status_code}")
    res = rr.json().get("Result", {})
    print("Result 字段:")
    for k, v in res.items():
        vs = str(v)
        print(f"  {k:20s}= {vs[:60]}{'…' if len(vs)>60 else ''}")

    tokens = {k: v for k, v in res.items() if isinstance(v, str) and len(v) > 60}
    if not tokens:
        print("没有像 token 的字段")
        return
    print(f"\ntoken 候选: {list(tokens)}\n")

    # 拿每个 token 打真接口
    targets = [
        ("POST", host + "/cloudide/api/v3/trae/GetUserInfo", {"IfWebPage": True}),
        ("POST", host + "/cloudide/api/v3/trae/CheckLogin", {}),
        ("POST", host + "/cloudide/api/v3/common/GetUserToken", {}),
        ("POST", "https://trae-api-us.mchost.guru/api/ide/v1/get_detail_param",
         {"function": "solo_agent", "config_names": None, "need_prompt": False,
          "current_config_info": None, "poly_prompt": True, "mode_type": None, "agent_type": None}),
    ]
    print("=" * 78)
    for tname, tok in tokens.items():
        for scheme in ("Cloud-IDE-JWT", "Bearer"):
            for hdr in ("Authorization", "X-Cloudide-Token", "X-Ide-Token"):
                m, url_, payload = targets[0]
                h = {"Content-Type": "application/json", "User-Agent": "Trae/2.3.88407",
                     f"{hdr}": f"{scheme} {tok}"}
                try:
                    resp = requests.post(url_, json=payload, headers=h, timeout=30, proxies=PROXIES)
                    ok = resp.status_code == 200 and '"IsLogin":true' in resp.text.replace(" ", "")
                    print(f"  {'✅' if ok else '  '} {tname[:10]:10s} {hdr:17s} {scheme:14s} -> {resp.status_code} {resp.text[:80]}")
                    if ok:
                        print("\n★★★ 生效组合:", tname, hdr, scheme, url_)
                        print(resp.text[:400])
                        return
                except Exception as e:
                    print(f"  ✗ {tname} {hdr} {scheme}: {str(e)[:70]}")
            # CheckLogin 用另一种判据
    print("\n(按 IsLogin:true 判定，未命中)")

    # 再单独看 CheckLogin 结果，别用 IsLogin 判据
    print("\n-- CheckLogin 采样 --")
    for tname, tok in list(tokens.items())[:3]:
        resp = requests.post(host + "/cloudide/api/v3/trae/CheckLogin", json={},
                             headers={"Content-Type": "application/json",
                                      "Authorization": f"Cloud-IDE-JWT {tok}"},
                             timeout=30, proxies=PROXIES)
        try:
            j = resp.json().get("Result", {})
            print(f"  {tname[:12]:12s} IsLogin={j.get('IsLogin')} UserID={j.get('UserID')} Region={j.get('Region')} Host={j.get('Host')}")
        except Exception:
            print(f"  {tname[:12]:12s} {resp.status_code} {resp.text[:100]}")


if __name__ == "__main__":
    main()
