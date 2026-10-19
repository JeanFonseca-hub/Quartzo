@echo off
rem Verifica se este computador esta pronto para extracoes (nao toca em aparelhos).
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" ferramentas\diagnostico.py %*
pause
