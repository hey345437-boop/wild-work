#!/usr/bin/env node
// trae-auto-authorize.mjs — 半自动授权工作流
//
//   你只做一件事：在弹出来的窗口里输入邮箱密码点「登入」。
//   其余全自动：等授权页 → 点授权 → 收回调 → 换 token → 落盘给 wild-work。
//
// 为什么登录不能全自动：登录请求 `POST /passport/web/email/login/` 的 query 里带着
//   X-Bogus / X-Gnarly / sign 三个字节跳动的反爬签名，且即便请求返回 200
//   （cookie 里也确实写入了 sessionid），前端 SPA 仍不提交登录态 —— 说明还有
//   一层前端侧的指纹/风控校验。复刻这套签名是独立的逆向工程，不在本工具范围内。
//   **授权那一段（点按钮 → 收回调 → 换 token）是全自动的**，已实测跑通。
//
// 用法:
//   node trae-auto-authorize.mjs --email a@b.com [--password xxx] [--proxy socks5://127.0.0.1:39042]
//   node trae-auto-authorize.mjs --email a@b.com --auto-check     # 不弹窗，纯脚本判定（调试用）
import { chromium } from 'playwright';
import crypto from 'node:crypto';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';

const argv = process.argv.slice(2);
const arg = (k, d) => { const i = argv.indexOf(k); return i >= 0 ? argv[i + 1] : d; };
const EMAIL = arg('--email', '');
const PASSWORD = arg('--password', '');
const PROXY = arg('--proxy', 'socks5://127.0.0.1:39042');
const AUTH_DIR = '/Users/lan/dsh/wild-work/auths';
if (!EMAIL) { console.error('用法: node trae-auto-authorize.mjs --email a@b.com [--password xxx]'); process.exit(1); }

const CLIENT_IDE = 'ono9krqynydwx5';
const IDE_VERSION = '3.5.104';
const APP_VERSION = '3.5.20';
const PLUGIN_VERSION = '2.3.73734';
const AGENT_BY_REGION = { sg: 'https://coresg-normal.trae.ai', us: 'https://coreva-normal.trae.ai' };
const FULL_CHROME = '/Users/lan/Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';

const sleep = ms => new Promise(r => setTimeout(r, ms));
const safeEval = async (pg, fn, def = null) => {
  for (let i = 0; i < 3; i++) {
    try { return await pg.evaluate(fn); }
    catch (e) { if (!/context was destroyed|Execution context/i.test(String(e))) throw e; await sleep(1200); }
  }
  return def;
};

// ── 本地回调 + PKCE + 授权 URL ────────────────────────────────────
let caught = null;
const srv = http.createServer((req, res) => {
  caught = req.url;
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end('<html><body style="font:18px -apple-system,sans-serif;padding:56px;background:#111;color:#eee">'
    + '<h2>✅ 授权完成</h2><p>可以关掉这个标签页了。</p></body></html>');
});
await new Promise(r => srv.listen(0, '127.0.0.1', r));
const CB = `http://127.0.0.1:${srv.address().port}/authorize`;

const verifier = crypto.randomBytes(48).toString('base64url');
const challenge = crypto.createHash('sha256').update(verifier).digest('base64url');
const deviceId = (() => { let s = String(1 + crypto.randomInt(9)); for (let i = 1; i < 16; i++) s += crypto.randomInt(10); return s; })();
const machineId = crypto.randomBytes(32).toString('hex');

