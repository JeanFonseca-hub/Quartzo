# -*- coding: utf-8 -*-
"""Autoverificação do código do Quartzo.

O arquivo integridade.json (gerado por ferramentas/gerar_integridade.py no
momento da publicação) guarda o SHA-256 de cada arquivo de código do Quartzo e
dos motores. Na abertura, o lançador recalcula e compara. Qualquer divergência
é exibida na tela e registrada no log da sessão.
"""
import hashlib
import json
import os

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARQ_BASE = os.path.join(RAIZ, "integridade.json")
EXTENSOES = (".py", ".pyw", ".json", ".bat", ".txt")
PASTAS = ("quartzo_app", "motores", "ferramentas", "i18n", "testes")
# binários que os motores enviam ou executam no aparelho (exploits, su, payloads):
# entram na base independentemente da extensão, pois não têm sufixo de código
PASTAS_BINARIAS = ("motores/android/ressources", "motores/ios/ressources")
RAIZ_ARQUIVOS = ("quartzo.py", "requirements.txt", "INSTALAR.bat", "INICIAR_QUARTZO.bat",
                 "QUARTZO_CONSOLE.bat", "QUARTZO_DIAGNOSTICO.bat", "BAIXAR_IMAGENS_DEV.bat", "ATUALIZAR_MOTORES.bat")


def _sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def listar_arquivos():
    saida = {f for f in RAIZ_ARQUIVOS if os.path.isfile(os.path.join(RAIZ, f))}
    for pasta in PASTAS:
        for base, dirs, nomes in os.walk(os.path.join(RAIZ, pasta)):
            dirs[:] = sorted(d for d in dirs if d not in ("__pycache__", "ufade_developer", ".git"))
            for n in sorted(nomes):
                if n.endswith(EXTENSOES):
                    saida.add(os.path.relpath(os.path.join(base, n), RAIZ).replace("\\", "/"))
    for pasta in PASTAS_BINARIAS:
        for base, dirs, nomes in os.walk(os.path.join(RAIZ, *pasta.split("/"))):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for n in sorted(nomes):
                saida.add(os.path.relpath(os.path.join(base, n), RAIZ).replace("\\", "/"))
    return sorted(saida)


def calcular():
    return {f: _sha256(os.path.join(RAIZ, f)) for f in listar_arquivos()}


def verificar():
    """Retorna (situacao, detalhes). situacao: 'OK', 'ALTERADO' ou 'SEM_BASE'."""
    if not os.path.isfile(ARQ_BASE):
        return "SEM_BASE", ["integridade.json ausente"]
    with open(ARQ_BASE, "r", encoding="utf-8") as fh:
        base = json.load(fh)["arquivos"]
    atual = calcular()
    det = []
    for f, h in base.items():
        if f not in atual:
            det.append(f"ausente: {f}")
        elif atual[f] != h:
            det.append(f"alterado: {f}")
    for f in atual:
        if f not in base and f != "integridade.json":
            det.append(f"novo: {f}")
    return ("OK" if not det else "ALTERADO"), det


def sha256_da_base():
    return _sha256(ARQ_BASE) if os.path.isfile(ARQ_BASE) else ""
