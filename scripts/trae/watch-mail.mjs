#!/usr/bin/env node
// watch-mail.mjs — 盯着一个 mail.tm 邮箱，有新邮件就把 6 位验证码打出来
// 用法: node watch-mail.mjs <local-part> [分钟数]
// 密码规则与 trae-batch.mjs 一致：'T!' + local + 'pw9'
const LOCAL = process.argv[2];
const MIN   = Number(process.argv[3] || 5);
if (!LOCAL) { console.error('用法: node watch-mail.mjs <local-part> [分钟]'); process.exit(1); }

const DOMAIN = 'maxxspace.com';
const addr = `${LOCAL}@${DOMAIN}`;
const pw   = 'T!' + LOCAL + 'pw9';
const j = async (u, o = {}) => { const r = await fetch(u, o); let x = null; try { x = await r.json(); } catch {} return x; };

const t = await j('https://api.mail.tm/token', { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ address: addr, password: pw }) });
if (!t?.token) { console.error('❌ 邮箱登录失败:', JSON.stringify(t).slice(0, 200)); process.exit(1); }
console.log(`📬 盯着 ${addr}（最多 ${MIN} 分钟）… 有新邮件就打印验证码\n`);

const clean = s => s
  .replace(/<style[\s\S]*?<\/style>/gi, ' ').replace(/<head[\s\S]*?<\/head>/gi, ' ')
  .replace(/<!--[\s\S]*?-->/g, ' ').replace(/<[^>]*>/g, ' ').replace(/&#\d+;|&\w+;/g, ' ');

const seen = new Set();
const deadline = Date.now() + MIN * 60_000;
while (Date.now() < deadline) {
  const msgs = await j('https://api.mail.tm/messages', { headers: { Authorization: 'Bearer ' + t.token } });
  for (const m of (msgs?.['hydra:member'] || [])) {
    if (seen.has(m.id)) continue;
    seen.add(m.id);
    const d = await j(`https://api.mail.tm/messages/${m.id}`, { headers: { Authorization: 'Bearer ' + t.token } });
    const cand = (d?.text || '') + '\n' + clean((d?.html || []).join('\n'));
    const codes = [...cand.matchAll(/(?<![#\w])(\d{6})(?![\w])/g)].map(x => x[1]);
    console.log(`\n📨 ${new Date(m.createdAt).toLocaleTimeString()}  «${m.subject}»`);
    console.log(codes.length ? `   ★ 验证码: ${codes.join('  ')}` : `   (正文里没有 6 位数字)`);
  }
  await new Promise(r => setTimeout(r, 3000));
}
console.log('\n(盯梢结束)');
