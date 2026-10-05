import time
from dotenv import load_dotenv; load_dotenv(".env")
from deerflow.client import DeerFlowClient
from sovereign_research import fetch

fetch.current = fetch.Capture()
c = DeerFlowClient(config_path="config.yaml", thinking_enabled=False, subagent_enabled=False)
t = time.time()
types = {}
final = ""
for ev in c.stream("Research: what is TSMC's latest announced CoWoS capacity plan? Search the web, open 2-3 relevant pages with web_fetch, then answer in 3 sentences with sources."):
    types[ev.type] = types.get(ev.type, 0) + 1
    if ev.type == "messages-tuple" and isinstance(ev.data, dict) and ev.data.get("type") == "tool":
        print(f"  [{time.time()-t:5.1f}s] tool result: {ev.data.get('name')} ({len(str(ev.data.get('content','')))} chars)", flush=True)
    if ev.type == "messages-tuple" and isinstance(ev.data, dict) and ev.data.get("type") == "ai" and ev.data.get("tool_calls"):
        for tc in ev.data["tool_calls"]:
            print(f"  [{time.time()-t:5.1f}s] tool call: {tc.get('name')} {str(tc.get('args'))[:100]}", flush=True)
print("event types:", types)
print(f"took {time.time()-t:.0f}s")
for p in fetch.current.pages:
    print(f"  captured {p.status:7} {len(p.markdown):6} chars {p.url[:90]} {p.error}")
