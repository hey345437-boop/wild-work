#!/usr/bin/env node
// trae-batch.mjs — 用 mail.tm 邮箱批量注册 Trae 账号（页内驱动）
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const N       = Number(process.argv[2] || 5);
// 第 3 个参数：代理。注册时的出口 IP 决定账号落在哪个区（实测直连 = US-East），
// 而 US-East 的号打不了 agent API。用 SG/JP 出口注册才能拿到可用区域的号。
// 例：node trae-batch.mjs 1 socks5://127.0.0.1:39042
const PROXY   = process.argv[3] || '';
const OUTDIR  = '/Users/lan/dsh/trae-recon/accounts';
const OUT     = path.join(OUTDIR, 'trae-accounts.jsonl');
fs.mkdirSync(OUTDIR, { recursive: true });

const sleep = ms => new Promise(r => setTimeout(r, ms));
const jfetch = async (url, opts = {}) => {
  const r = await fetch(url, opts);
  let j = null; try { j = await r.json(); } catch {}
  return { status: r.status, json: j, text: async () => r.text() };
};

// ── 临时邮箱：temp-mail.io ────────────────────────────────────────
// 2026-10-06 换掉 mail.tm：它只剩 maxxspace.com 一个域名，而该域名已被 Trae
// 风控标记（登录时报「域名邮箱存在风险」）。temp-mail.io 有 7 个域名，实测可正常收码。
const TM_DOMAINS = ['ruutukf.com', 'yzcalo.com', 'lnovic.com', 'gmeenramy.com', 'olipii.com', 'ooynib.com', 'tanpony.com'];
const tmApi = 'https://api.internal.temp-mail.io/api/v3';

async function newMailbox() {
  for (let i = 0; i < 8; i++) {
    const dom = TM_DOMAINS[i % TM_DOMAINS.length];
    const a = await jfetch(`${tmApi}/email/new`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ min_name_length: 10, max_name_length: 10, domain: dom }),
    });
    if (a.json?.email) return { email: a.json.email, token: a.json.token, provider: 'temp-mail.io' };
    await sleep(1500);
  }
  throw new Error('temp-mail.io 建箱失败');
}

