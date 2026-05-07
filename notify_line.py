"""LINE 推播 — 優先讀取 .env，其次讀系統環境變數。"""
import os, json, sys, urllib.request, urllib.error
from pathlib import Path

# ── 自動載入同層 .env ─────────────────────────────────────────────────────────
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=_env_path, override=False)
    except ImportError:
        # dotenv 未安裝時，手動解析
        with open(_env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

# ── 推播函式 ──────────────────────────────────────────────────────────────────
def push(message: str) -> bool:
    token   = os.environ.get("LINE_TOKEN", "")
    user_id = os.environ.get("LINE_USER_ID", "")
    debug   = os.environ.get("DEBUG_MODE", "false").lower() == "true"

    if debug:
        print(f"[LINE][DEBUG] 推播內容預覽（未實際發送）：\n{message}", file=sys.stderr)
        return True

    if not token or not user_id:
        print("[LINE] ⚠️  LINE_TOKEN / LINE_USER_ID 未設定\n"
              f"      請編輯 {_env_path} 填入憑證", file=sys.stderr)
        return False

    if token.startswith("請填入") or user_id.startswith("請填入"):
        print("[LINE] ⚠️  .env 尚未填寫實際憑證，請先設定", file=sys.stderr)
        return False

    body = json.dumps({
        "to": user_id,
        "messages": [{"type": "text", "text": message[:4900]}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=body, method="POST",
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ok = r.status == 200
            print(f"[LINE] {'✅ 推播成功' if ok else '❌ 推播失敗'} (HTTP {r.status})",
                  file=sys.stderr)
            return ok
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="ignore")
        print(f"[LINE] ❌ HTTP {e.code}: {body_err}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[LINE] ❌ 錯誤: {e}", file=sys.stderr)
        return False


def test():
    """測試推播（執行：python notify_line.py）"""
    import datetime as dt
    msg = (f"✅ LINE Bot 連線測試\n"
           f"時間：{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
           f"台股策略已整合，每日自動推播中。")
    ok = push(msg)
    print("推播結果：", "成功 ✅" if ok else "失敗 ❌（請檢查 .env 憑證）")


if __name__ == "__main__":
    test()
