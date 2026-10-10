@echo off
setlocal
cd /d "%~dp0"

echo Starting FrontDesk AI web server + LiveKit voice worker...
echo Web chat:   http://127.0.0.1:8000
echo Voice page: http://127.0.0.1:8000/voice/page
echo (Voice needs LIVEKIT_* and DEEPGRAM_API_KEY or GROQ_API_KEY in .env — see VOICE.md)
echo.

start "FrontDesk Voice Worker" cmd /k ".venv\Scripts\python.exe app\voice\agent.py start"
start "" cmd /c "timeout /t 4 >nul & start "" http://127.0.0.1:8000/voice/page"
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
pause
