#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gera integridade.json: SHA-256 de cada arquivo de código do Quartzo.

Execute SEMPRE depois de alterar código, traduções ou sincronizar os motores:
    python ferramentas/gerar_integridade.py
O lançador compara os arquivos com esta base a cada abertura.
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quartzo_app import VERSAO, integridade  # noqa: E402

if __name__ == "__main__":
    arquivos = integridade.calcular()
    dados = {"versao_quartzo": VERSAO,
             "gerado_em_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "algoritmo": "SHA-256", "arquivos": arquivos}
    with open(integridade.ARQ_BASE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(dados, fh, ensure_ascii=False, indent=1)
    print(f"integridade.json gerado com {len(arquivos)} arquivos.")
    print(f"SHA-256 da base: {integridade.sha256_da_base()}")
