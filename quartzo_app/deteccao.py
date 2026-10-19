# -*- coding: utf-8 -*-
"""Detecção passiva de dispositivos conectados.

A detecção do lançador foi desenhada para tocar o mínimo possível no aparelho:

* Apple: consulta apenas o usbmuxd (lista de UDIDs e tipo de conexão). Não abre
  sessão lockdown, portanto NÃO dispara o aviso "Confiar neste computador".
* Android: executa somente "adb devices -l", que lê o estado do transporte USB
  (serial, estado de autorização e modelo informado no anúncio do aparelho).
  Nenhum comando é executado dentro do aparelho.
"""
import shutil
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def caminho_adb():
    """Procura o adb no PATH e nos locais usuais de instalação no Windows."""
    adb = shutil.which("adb")
    if adb:
        return adb
    if sys.platform == "win32":
        import glob
        import os
        candidatos = [
            os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\adb.exe"),
            r"C:\platform-tools\adb.exe",
        ]
        candidatos += glob.glob(os.path.expandvars(
            r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Google.PlatformTools*\platform-tools\adb.exe"))
        for c in candidatos:
            if os.path.isfile(c):
                return c
    return None


def listar_apple():
    """Retorna (lista_de_dicts, mensagem_de_erro_ou_None)."""
    try:
        from pymobiledevice3 import usbmux
    except Exception as e:  # biblioteca ausente
        return [], f"pymobiledevice3 indisponível ({e.__class__.__name__})"
    try:
        dispositivos = usbmux.list_devices()
    except Exception as e:
        if isinstance(e, (FileNotFoundError, ConnectionRefusedError, ConnectionError, OSError)):
            msg = "serviço de comunicação com aparelhos Apple (usbmuxd) indisponível"
        else:
            msg = str(e) or e.__class__.__name__
        if sys.platform == "win32":
            msg += " - instale o app \"Dispositivos Apple\" (Microsoft Store) ou o iTunes"
        return [], msg
    saida = []
    vistos = set()
    for d in dispositivos:
        udid = getattr(d, "serial", "") or ""
        if udid in vistos:
            continue
        vistos.add(udid)
        conexao = getattr(d, "connection_type", "") or ""
        saida.append({"plataforma": "Apple", "id": udid,
                      "estado": "conectado" if conexao.upper() == "USB" else conexao,
                      "modelo": "dispositivo Apple", "conexao": conexao})
    return saida, None


def listar_android():
    adb = caminho_adb()
    if not adb:
        return [], "adb não encontrado (instale o Android Platform Tools)"
    try:
        r = subprocess.run([adb, "devices", "-l"], capture_output=True, text=True,
                           timeout=20, creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        return [], f"falha ao executar o adb: {e}"
    saida = []
    estados = {"device": "autorizado", "unauthorized": "NÃO autorizado (confirme no aparelho)",
               "offline": "offline", "recovery": "modo recovery", "sideload": "sideload",
               "bootloader": "bootloader", "authorizing": "autorizando..."}
    for linha in r.stdout.splitlines()[1:]:
        partes = linha.split()
        if len(partes) < 2 or partes[0].startswith("*"):
            continue
        info = dict(p.split(":", 1) for p in partes[2:] if ":" in p)
        saida.append({"plataforma": "Android", "id": partes[0],
                      "estado": estados.get(partes[1], partes[1]),
                      "modelo": info.get("model", "").replace("_", " ") or "dispositivo Android",
                      "conexao": "Wi-Fi" if ":" in partes[0] else "USB"})
    return saida, None


def listar_fastboot():
    exe = shutil.which("fastboot")
    if not exe and caminho_adb():
        import os
        cand = os.path.join(os.path.dirname(caminho_adb()), "fastboot" + (".exe" if sys.platform == "win32" else ""))
        exe = cand if os.path.isfile(cand) else None
    if not exe:
        return []
    try:
        r = subprocess.run([exe, "devices"], capture_output=True, text=True, timeout=10,
                           creationflags=CREATE_NO_WINDOW)
    except Exception:
        return []
    return [{"plataforma": "Android", "id": l.split()[0], "estado": "modo fastboot",
             "modelo": "dispositivo em fastboot", "conexao": "USB"}
            for l in r.stdout.splitlines() if l.strip()]


def detectar():
    """Retorna (dispositivos, avisos)."""
    avisos = []
    apple, erro = listar_apple()
    if erro:
        avisos.append("Apple: " + erro)
    android, erro = listar_android()
    if erro:
        avisos.append("Android: " + erro)
    return apple + android + listar_fastboot(), avisos
