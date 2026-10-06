# -*- coding: utf-8 -*-
"""一次跑完：网页登录 → 授权 → **立刻**用新鲜 AuthCode 打交换矩阵。

教训：AuthCode 是一次性的，第一次尝试（哪怕失败）就消费掉了。
所以必须拿到码后立刻测，且把变体排好序。
"""
import glob, json, os, sys, time, base64, secrets, threading, http.server, socketserver
from urllib.parse import urlparse, urlencode, urlunparse, parse_qsl
import requests
from playwright.sync_api import sync_playwright
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization

EMAIL = sys.argv[1] if len(sys.argv) > 1 else "2tpskvgzlf@ruutukf.com"
PW    = sys.argv[2] if len(sys.argv) > 2 else "Trae6eqkfrwf!9"
PROXY = sys.argv[3] if len(sys.argv) > 3 else "socks5://127.0.0.1:39042"
WW = "http://127.0.0.1:7866"
CLIENT_IDE = "ono9krqynydwx5"
EP = "/trae/api/v3/oauth/ExchangeToken"
CAUGHT = []

class CB(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def do_GET(self):
        CAUGHT.append(self.path)
        p = "<html><body style='font:18px sans-serif;padding:48px;background:#111;color:#eee'><h2>✅ 授权完成</h2></body></html>".encode()
        self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(p))); self.end_headers(); self.wfile.write(p)
    def log_message(self, *a): pass

srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), CB); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
cb = f"http://127.0.0.1:{srv.server_address[1]}/authorize"

requests.post(f"{WW}/api/login/cancel", timeout=10); time.sleep(1.2)
r = requests.post(f"{WW}/api/login/start", json={"channel":"traework"}, timeout=25).json()
ST = json.load(open("/Users/lan/dsh/wild-work/data/login-state.json"))
VER, MACH, DEV = ST["codeVerifier"], ST["machineId"], ST["deviceId"]
u = urlparse(r["auth_url"]); q = dict(parse_qsl(u.query))
q.update({"client_id":CLIENT_IDE,"auth_from":"trae","login_channel":"ai_extension","auth_callback_url":cb})
authz = urlunparse(u._replace(query=urlencode(q)))
print(f"\n授权链接:\n{authz}\n")

priv = serialization.load_pem_private_key(json.load(open("ide/device-key.json"))["private"].encode(), password=None)
PUB = priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

CH = glob.glob(os.path.expanduser("~/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"))
kw = {"headless": False, "args":["--disable-blink-features=AutomationControlled","--window-size=1280,940"]}
if CH: kw["executable_path"] = CH[0]
with sync_playwright() as p:
    b = p.chromium.launch(**kw)
    ckw = {"locale":"zh-CN","viewport":{"width":1280,"height":900}}
    if PROXY: ckw["proxy"] = {"server":PROXY,"bypass":"127.0.0.1,localhost"}
    ctx = b.new_context(**ckw); pg = ctx.new_page()
    pg.goto("https://www.trae.ai/login", wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_timeout(7000)
    try:
        pg.fill("input[type=email]", EMAIL); pg.wait_for_timeout(500)
        pg.fill("input[type=password]", PW); pg.wait_for_timeout(500)
        box = pg.evaluate("""() => {let b=null;
            for (const el of document.querySelectorAll('div,span,a,button,[role=button]')) {
                const t=(el.textContent||'').trim(); if(t!=='Log in'&&t!=='登录') continue;
                const r=el.getBoundingClientRect(); if(r.width<5) continue;
                const a=r.width*r.height; if(!b||a<b.a) b={x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};}
            return b;}""")
        if box:
            pg.mouse.move(box["x"],box["y"]); pg.wait_for_timeout(150)
            pg.mouse.down(); pg.wait_for_timeout(80); pg.mouse.up()
    except Exception as e: print("表单:", str(e)[:80])
    pg.wait_for_timeout(14000)
    pg.goto(authz, wait_until="domcontentloaded", timeout=60000)
    print("="*70); print("  窗口已开。请在里面登录并点授权。我等回调。"); print("="*70+"\n")
    last=""
    for i in range(700):
        pg.wait_for_timeout(1000)
        if CAUGHT: print(f"\n🎯 回调到了 ({i}s)"); break
        if i%10==0:
            try: t = pg.evaluate("()=>document.body.innerText.replace(/\\s+/g,' ').trim().slice(0,100)")
            except Exception: t="<跳走>"
            if t!=last: print(f"  [{i:3d}s] {pg.url[:90]}\n          {t}"); last=t
    b.close()
srv.shutdown()
if not CAUGHT: print("❌ 无回调"); sys.exit(1)

qs = dict(parse_qsl(urlparse(CAUGHT[0]).query))
info = json.loads(qs["authCodeInfo"]); CODE = info["AuthCode"]; HOST = qs.get("host")
print(f"\nAuthCode={CODE}\nhost={HOST}  userRegion={qs.get('userRegion')}\n")
print("立刻打交换矩阵（AuthCode 一次性，必须快）:")

def proof(rt):
    ts=int(time.time()); n=secrets.token_hex(16)
    msg="\n".join(["POST",EP,CLIENT_IDE,rt,str(ts),n])
    return {"Signature":base64.b64encode(priv.sign(msg.encode(), ec.ECDSA(hashes.SHA256()))).decode(),
            "Timestamp":ts,"Nonce":n}

DI = {"DeviceID":DEV,"MachineID":MACH,"PlatformCode":"IDE_PC","DeviceType":"PC",
      "DeviceName":"DESKTOP-TRAE","DeviceModel":"MacBookPro18,3","ClientVersion":"3.5.104",
      "DevicePublicKey":PUB,"DeviceBrand":"Apple","DeviceCPU":"Apple M1 Pro",
      "OSInfo":"macOS","OSVersion":"macOS 15.7.4"}

VARIANTS = [
  ("裸（无 proof）",            {}),
  ("DeviceProof 空 refresh",    {"DeviceProof": proof("")}),
  ("DeviceProof + ClientSecret",{"DeviceProof": proof(""), "ClientSecret":"-"}),
]
for label, extra in VARIANTS:
    body = {"ClientID":CLIENT_IDE,"AuthCode":CODE,"CodeVerifier":VER,"DeviceInfo":DI,
            "IDEVersion":"3.5.104", **extra}
    try:
        rr = requests.post(HOST+EP, json=body, timeout=30,
                           headers={"Content-Type":"application/json","x-cloudide-token":""})
        j = rr.json(); res = j.get("Result") or {}
        err = (j.get("ResponseMetadata") or {}).get("Error") or {}
        print(f"  {rr.status_code}  {label:28s} {'✅ token!' if res.get('Token') else err.get('Code','')+' '+err.get('Message','')[:40]}")
        if res.get("Token"):
            json.dump({"host":HOST,"token":res["Token"],"refresh":res.get("RefreshToken"),
                       "email":EMAIL,"code":CODE}, open("/tmp/tj/sg-token.json","w"), indent=2)
            print("\n★ 拿到了！-> /tmp/tj/sg-token.json")
            break
    except Exception as e:
        print(f"  ✗ {label}: {str(e)[:60]}")
