"""LINE 推播 — 從環境變數讀取憑證。"""
import os, json, sys, urllib.request, urllib.error

def push(message: str) -> bool:
    token = os.environ.get("LINE_TOKEN", "")
    user_id = os.environ.get("LINE_USER_ID", "")
    if not token or not user_id:
        print("[LINE] ENV LINE_TOKEN / LINE_USER_ID not set", file=sys.stderr)
        return False
    body = json.dumps({
        "to": user_id,
        "messages": [{"type": "text", "text": message[:4900]}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ok = r.status == 200
            print(f"[LINE] {'OK' if ok else 'FAIL'} status={r.status}", file=sys.stderr)
            return ok
    except Exception as e:
        print(f"[LINE] ERROR: {e}", file=sys.stderr)
        return False
