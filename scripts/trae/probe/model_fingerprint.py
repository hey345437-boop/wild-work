# -*- coding: utf-8 -*-
"""把 Trae 渠道的模型「后端真名」挖出来。

Trae 的 SSE 里有一帧 event:extra_info，带 {"model":"<后端真实模型名>"}。
这是服务端自己写在响应里的，比问模型"你是谁"可靠得多 ——
实测 kimi-k3 会说"我是Claude"（幻觉），但 extra_info 老实写 aws-kimi-k3。

用法: .venv/bin/python probe/model_fingerprint.py [model ...]
"""
import json, re, sys, requests
A = json.load(open('/Users/lan/dsh/wild-work/auths/trae-7693360420411507733.json'))
TOK = A['auth']['accessToken']; UID = A['account']['uid']
H = {"Content-Type":"application/json","Accept":"text/event-stream","User-Agent":"Trae/3.5.104",
     "x-ide-token":TOK,"X-Uid":UID,"X-Device-Id":A['auth']['deviceId'],"X-Machine-Id":A['auth']['machineId'],
     "X-App-Id":"6eefa01c-1036-4c7e-9ca5-d891f63bfcd8","X-Ide-Version":"0.1.63",
     "X-Ide-Version-Code":"20260904","X-App-Version-Code":"20260904","X-Version-Code":"20260904",
     "X-Device-Type":"darwin","X-Device-Platform":"darwin","X-Platform":"darwin",
     "X-OS":"darwin","X-OSType":"darwin","X-System":"darwin","Request-Traffic-Type":"prod"}
HOST = "https://coresg-normal.trae.ai"

def probe(cfg, fn="solo_work_lite"):
    BODY={"function":fn,"stream":True,"config_name":cfg,"model":cfg,
          "messages":[{"role":"user","content":[{"type":"text","text":"Reply with exactly: PONG"}]}]}
    r=requests.post(HOST+"/api/agent/v3/llm_utils_chat",json=BODY,headers=H,timeout=120,stream=True)
    ev=None; out={"apimodel":None,"extramodel":None,"idprefix":None,"marks":set()}
    for line in r.iter_lines(decode_unicode=True):
        if not line: continue
        if line.startswith("event:"): ev=line[6:].strip()
        elif line.startswith("data:"):
            try: j=json.loads(line[5:])
            except: continue
            if not isinstance(j,dict): continue
            if ev=="metadata" and j.get("model"): out["apimodel"]=j["model"]
            if ev=="extra_info":
                if j.get("model"): out["extramodel"]=j["model"]
                c=json.dumps(j.get("content"))
                for m in re.findall(r'"id":"([a-z]{2})_', c): out["idprefix"]=m+"_"
                for k in ("encrypted_content","reasoning","response.output_text","tool_call"):
                    if k in c: out["marks"].add(k)
    return out

MODELS = sys.argv[1:] or ["kimi-k3","gpt-5.2","gpt-5.4"]
print(f"{'config':14s} {'metadata.model':18s} {'extra_info.model':26s} {'id前缀':8s} 特征")
for m in MODELS:
    try:
        o=probe(m)
        print(f"{m:14s} {str(o['apimodel'] or '-'):18s} {str(o['extramodel'] or '-'):26s} "
              f"{str(o['idprefix'] or '-'):8s} {','.join(sorted(o['marks']))}")
    except Exception as e:
        print(f"{m:14s} ERR {str(e)[:60]}")
