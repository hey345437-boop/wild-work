# -*- coding: utf-8 -*-
"""最后一环：照真 IDE 走完整链路 —— AuthCode（无签名）→ 带 DeviceProof 的 refresh。

从 Trae.app/.../main.js 反编译：

  // AuthCode 交换（无签名）
  {ClientID, AuthCode, CodeVerifier, DeviceInfo, IDEVersion}

  // refresh 交换（★ 带 DeviceProof）
  const a = _Te("POST", "/trae/api/v3/oauth/ExchangeToken", clientID, refreshToken, privateKeyPEM)
  const DeviceProof = {Signature: a.signature, Timestamp: a.timestamp, Nonce: a.nonce}
  body = {ClientID, ClientSecret:"", RefreshToken, DeviceInfo, DeviceProof, IDEVersion}

  // 签名原文（_Te）：六段用 \n 连接，ECDSA P-256 / SHA-256，结果 base64
  [method, path, clientID, refreshToken, String(timestamp), nonce].join("\n")

关键：**同一个密钥对**必须同时用于 DeviceInfo.DevicePublicKey 和 DeviceProof 的签名，
否则服务端无法验证 —— wild-work 现在生成完公钥就把私钥丢了，这正是缺的那一环。

用法: .venv/bin/python trae-recon/probe/device_proof.py [socks5://host:port] [account_index]
"""
import json
import os
import sys
import time
import glob
import hashlib
import secrets
import threading
import http.server
import socketserver
from urllib.parse import urlparse, urlencode, urlunparse, parse_qsl

import requests
from playwright.sync_api import sync_playwright
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization

CK_FILE = "/Users/lan/dsh/trae-recon/accounts/trae-ck.jsonl"
WW = "http://127.0.0.1:7866"
STATE = "/Users/lan/dsh/wild-work/data/login-state.json"

PROXY = sys.argv[1] if len(sys.argv) > 1 else None
IDX = int(sys.argv[2]) if len(sys.argv) > 2 else 0

CLIENT_IDE = "ono9krqynydwx5"
EP_V2 = "/trae/api/v3/oauth/ExchangeToken"
PERM_FILE = "/Users/lan/dsh/trae-recon/ide/device-key.json"

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


def load_key():
    """持久化密钥对 —— 官方客户端也是持久的（wild-work 每次新生成且丢私钥，是错的）"""
    if os.path.exists(PERM_FILE):
        d = json.load(open(PERM_FILE))
        priv = serialization.load_pem_private_key(d["private"].encode(), password=None)
        return priv, d["public"]
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    json.dump({"private": priv.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode(), "public": pub}, open(PERM_FILE, "w"))
    return priv, pub


def device_proof(priv, method, path, client_id, refresh_token):
    """_Te() 的 Go/JS 等价实现"""
    ts = int(time.time())
    nonce = secrets.token_hex(16)
    msg = "\n".join([method, path, client_id, refresh_token, str(ts), nonce])
    sig = priv.sign(msg.encode(), ec.ECDSA(hashes.SHA256()))
    import base64
    return {"Signature": base64.b64encode(sig).decode(),
            "Timestamp": ts, "Nonce": nonce}


def test_agent(tok, uid, dev, mach, label):
    H = {"Content-Type": "application/json", "Accept": "text/event-stream, application/json",
         "User-Agent": "Trae/0.1.63",
         "Authorization": "Cloud-IDE-JWT " + tok, "X-Cloudide-Token": tok, "X-Ide-Token": tok,
         "X-Uid": uid, "X-Device-Id": dev, "X-Machine-Id": mach,
         "X-App-Id": "931506", "X-Ide-Version": "0.1.63", "X-Ide-Version-Code": "20260904",
         "X-App-Version-Code": "20260904", "X-Version-Code": "20260904",
         "X-Device-Type": "macos", "X-Device-Platform": "darwin", "X-Platform": "darwin",
         "X-OS": "darwin", "X-OSType": "darwin", "X-System": "darwin",
         "Request-Traffic-Type": "prod"}
    BODY = {"function": "solo_work_lite", "config_names": None, "need_prompt": False,
            "current_config_info": None, "poly_prompt": True, "mode_type": None, "agent_type": None}
    for host in ("https://trae-api-sg.mchost.guru", "https://api5-normal.mchost.guru"):
        try:
            r = requests.post(host + "/api/ide/v1/get_detail_param", json=BODY, headers=H, timeout=25)
            ok = r.status_code == 200
            print(f"    {'✅' if ok else '  '} [{label}] {host.split('//')[1][:26]:26s} -> {r.status_code} {r.text[:60]}")
            if ok:
                return True
        except Exception as e:
            print(f"    ✗ {host}: {str(e)[:55]}")
    return False


