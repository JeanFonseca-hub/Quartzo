#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quartzo - autodiagnóstico do posto de extração.

Verifica, sem tocar em nenhum aparelho além da listagem passiva, se este
computador está pronto para extrações: Python, dependências de cada motor,
ADB/fastboot, serviço de comunicação Apple, caminhos longos do Windows,
pasta de saída, imagens de desenvolvedor e integridade do código.

    .venv\\Scripts\\python.exe ferramentas\\diagnostico.py [pasta_de_saida]

Gera também QUARTZO_diagnostico_<data>.txt na pasta _diag.
"""
import ast
import importlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from quartzo_app import VERSAO, deteccao, integridade  # noqa: E402

linhas = []
falhas = []
avisos = []


def reg(situacao, item, detalhe=""):
    simbolo = {"OK": "[ OK ]", "AVISO": "[AVISO]", "FALHA": "[FALHA]", "INFO": "[INFO]"}[situacao]
    txt = f"{simbolo} {item}" + (f": {detalhe}" if detalhe else "")
    linhas.append(txt)
    print(txt, flush=True)
    if situacao == "FALHA":
        falhas.append(item)
    elif situacao == "AVISO":
        avisos.append(item)


def importacoes_do_motor(script):
    """Lista os módulos de terceiros importados no nível superior do motor."""
    with open(script, "r", encoding="utf-8") as fh:
        arvore = ast.parse(fh.read())
    mods = set()
    for no in arvore.body:
        if isinstance(no, ast.Import):
            mods.update(a.name for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module and no.level == 0:
            mods.add(no.module)
    return sorted(mods)


def testar_motor(nome, pasta, script):
    sys.path.insert(0, pasta)
    erros = []
    mods = importacoes_do_motor(os.path.join(pasta, script))
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            erros.append(f"{m} ({e.__class__.__name__}: {e})")
    sys.path.remove(pasta)
    if erros:
        reg("FALHA", f"Dependências do motor {nome}", "; ".join(erros))
    else:
        reg("OK", f"Dependências do motor {nome}", f"{len(mods)} módulos importados")


def main():
    saida = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser("~"), "Quartzo_extracoes")
    reg("INFO", "Quartzo", f"versão {VERSAO}")
    reg("INFO", "Data/hora", datetime.now().astimezone().isoformat(timespec="seconds"))
    reg("INFO", "Sistema", platform.platform())

    # 1. Python
    v = sys.version_info
    if (v.major, v.minor) == (3, 11):
        reg("OK", "Python", f"{platform.python_version()} ({sys.executable})")
    else:
        reg("FALHA", "Python", f"{platform.python_version()} - os motores exigem 3.11")

    # 2. Integridade
    s, det = integridade.verificar()
    if s == "OK":
        reg("OK", "Integridade do código", f"base SHA-256 {integridade.sha256_da_base()[:16]}...")
    else:
        reg("FALHA" if s == "ALTERADO" else "AVISO", "Integridade do código", f"{s}: " + "; ".join(det[:10]))

    # 3. Dependências dos motores
    testar_motor("iOS (UFADE)", os.path.join(RAIZ, "motores", "ios"), "ufade.py")
    testar_motor("Android (ALEX)", os.path.join(RAIZ, "motores", "android"), "alex.py")
    for extra in ("customtkinter", "tkcalendar", "simpleaudio", "qrcode", "Crypto"):
        try:
            importlib.import_module(extra)
        except Exception as e:
            reg("FALHA", f"Módulo {extra}", str(e))

    # 4. ADB / fastboot
    adb = deteccao.caminho_adb()
    if adb:
        try:
            ver = subprocess.run([adb, "version"], capture_output=True, text=True, timeout=20,
                                 creationflags=deteccao.CREATE_NO_WINDOW).stdout.splitlines()
            reg("OK", "ADB", f"{adb} - {ver[0] if ver else '?'}")
            no_path = shutil.which("adb") is not None
            if not no_path:
                reg("AVISO", "ADB fora do PATH", "o lançador corrige isso ao abrir o módulo Android")
        except Exception as e:
            reg("FALHA", "ADB", f"{adb} não executou: {e}")
    else:
        reg("FALHA", "ADB", "não encontrado - instale com: winget install -e --id Google.PlatformTools")
    fb = shutil.which("fastboot") or (os.path.join(os.path.dirname(adb), "fastboot.exe") if adb else None)
    if fb and os.path.isfile(fb):
        reg("OK", "fastboot", fb)
    else:
        reg("AVISO", "fastboot", "não encontrado (só é usado em procedimentos específicos do Android)")

    # 5. Apple
    apple, erro = deteccao.listar_apple()
    if erro:
        reg("FALHA", "Comunicação com aparelhos Apple (usbmuxd)", erro)
    else:
        reg("OK", "Comunicação com aparelhos Apple (usbmuxd)", f"{len(apple)} aparelho(s) visível(is)")
    if sys.platform == "win32":
        try:
            r = subprocess.run(["sc", "query", "Apple Mobile Device Service"], capture_output=True, text=True,
                               timeout=10, creationflags=deteccao.CREATE_NO_WINDOW)
            if "RUNNING" in r.stdout:
                reg("OK", "Serviço Apple Mobile Device (iTunes)", "em execução")
            else:
                reg("INFO", "Serviço Apple Mobile Device (iTunes)",
                    "não encontrado - normal se o app \"Dispositivos Apple\" estiver em uso")
        except Exception:
            pass
    dev = os.path.join(RAIZ, "motores", "ios", "ufade_developer", "Developer")
    if os.path.isdir(dev):
        reg("OK", "DeveloperDiskImages (opções de desenvolvedor iOS)", f"{len(os.listdir(dev))} versões")
    else:
        reg("AVISO", "DeveloperDiskImages (opções de desenvolvedor iOS)",
            "ausentes - execute BAIXAR_IMAGENS_DEV.bat se for usar captura de tela/listagem no iOS")

    # 6. Android: aparelhos visíveis
    andr, erro = deteccao.listar_android()
    if not erro:
        reg("OK", "Listagem passiva Android (adb devices -l)", f"{len(andr)} aparelho(s)")

    # 7. Caminhos longos
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as k:
                val = winreg.QueryValueEx(k, "LongPathsEnabled")[0]
            if val == 1:
                reg("OK", "Caminhos longos do Windows (LongPathsEnabled)", "habilitado")
            else:
                reg("AVISO", "Caminhos longos do Windows (LongPathsEnabled)",
                    "desabilitado - backups de iOS podem falhar em caminhos com mais de 260 caracteres")
        except Exception as e:
            reg("AVISO", "Caminhos longos do Windows", f"não foi possível verificar ({e})")

    # 8. Pasta de saída
    try:
        os.makedirs(saida, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=saida, prefix="quartzo_teste_", delete=True) as fh:
            fh.write(b"teste")
        livre = shutil.disk_usage(saida).free / 1024 ** 3
        sit = "OK" if livre >= 256 else "AVISO"
        reg(sit, "Pasta de saída", f"{saida} - gravável - {livre:.0f} GB livres"
            + ("" if sit == "OK" else " (recomendado: espaço maior que a capacidade do aparelho)"))
    except Exception as e:
        reg("FALHA", "Pasta de saída", f"{saida}: {e}")

    # 9. Git (para atualizar motores e baixar imagens)
    reg("OK" if shutil.which("git") else "AVISO", "Git",
        shutil.which("git") or "não encontrado (necessário só para BAIXAR_IMAGENS_DEV.bat e atualizações)")

    resumo = (f"\nRESULTADO: {len(falhas)} falha(s), {len(avisos)} aviso(s). "
              + ("Posto PRONTO para extração." if not falhas else "Corrija as falhas antes de extrair."))
    print(resumo)
    linhas.append(resumo)
    pasta_diag = os.path.join(RAIZ, "_diag")
    os.makedirs(pasta_diag, exist_ok=True)
    arq = os.path.join(pasta_diag, f"QUARTZO_diagnostico_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    with open(arq, "w", encoding="utf-8") as fh:
        fh.write("\n".join(linhas) + "\n")
    print(f"Relatório salvo em: {arq}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
