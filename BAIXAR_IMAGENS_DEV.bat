@echo off
rem Baixa as DeveloperDiskImages usadas pelas Opcoes de desenvolvedor do modulo iOS
rem (repositorio https://github.com/prosch88/ufade_developer).
chcp 65001 >nul
cd /d "%~dp0motores\ios"
where git >nul 2>&1
if errorlevel 1 (
  echo Git nao encontrado. Instale com: winget install -e --id Git.Git
  pause
  exit /b 1
)
if exist "ufade_developer\Developer" (
  echo Imagens ja presentes em motores\ios﫞_developer
  pause
  exit /b 0
)
rmdir "ufade_developer" 2>nul
git clone --depth 1 https://github.com/prosch88/ufade_developer ufade_developer
pause