def main():
    rows = [json.loads(l) for l in open(CK_FILE, encoding="utf-8") if l.strip()]
    ck = rows[IDX]
    priv, pub = load_key()
    print(f"设备密钥: {PERM_FILE}  ({'复用' if os.path.exists(PERM_FILE) else '新建'})")

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), CB)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cb = f"http://127.0.0.1:{port}/authorize"

    requests.post(f"{WW}/api/login/cancel", timeout=10)
    time.sleep(1.5)
    r = requests.post(f"{WW}/api/login/start", json={"channel": "traework"}, timeout=25).json()
    st = json.load(open(STATE))
    verifier, machine_id, device_id = st.get("codeVerifier"), st.get("machineId"), st.get("deviceId")

    u = urlparse(r["auth_url"])
    q = dict(parse_qsl(u.query))
    q["client_id"] = CLIENT_IDE
    q["auth_callback_url"] = cb
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
        c = None
        for attempt in range(4):
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"  试{attempt+1} goto: {str(e)[:60]}")
            for _ in range(14):
                pg.wait_for_timeout(1000)
                if CAUGHT: break
                c = pg.evaluate("""() => {let b=null;
                    for (const el of document.querySelectorAll('button,a,div,span')) {
                        const t=(el.innerText||'').trim();
                        if(!t.startsWith('Log in and open')||t.length>40) continue;
                        const r=el.getBoundingClientRect(); if(r.width<=0) continue;
                        const a=r.width*r.height; if(!b||a<b.a) b={x:r.x+r.width/2,y:r.y+r.height/2,a};}
                    return b;}""")
                if c or CAUGHT: break
            if c or CAUGHT: break
            print(f"  试{attempt+1}: 无按钮")
            pg.wait_for_timeout(2500)
        if not c and not CAUGHT:
            print("❌ 拿不到按钮"); b.close(); srv.shutdown(); return
        if c:
            pg.mouse.click(c["x"], c["y"])
            for _ in range(25):
                pg.wait_for_timeout(1000)
                if CAUGHT: break
        b.close()
    srv.shutdown()

    if not CAUGHT:
        print("❌ 无回调"); return
    qs = dict(parse_qsl(urlparse(CAUGHT[0]).query))
    info = json.loads(qs["authCodeInfo"])
    auth_code, host = info["AuthCode"], qs.get("host")
    print(f"AuthCode = {auth_code[:28]}…   host = {host}\n")

    devinfo = {"DeviceID": device_id, "MachineID": machine_id, "PlatformCode": "IDE_PC",
               "DeviceType": "PC", "DeviceName": "DESKTOP-TRAE", "DeviceModel": "MacBookPro18,3",
               "ClientVersion": "0.1.63", "DevicePublicKey": pub,
               "DeviceBrand": "Apple", "DeviceCPU": "Apple M1 Pro",
               "OSInfo": "macOS", "OSVersion": "macOS 15.7.4"}

    # ① AuthCode 交换（无签名）—— 只用 IDE clientID + V2 路径
    r1 = requests.post(host + EP_V2, timeout=40,
                       headers={"Content-Type": "application/json", "x-cloudide-token": ""},
                       json={"ClientID": CLIENT_IDE, "AuthCode": auth_code,
                             "CodeVerifier": verifier, "DeviceInfo": devinfo,
                             "IDEVersion": "0.1.63"})
    j1 = r1.json()
    res1 = j1.get("Result") or {}
    print(f"① AuthCode 交换 -> {r1.status_code}  token={bool(res1.get('Token'))} refresh={bool(res1.get('RefreshToken'))}")
    if not res1.get("Token"):
        print("   ", json.dumps(j1, ensure_ascii=False)[:300]); return
    print(f"   BoundDeviceID = {res1.get('BoundDeviceID')}   DeviceBindStatus = {res1.get('DeviceBindStatus')}")

    # ② 带 DeviceProof 的 refresh —— 真 IDE 在 AuthCode 之后必走这一步
    for label, proof in (("带 DeviceProof", device_proof(priv, "POST", EP_V2, CLIENT_IDE, res1["RefreshToken"])),
                         ("不带 DeviceProof", None)):
        body = {"ClientID": CLIENT_IDE, "ClientSecret": "", "RefreshToken": res1["RefreshToken"],
                "DeviceInfo": devinfo, "IDEVersion": "0.1.63"}
        if proof:
            body["DeviceProof"] = proof
        r2 = requests.post(host + EP_V2, json=body, timeout=40,
                           headers={"Content-Type": "application/json", "x-cloudide-token": ""})
        j2 = r2.json()
        res2 = j2.get("Result") or {}
        err = (j2.get("ResponseMetadata") or {}).get("Error") or {}
        tok = res2.get("Token") or res1["Token"]
        print(f"② refresh [{label}] -> {r2.status_code}  "
              f"{'token ok' if res2.get('Token') else err.get('Message','')[:50]}")

        print(f"\n=== 拿 token 打 agent API [{label}] ===")
        if test_agent(tok, ck["uid"], device_id, machine_id, label):
            print("\n🎯🎯🎯🎯 agent API 通了！！！")
            print(f"   DeviceProof {'是' if proof else '否'} 关键")
            json.dump({"token": tok, "refresh": res2.get("RefreshToken") or res1["RefreshToken"],
                       "clientID": CLIENT_IDE, "withProof": bool(proof)},
                      open("/Users/lan/dsh/trae-recon/ide/working-cred.json", "w"), indent=2)
            return
        print()


if __name__ == "__main__":
    main()