const AUTHZ = 'https://www.trae.ai/authorization?' + new URLSearchParams({
  login_version: '1', auth_from: 'trae', login_channel: 'ai_extension',
  plugin_version: PLUGIN_VERSION, auth_type: 'local', client_id: CLIENT_IDE,
  redirect: '0', login_trace_id: crypto.randomUUID(), auth_callback_url: CB,
  machine_id: machineId, device_id: deviceId, x_device_id: deviceId, x_machine_id: machineId,
  x_device_brand: '20Y5A002XX', x_device_type: 'windows', x_os_version: 'Windows 10 Pro',
  x_env: '', x_app_version: APP_VERSION, x_app_type: 'stable',
  code_challenge: challenge, code_challenge_method: 'S256',
  hide_saas_login: 'true', channel_name: 'common',
  click_id: 'TRAE SOLOSetup-stable-' + PLUGIN_VERSION,
}).toString();

console.log('='.repeat(72));
console.log(`邮箱     : ${EMAIL}`);
console.log(`代理     : ${PROXY}`);
console.log(`本地回调 : ${CB}`);
console.log('='.repeat(72));
console.log('\n授权链接（备用）：\n' + AUTHZ + '\n');

const exe = fs.existsSync(FULL_CHROME) ? FULL_CHROME : undefined;
const browser = await chromium.launch({
  headless: false, ...(exe ? { executablePath: exe } : {}),
  args: ['--disable-blink-features=AutomationControlled', '--window-size=1280,940'],
  chromiumSandbox: false,
});
const ctx = await browser.newContext({
  locale: 'zh-CN', viewport: { width: 1280, height: 900 },
  ...(PROXY ? { proxy: { server: PROXY, bypass: '127.0.0.1,localhost' } } : {}),
});
const pg = await ctx.newPage();

// 先开授权页 —— 未登录时它会自己跳到 /login?redirect_url=<授权页>，
// 于是登录完会**自动回**授权页，省掉我们自己跳转。
console.log('① 打开授权页 …');
await pg.goto(AUTHZ, { waitUntil: 'domcontentloaded', timeout: 60000 });
await pg.waitForTimeout(7000);
console.log(`   落在: ${pg.url().slice(0, 96)}`);

// 预填邮箱密码，省你打字（登录按钮仍需你点 —— 这就是那道反爬关卡）
if (PASSWORD) {
  try {
    await pg.fill('input[type=email]', EMAIL, { timeout: 12000 });
    await pg.waitForTimeout(400);
    await pg.fill('input[type=password]', PASSWORD, { timeout: 12000 });
    await pg.waitForTimeout(400);
    console.log('   ✔ 已预填邮箱密码，请直接点「Log in」');
  } catch { console.log('   （没找到登录表单，可能已是登录态，继续）'); }
}

console.log('\n' + '='.repeat(72));
console.log('  👉 请在窗口里点「Log in」完成登录');
console.log('     登录后我会自动点授权、收回调、换 token、落盘 —— 你不用管了');
console.log('='.repeat(72) + '\n');

// ── 轮询：等授权页出现「Log in and open TRAE」 ────────────────────
let clicked = 0;
for (let i = 0; i < 600 && !caught; i++) {
  await sleep(1000);
  const btn = await safeEval(pg, `(()=>{let x=null;
    for (const el of document.querySelectorAll('button,a,div,span')) {
      const t=(el.innerText||'').trim();
      if (!t.startsWith('Log in and open') || t.length > 40) continue;
      const r=el.getBoundingClientRect(); if (r.width<=0) continue;
      const a=r.width*r.height; if (!x||a<x.a) x={x:r.x+r.width/2, y:r.y+r.height/2};
    } return x;})()`);
  if (btn) {
    clicked++;
    console.log(`  [${i}s] 检测到授权页 → 点「Log in and open TRAE」`);
    await pg.mouse.click(btn.x, btn.y);
    for (let k = 0; k < 30 && !caught; k++) await sleep(1000);
    if (caught) break;
    if (clicked >= 3) break;
  }
  if (i > 0 && i % 20 === 0) {
    const t = await safeEval(pg, `()=>document.body?document.body.innerText.replace(/\\s+/g,' ').trim().slice(0,96):'<空>'`, '?');
    console.log(`  [${i}s] 等待中… ${t}`);
  }
}

