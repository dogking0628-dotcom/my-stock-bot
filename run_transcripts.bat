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

python -X utf8 fetch_transcript.py --watch --queue --notify
python -X utf8 analyze_transcript.py --new --notify

git add data\transcripts data\video_queue.txt analyst_claims.md
if exist data\evidence_candidates.csv git add data\evidence_candidates.csv
git diff --staged --quiet || git commit -m "chore: transcripts %date:~0,10%"
git push
endlocal
