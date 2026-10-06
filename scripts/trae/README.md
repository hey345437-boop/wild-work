# Trae 渠道工具

配合 `internal/traework/` 的接入改动使用。完整逆向记录见
[`docs/trae-渠道接入备忘.md`](../../docs/trae-渠道接入备忘.md)。

## 为什么需要这些脚本

Trae 的**登录只能在网页版做**（IDE 的邮箱登录有域名风控，网页版没有）。
流程是：

```
注册账号 → 网页登录 → 网页授权页点「Log in and open TRAE」
        → 本地回调收 AuthCode → 换 token → 落盘到 wild-work/auths/
```

**授权那一段全自动；只有「点 Log in」必须人工** —— 登录请求带 `X-Bogus`/`X-Gnarly`
反爬签名，复刻它是独立的逆向工程。

## 环境

```bash
# playwright 只装在 wb-manager 的 venv 里（Python 脚本用）
# Node 脚本需要能 resolve 到 playwright
ln -s /Users/lan/dsh/dola-exp/node_modules /Users/lan/dsh/trae-recon/node_modules
```

## 注册机

`trae-batch.mjs` —— 用 temp-mail.io 一次性邮箱自动注册。

```bash
node trae-batch.mjs <个数> [代理]
node trae-batch.mjs 1 socks5://127.0.0.1:39042     # SG 出口
```

**★ 代理决定账号区域。** 实测直连 → US-East（打不了 agent API），
SG 出口 → `store-country-code=sg` / `trae-target-idc=alisg`（可用）。

产物写入 `accounts/trae-accounts.jsonl`。

## 授权工作流

`trae-auto-authorize.mjs` —— 半自动。开可见窗口、预填邮箱密码，
**你点一次「Log in」，其余全自动**：等授权页 → 点授权 → 收 AuthCode →
换 token → 写进 `wild-work/auths/`。

```bash
node trae-auto-authorize.mjs --email <邮箱> --password <密码> --proxy socks5://127.0.0.1:39042
```

`watch-mail.mjs` —— 盯 temp-mail.io 邮箱，有新信就把 6 位验证码打出来。

```bash
node watch-mail.mjs <邮箱local-part> [分钟数]
```

## 探针（Python）

都在 `probe/`，用 `wb-manager/.venv/bin/python` 跑。

| 脚本 | 用途 |
|---|---|
| `authz_matrix.py` | `auth_from` 六值矩阵 —— 证明区域墙是路由级常量 |
| `authz_e2e.py` | 端到端：驱动 wild-work 自己的回调落盘账号 |
| `sg_authz_full.py` | 一条龙：登录 → 授权 → 立刻换 token |
| `device_proof.py` | DeviceProof 签名实现（ECDSA P-256，已被服务端验证通过） |
| `model_fingerprint.py` | ★ 从 SSE 的 `extra_info` 帧挖模型**后端真名** |
| `web_login_authorize.py` | 可视浏览器：登录 + 授权窗口 |
| `exchange_probe.py` | AuthCode 交换端点矩阵 |
| `token_matrix.py` | token × 头 × scheme 矩阵 |
| `ck_inject.py` | CK 注入隔离浏览器 |

```bash
wb-manager/.venv/bin/python probe/model_fingerprint.py gpt-5.4 kimi-k3
```

## 关键参数（改之前先读）

| 项 | 值 | 说明 |
|---|---|---|
| agent 域名 | 按区域：`coresg-` / `coreva-` / `core-normal.trae.ai` | **不是** `trae-api-*.mchost.guru` |
| 认证头 | `x-ide-token` | 不是 `Authorization: Cloud-IDE-JWT` |
| IDE clientID | `ono9krqynydwx5` | SOLO 是 `en1oxy7wnw8j9n`，**换 AuthCode 时不能混** |
| 授权页 | `auth_from=trae` + `login_channel=ai_extension` | `solo` 路由硬编码地区墙 |
| `x_app_version` | `3.5.20` | 低于远程配置 `ide_required_version` 会卡在「版本过低」 |
