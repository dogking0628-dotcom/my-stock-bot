@echo off
:: 建立每日 23:00 排程跑 run_transcripts.bat（錯過會在下次開機補跑）。
:: 用法：對本檔按右鍵 →「以系統管理員身分執行」。實際邏輯在 setup_transcript_scheduler.ps1。
chcp 65001 >nul
set SCRIPT_DIR=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%setup_transcript_scheduler.ps1"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [FAIL] 排程建立失敗。請確認是用「以系統管理員身分執行」開啟本檔。
)
echo.
pause
