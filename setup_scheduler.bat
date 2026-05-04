@echo off
:: ────────────────────────────────────────────────────────────────
:: 每日投資訊號排程設定
:: 執行時間：
::   05:30 — 美股收盤後隔日早（台灣時間，對應美東前日 16:00 收盤）
::   14:00 — 台股收盤後 30 分鐘（台股 13:30 收盤）
:: ────────────────────────────────────────────────────────────────

set SCRIPT_DIR=%~dp0
set PYTHON=python
set RUNNER=%SCRIPT_DIR%daily_scan.py

echo ====================================================
echo  投資訊號排程設定
echo  腳本路徑：%RUNNER%
echo ====================================================
echo.

:: ── 每日 05:30 推播（美股收盤後）──────────────────────────────
schtasks /create ^
  /tn "InvestBot_US_Close" ^
  /tr "\"%PYTHON%\" -X utf8 \"%RUNNER%\"" ^
  /sc daily ^
  /st 05:30 ^
  /f ^
  /ru "%USERNAME%"

if %ERRORLEVEL% EQU 0 (
    echo [OK] 美股掃描排程建立成功（每日 05:30）
) else (
    echo [FAIL] 排程建立失敗，請以系統管理員身份執行
)

echo.

:: ── 每日 14:00 推播（台股收盤後）──────────────────────────────
schtasks /create ^
  /tn "InvestBot_TW_Close" ^
  /tr "\"%PYTHON%\" -X utf8 \"%SCRIPT_DIR%tw_only_scan.py\"" ^
  /sc daily ^
  /st 14:00 ^
  /f ^
  /ru "%USERNAME%"

if %ERRORLEVEL% EQU 0 (
    echo [OK] 台股掃描排程建立成功（每日 14:00）
) else (
    echo [FAIL] 排程建立失敗
)

echo.
echo ====================================================
echo  排程清單：
schtasks /query /tn "InvestBot_US_Close" 2>nul | findstr "狀態\|Status\|下次\|Next"
schtasks /query /tn "InvestBot_TW_Close" 2>nul | findstr "狀態\|Status\|下次\|Next"
echo ====================================================
echo.
echo 完成！請先編輯 .env 填入 LINE 憑證再測試。
echo 測試推播：python notify_line.py
pause
