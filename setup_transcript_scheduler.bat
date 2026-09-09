@echo off
:: 每日 19:30（機器時區）跑 run_transcripts.bat：抓頻道逐字稿 → 分析入庫 → push
set SCRIPT_DIR=%~dp0
schtasks /create ^
  /tn "InvestBot_Transcripts" ^
  /tr "\"%SCRIPT_DIR%run_transcripts.bat\"" ^
  /sc daily ^
  /st 19:30 ^
  /f ^
  /ru "%USERNAME%"
if %ERRORLEVEL% EQU 0 (
    echo [OK] 逐字稿排程建立成功（每日 19:30）
) else (
    echo [FAIL] 排程建立失敗，請以系統管理員身份執行
)
schtasks /query /tn "InvestBot_Transcripts" 2>nul | findstr "狀態\|Status\|下次\|Next"
pause
