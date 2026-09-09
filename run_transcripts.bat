@echo off
:: ────────────────────────────────────────────────────────────────
:: 本機一鍵：抓追蹤頻道 + 佇列的逐字稿 → Claude 分析入庫 → push
:: 用途：家用網路不會被 YouTube 要求登入驗證，比 GitHub Actions 穩。
:: 需要：pip install yt-dlp anthropic；環境變數 ANTHROPIC_API_KEY（分析用，可先不設）
:: 排程：setup_transcript_scheduler.bat（每日 19:30）
:: ────────────────────────────────────────────────────────────────
setlocal
cd /d "%~dp0"
chcp 65001 >nul

git pull --rebase --autostash
if errorlevel 1 echo [WARN] git pull 失敗，繼續用本地版本

:: 回補歷史（例：8 月）：run_transcripts.bat backfill 20260801 20260831
if "%1"=="backfill" (
  python -X utf8 fetch_transcript.py --watch --scan 120 --since %2 --until %3 --sleep 3
  :: Batch API 五折；送出後最多等 90 分鐘，沒收完再跑一次同指令會接著收
  python -X utf8 analyze_transcript.py --batch --batch-wait 90
) else (
  python -X utf8 fetch_transcript.py --watch --queue --notify
  python -X utf8 analyze_transcript.py --new --notify
)
python -X utf8 score_claims.py --notify

git add data\transcripts data\video_queue.txt data\video_sources.json analyst_claims.md
if exist data\evidence_candidates.csv git add data\evidence_candidates.csv
if exist data\claims.jsonl git add data\claims.jsonl
if exist analyst_scorecard.md git add analyst_scorecard.md
git diff --staged --quiet || git commit -m "chore: transcripts %date:~0,10%"
git push
endlocal