let rec = { email: EMAIL, at: new Date().toISOString(), proxy: PROXY, deviceId, machineId };
try {
  if (!caught) throw new Error('10 分钟内没等到授权回调');
  const q = new URL('http://x' + caught).searchParams;
  const info = JSON.parse(q.get('authCodeInfo') || '{}');
  rec.authCode = info.AuthCode;
  rec.host = q.get('host');
  rec.userRegion = (q.get('userRegion') || '').toLowerCase();
  console.log(`\n② AuthCode ${rec.authCode}`);
  console.log(`   host ${rec.host}   userRegion ${rec.userRegion}`);
  if (!rec.authCode) throw new Error('回调里没有 AuthCode');

  console.log('③ 换 token …');
  const devinfo = {
    DeviceID: deviceId, MachineID: machineId, PlatformCode: 'IDE_PC', DeviceType: 'PC',
    DeviceName: 'DESKTOP-TRAE', DeviceModel: 'MacBookPro18,3', ClientVersion: IDE_VERSION,
    DeviceBrand: 'Apple', DeviceCPU: 'Apple M1 Pro', OSInfo: 'macOS', OSVersion: 'macOS 15.7.4',
  };
  const er = await fetch(rec.host + '/trae/api/v3/oauth/ExchangeToken', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'x-cloudide-token': '' },
    body: JSON.stringify({ ClientID: CLIENT_IDE, AuthCode: rec.authCode, CodeVerifier: verifier,
      DeviceInfo: devinfo, IDEVersion: IDE_VERSION }),
  });
  const ej = await er.json();
  const res = ej.Result || {};
  if (!res.Token) throw new Error('ExchangeToken 失败: ' + JSON.stringify((ej.ResponseMetadata || {}).Error || ej).slice(0, 180));

  rec.token = res.Token; rec.refreshToken = res.RefreshToken;
  const payload = JSON.parse(Buffer.from(res.Token.split('.')[1], 'base64url').toString());
  rec.uid = payload?.data?.id; rec.expiresAt = payload?.exp;
  console.log(`   ✅ uid=${rec.uid}  到期 ${new Date(rec.expiresAt * 1000).toISOString().slice(0, 10)}`);

  const doc = {
    auth: { accessToken: rec.token, refreshToken: rec.refreshToken, expiresAt: rec.expiresAt,
      domain: 'trae.ai', apiHost: rec.host, machineId, deviceId,
      traeRegion: rec.userRegion },
    account: { uid: rec.uid, enterpriseId: '', nickname: 'auto-' + String(rec.uid).slice(-6) },
  };
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  const fp = path.join(AUTH_DIR, `trae-${rec.uid}.json`);
  fs.writeFileSync(fp, JSON.stringify(doc, null, 2), { mode: 0o600 });
  rec.authFile = fp; rec.ok = true;
  console.log(`④ 已写入 ${fp}`);
  console.log(`   agent 域名将是 ${AGENT_BY_REGION[rec.userRegion] || 'https://core-normal.trae.ai'}`);
} catch (e) {
  rec.error = String(e.message || e);
  console.log('\n✗ ' + rec.error);
  await pg.screenshot({ path: '/tmp/trae-auto-authorize.png' }).catch(() => {});
} finally {
  await browser.close().catch(() => {});
  srv.close();
}

console.log('\n' + '='.repeat(72));
console.log(rec.ok ? `✅ 成功  uid=${rec.uid}  region=${rec.userRegion}` : `❌ 失败  ${rec.error}`);
console.log('='.repeat(72));
if (rec.ok) console.log('\n跑 `bash /Users/lan/dsh/wb-stack.sh restart` 让 wild-work 加载新账号');
fs.appendFileSync('/Users/lan/dsh/trae-recon/accounts/trae-accounts.jsonl', JSON.stringify(rec) + '\n');
process.exit(rec.ok ? 0 : 1);
