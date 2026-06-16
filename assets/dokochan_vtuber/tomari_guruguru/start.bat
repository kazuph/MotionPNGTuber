@echo off
setlocal
cd /d "%~dp0"

if not exist node_modules (
  npm install
)

start "" "http://127.0.0.1:5173/talk.html"
npm run dev -- --host 127.0.0.1
