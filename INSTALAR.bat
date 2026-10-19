@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Quartzo - instalacao
echo ==================================================================
echo   Quartzo - Extracao forense de dispositivos moveis - INSTALACAO
echo ==================================================================
echo.
set "PY="
py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,11) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [ERRO] Python 3.11 nao encontrado. Os motores UFADE e ALEX exigem Python 3.11.
  echo        Instale com:  winget install -e --id Python.Python.3.11
  echo        e execute este arquivo novamente.
  pause
  exit /b 1
)
echo [1/4] Python 3.11 encontrado: %PY%
if not exist ".venv\Scripts\python.exe" (
  echo [2/4] Criando ambiente virtual .venv ...
  %PY% -m venv .venv || goto :erro
) else (
  echo [2/4] Ambiente virtual .venv ja existe.
)
echo [3/4] Instalando dependencias - pode levar varios minutos ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :erro
echo [4/4] Verificando programas externos ...
where adb >nul 2>&1
if errorlevel 1 (
  echo.
  echo   ADB ^(Android Platform Tools^) nao encontrado no PATH.
  choice /M "  Instalar agora via winget (Google.PlatformTools)"
  if not errorlevel 2 winget install -e --id Google.PlatformTools
)
echo.
echo   Para aparelhos Apple no Windows e preciso o app "Dispositivos Apple"
echo   (Apple Devices, Microsoft Store) ou o iTunes.
choice /M "  Abrir a pagina do Apple Devices na Microsoft Store"
if not errorlevel 2 start "" "ms-windows-store://pdp/?productid=9NP83LWLPZ9K"
echo.
echo Instalacao concluida. Use INICIAR_QUARTZO.bat para abrir a ferramenta.
echo Recomendado: habilitar caminhos longos do Windows (LongPathsEnabled).
pause
exit /b 0
:erro
echo.
echo [ERRO] A instalacao falhou. Veja as mensagens acima.
pause
exit /b 1
