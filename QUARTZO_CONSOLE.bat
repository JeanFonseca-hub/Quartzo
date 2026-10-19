@echo off
rem Abre o Quartzo com a janela de console visivel (para diagnostico de erros).
cd /d "%~dp0"
".venv\Scripts\python.exe" quartzo.py %*
pause