async function fetchCode(box, sinceMs) {
  // ⚠ 这里**不能** encodeURIComponent：把 @ 转成 %40 后该 API 回
  //  400 {"code":101,"message":"Email not found"}，导致永远收不到码。
  const r = await jfetch(`${tmApi}/email/${box.email}/messages`, {
    headers: { 'X-Token': box.token || '' },
  });
  for (const m of (Array.isArray(r.json) ? r.json : [])) {
    // 不再按时间过滤：邮箱是本次刚建的，里面只可能有这一封新邮件。
    // （原来用 created_at >= sinceMs-10s 过滤，实测把刚到的邮件滤掉了 ——
    //   邮件服务端盖的时间戳可能早于本地点「发送验证码」的时刻。）
    if (box.seen?.has(m.id)) continue;
    (box.seen ||= new Set()).add(m.id);
    const text = m.body_text || m.text || '';
    const html = m.body_html || m.html || '';
    // ★ 邮件模板把验证码放在一个带 letter-spacing 的 <span> 里，
    //   而 <style> 块里有 color:#111314 —— 直接 match(/\d{6}/) 会先命中 CSS 颜色，
    //   拿到一个恒定的假码（实测连续两次都是 111314）。所以先剥 <style>/<head> 再找。
    const clean = s => s
      .replace(/<style[\s\S]*?<\/style>/gi, ' ')
      .replace(/<head[\s\S]*?<\/head>/gi, ' ')
      .replace(/<!--[\s\S]*?-->/g, ' ')
      .replace(/<[^>]*>/g, ' ')
      .replace(/&#\d+;|&\w+;/g, ' ');
    const cand = [text, clean(html)].join('\n');
    for (const mm of cand.matchAll(/(?<![#\w])(\d{6})(?![\w])/g)) {
      return { code: mm[1], subject: m.subject, body: cand.replace(/\s+/g, ' ').slice(0, 300) };
    }
  }
  return null;
}

// ── 一个号的注册流程 ─────────────────────────────────────────────
async function registerOne(browser, idx) {
  const box = await newMailbox();
  const pass = 'Trae' + Math.random().toString(36).slice(2, 10) + '!9';
  const rec = { idx, email: box.email, password: pass, at: new Date().toISOString() };
  console.log(`\n═══ [${idx}/${N}] ${box.email} ═══`);

  const ctx = await browser.newContext({
    locale: 'en-US', viewport: { width: 1440, height: 900 },
    // bypass 让 127.0.0.1 的回调/本地请求不走代理
    ...(PROXY ? { proxy: { server: PROXY, bypass: '127.0.0.1,localhost' } } : {}),
  });
  const page = await ctx.newPage();
  const reqs = [];
  page.on('response', async r => { if (/passport/.test(r.url())) { try { const t = await r.text();
    reqs.push({ url: r.url(), status: r.status(), bd: r.headers()['bd-tt-error-code'], body: t.slice(0, 300) }); } catch {} } });

  try {
    await page.goto('https://www.trae.ai/sign-up', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(8000);
    await page.fill('input[type=email]', box.email);
    await page.waitForTimeout(2000);
    await page.locator('text="Send Code"').first().click({ timeout: 15000 });
    console.log('  → 已发码');
    const t0 = Date.now();

    let got = null;
    for (let i = 0; i < 60 && !got; i++) { await sleep(3000); got = await fetchCode(box, t0).catch(() => null); if (i % 5 === 4) process.stdout.write('.'); }
    console.log();
    if (!got) { rec.fail = 'no_code'; console.log('  ✗ 没收到验证码'); await ctx.close(); return rec; }
    rec.code = got.code; rec.subject = got.subject; rec.mailBody = got.body;
    console.log(`  → 验证码 ${got.code}  «${got.subject}»`);
    console.log(`     邮件正文: ${JSON.stringify(got.body).slice(0, 260)}`);

    await page.fill('input[placeholder*="Verification"], input[placeholder*="code" i]', got.code);
    await page.waitForTimeout(1200);
    await page.fill('input[type=password]', pass);
    await page.waitForTimeout(2000);
    const clicked = await page.evaluate(`(() => {
      const els = [...document.querySelectorAll('div,span,a,button,[role=button]')];
      for (const w of ['Sign Up','Sign up','Create Account','Register']) {
        const hits = els.filter(x => (x.textContent||'').trim() === w);
        if (!hits.length) continue;
        for (const el of hits.reverse()) {
          const r = el.getBoundingClientRect();
          if (r.width < 5 || r.height < 5) continue;
          (el.closest('button,[role=button]') || el).click();
          return w;
        }
      }
      return 'NOT_FOUND';
    })()`);
    console.log('  → 提交:', clicked);
    await page.waitForTimeout(18000);
    rec.finalUrl = page.url();
    const reg = reqs.filter(r => /register_verify_login/.test(r.url));
    rec.registerResp = reg.map(r => ({ status: r.status, bd: r.bd, body: r.body })).slice(-2);
    const ok = reg.some(r => r.bd === '0');
    if (ok) {
      const cks = await ctx.cookies();
      rec.cookies = Object.fromEntries(cks.map(c => [c.name, c.value]));
      rec.ok = true;
      console.log('  ✅ 注册成功');
    } else {
      rec.fail = reg.length ? ('bd=' + reg.at(-1).bd) : 'no_register_req';
      console.log('  ✗ 注册失败:', rec.fail, JSON.stringify(reg.at(-1)?.body || '').slice(0, 150));
    }
    await page.screenshot({ path: path.join(OUTDIR, `trae-${idx}.png`) }).catch(() => {});
  } catch (e) {
    rec.fail = 'exception'; rec.error = String(e.message || e);
    console.log('  ✗ 异常:', rec.error);
  } finally {
    await ctx.close().catch(() => {});
  }
  return rec;
}

// ── main ─────────────────────────────────────────────────────────
// node 侧 playwright 期望的 headless shell 版本号（-1243）本机没装，
// 复用已有的 -1234，避免再下一份 150MB。
const CH_CANDIDATES = [
  '/Users/lan/Library/Caches/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-mac-arm64/chrome-headless-shell',
  '/Users/lan/Library/Caches/ms-playwright/chromium_headless_shell-1200/chrome-headless-shell-mac-arm64/chrome-headless-shell',
];
const CH = CH_CANDIDATES.find(p => fs.existsSync(p));
if (CH) console.log('chromium:', CH.split('/ms-playwright/')[1].split('/')[0]);

const browser = await chromium.launch({
  headless: true,
  ...(CH ? { executablePath: CH } : {}),
  args: ['--disable-blink-features=AutomationControlled'],
  chromiumSandbox: false,
});
const results = [];
for (let i = 1; i <= N; i++) {
  const r = await registerOne(browser, i);
  results.push(r);
  fs.appendFileSync(OUT, JSON.stringify(r) + '\n');
  if (i < N) { console.log('  等 20 秒再下一个…'); await sleep(20000); }
}
await browser.close();
const okN = results.filter(r => r.ok).length;
console.log(`\n${'═'.repeat(60)}\n结果：${okN}/${N} 成功`);
for (const r of results) console.log(`  ${r.ok ? '✅' : '✗ '} ${r.email.padEnd(38)} ${r.ok ? 'ok' : (r.fail || '?')}`);
console.log('═'.repeat(60));
