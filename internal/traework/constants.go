// Package traework 封装 Trae SOLO 免费通道上游协议。
package traework

import (
	"strings"
	"time"
)

const (
	AgentHost      = "https://trae-api-sg.mchost.guru"   // 国际版（原 trae-api-cn）
	// ★ agent 域名按区域分服，不是所有区共用一个 mchost.guru。
	// 来源：Trae IDE 的 product.json → bootConfig.agent.trae.*，并用真机日志核对过
	// （SG 账号实际打的就是 coresg-normal.trae.ai）。
	// 美区账号打 mchost.guru 上的任何域名都恒 401 code:1001，换域名是解不通的
	// —— 必须用账号所属区域的域名。
	AgentHostSG     = "https://coresg-normal.trae.ai" // bootConfig.agent.trae.SG
	AgentHostUS     = "https://coreva-normal.trae.ai" // bootConfig.agent.trae.US
	AgentHostNormal = "https://core-normal.trae.ai"   // bootConfig.agent.trae.normal
	UgHost         = "https://api.trae.ai"                // 国际版（原 api.trae.cn）
	OAuthHost      = "https://api.trae.ai"                // 国际版（原 api.trae.com.cn）
	ConsoleHost    = "https://www.trae.ai"                // 国际版（原 www.trae.cn）
	WorkHost       = "https://work.trae.ai"               // 国际版（原 work.trae.cn）
	ClientID       = "en1oxy7wnw8j9n"
	AppID          = "6eefa01c-1036-4c7e-9ca5-d891f63bfcd8"
	IdeVersion     = "0.1.52" // 对齐 connectedGraph/traework2api 上游，对应更新更全的模型配置表（含 glm-5.3）
	IdeVersionCode = "20260811"
	// AuthAppVersion 只用于授权页的 x_app_version，不要拿去当客户端指纹头。
	// auth_from=trae（TRAE Desktop App 路由，chunk 7093）拿远程配置 ide_required_version
	// 与 x_app_version 做**客户端** semver 比较，低于要求就渲染「版本过低」分支
	// （state 4：按钮变成 "open TRAE and upgrade"，永远不会回调）。
	// 取值来源：GET https://www.trae.ai/api/tcc/config?key=ide_required_version&region=us
	// （实测 2026-10-05 = 3.5.20；未带 cookie 时可能返回空串，此时页面跳过该检查）。
	// 注意这是**页面**要求的版本，与 IDE 真实构建号不是一回事，故与 IdeBuildVersion 分开。
	AuthAppVersion = "3.5.20"
	// IdeBuildVersion IDE 真实构建号（ExchangeToken 的 IDEVersion / DeviceInfo.ClientVersion）。
	// 取值来源：GET https://api.trae.ai/icube/api/v1/native/version/trae/latest
	IdeBuildVersion = "2.3.88407"
	// PlatformCode 授权页 platformCode：auth_from=trae 的 IDE 路由是 "IDE_PC"，
	// SOLO/TraeWork 路由是 "SOLO_PC"。必须与授权页一致，否则交换出来的设备绑定会对不上。
	PlatformCode = "IDE_PC"
	// ClientSecret 网页版 ExchangeToken 固定送字面量 "-"（bundle 内 w() 函数）。
	ClientSecret = "-"
	DeviceBrand    = "20Y5A002XX"     // 设备机型示例（指纹头用，可替换为真实机型，无需精确匹配）
	OSVersion      = "Windows 10 Pro" // 真实客户端系统版本
	PluginVersion  = "2.3.73734"      // 真实客户端插件版本（登录 URL 用）
	Function       = "solo_work_lite" // 办公版（TraeWork）function
	FunctionCode   = "solo_agent"     // 代码版（TraeCode）function

	// 定价分组：Trae 同一上游有两个 function，各渠道记录自己所用的口径。
	// PricingFunctionsCode 必须含**无** _remote 后缀的 solo_agent，否则拿不到新版
	// flash 模型的费率（实测 deepseek-v4.1-flash、glm-5.3-flash 仅在 solo_agent
	// 分组下发）；_remote 分组用于补齐旧命名模型。
	// PricingPrimary* 是各渠道「主 function」：同一模型可能在多个分组下倍率不同
	// （实测豆包 Seed-2.1-Pro：solo_agent=0.08 / _remote=0.8），对话按主 function 计费。
	PricingFunctionsWork = "solo_agent_remote,solo_work_remote,solo_design_remote"
	PricingFunctionsCode = "solo_agent,solo_agent_remote,solo_work_remote,solo_design_remote"
	PricingPrimaryWork   = "solo_work_remote"
	PricingPrimaryCode   = "solo_agent"

	EpChat          = "/api/agent/v3/llm_utils_chat"
	EpModels        = "/api/ide/v1/get_detail_param"
	EpExchange      = "/cloudide/api/v3/trae/oauth/ExchangeToken"
	EpUserInfo      = "/cloudide/api/v3/trae/GetUserInfo"
	EpCheckinStatus = "/trae/api/v2/ug/checkin_credits/status"
	EpCheckinClaim  = "/trae/api/v2/ug/checkin_credits/claim"
	EpEntUsage      = "/trae/api/v2/pay/web_user_ent_usage" // 网页版积分接口（CN；国际版 404）
	// EpEntUsageIntl 国际版的额度接口。实测 /trae/api/v2/pay/{web,ide}_user_ent_usage 在
	// 所有国际域名上都是 404，能返回 user_entitlement_pack_list 的是这一条，
	// 且必须打在**账号自身的 host**（回调里的 host / apiHost）上。
	EpEntUsageIntl = "/trae/api/v1/pay/user_current_entitlement_list"
	EpModelsPricing = "/api/remote/v1/models"               // 模型定价接口
)

const DefaultConfigName = "glm-5.2"

// softRateResetLoc 上游时间戳展示口径：固定按 UTC+8 墙钟解释。
// 上游下发的 Unix 秒与官网展示的日期均以国内时区为准，用本地时区格式化会在
// 非 UTC+8 机器上把到期日算错一天。
var softRateResetLoc = time.FixedZone("UTC+8", 8*60*60)

// AgentHostForRegion 按账号所属区域挑 agent 域名。
//
// 入参是登录回调/GetUserInfo 里的区域标记，形态可能是：
//
//	"sg" / "SG" / "Singapore-Central"  → coresg-normal
//	"us" / "US" / "US-East"            → coreva-normal
//	其它（含空串、cn、未知）            → AgentHostNormal
//
// region 为空时返回空串，调用方应回退到 Client.AgentHost（老账号的 mchost.guru 行为不变）。
func AgentHostForRegion(region string) string {
	r := strings.ToLower(strings.TrimSpace(region))
	if r == "" {
		return ""
	}
	switch {
	case strings.Contains(r, "sg"), strings.Contains(r, "singapore"):
		return AgentHostSG
	case strings.HasPrefix(r, "us"), strings.Contains(r, "united states"):
		return AgentHostUS
	default:
		return AgentHostNormal
	}
}
