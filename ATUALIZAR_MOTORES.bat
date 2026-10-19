@echo off
rem Atualiza os motores a partir dos repositorios originais do UFADE e do ALEX.
cd /d "%~dp0"
".venv\Scripts\python.exe" ferramentas\sincronizar.py %*
pause
