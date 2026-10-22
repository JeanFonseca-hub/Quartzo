#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Atualiza os motores a partir dos repositórios originais (UFADE e ALEX).

    python ferramentas/sincronizar.py            # baixa a versão mais recente
    python ferramentas/sincronizar.py --offline  # só regenera a partir de upstream/

Passos:
 1. baixa o código mais recente do GitHub (via git, ou ZIP se não houver git);
 2. substitui upstream/UFADE e upstream/ALEX e registra os commits em ORIGEM.json;
 3. regenera motores/ios e motores/android (tradução + integração);
 4. lista as strings NOVAS de interface ainda sem tradução;
 5. regenera integridade.json.
Os dois motores são montados em pastas de trabalho e só tomam o lugar dos atuais
depois que ambos terminam sem erro. Se algum ajuste de integração não encontrar
o trecho esperado (porque o autor mudou o código), ou se o download falhar, o
processo para com erro, upstream/ volta ao que era e motores/ não é alterado.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import date

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "ferramentas"))
REPOS = {"UFADE": "https://github.com/prosch88/UFADE", "ALEX": "https://github.com/prosch88/ALEX"}

# Quando chamado pela interface (QUARTZO_SILENCIOSO=1), suprime as janelas de
# console que o git abriria; pelo .bat a variável não existe e o comportamento
# (com a saída do git visível no console) continua igual.
_SEM_JANELA = 0x08000000 if (os.name == "nt" and os.environ.get("QUARTZO_SILENCIOSO") == "1") else 0


def baixar(nome, url, destino_tmp):
    alvo = os.path.join(destino_tmp, nome)
    if shutil.which("git"):
        # core.autocrlf=false: o Git do Windows não pode converter LF em CRLF,
        # senão os bytes (e os hashes) diferem do repositório original
        subprocess.run(["git", "-c", "core.autocrlf=false", "clone", "--depth", "1", url, alvo],
                       check=True, creationflags=_SEM_JANELA)
        commit = subprocess.check_output(["git", "-C", alvo, "rev-parse", "HEAD"], text=True,
                                         creationflags=_SEM_JANELA).strip()
        data = subprocess.check_output(["git", "-C", alvo, "log", "-1", "--format=%cI"], text=True,
                                       creationflags=_SEM_JANELA).strip()
        shutil.rmtree(os.path.join(alvo, ".git"), ignore_errors=True)
        return alvo, commit, data
    api = url.replace("https://github.com/", "https://api.github.com/repos/") + "/commits/HEAD"
    with urllib.request.urlopen(api, timeout=60) as r:
        info = json.load(r)
    commit = info["sha"]
    data = info["commit"]["committer"]["date"]
    zurl = url.replace("https://github.com/", "https://codeload.github.com/") + f"/zip/{commit}"
    with urllib.request.urlopen(zurl, timeout=300) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    z.extractall(destino_tmp)
    os.rename(os.path.join(destino_tmp, f"{nome}-{commit}"), alvo)
    return alvo, commit, data


def main():
    import traduzir
    offline = "--offline" in sys.argv
    arq_origem = os.path.join(RAIZ, "upstream", "ORIGEM.json")
    origem = json.load(open(arq_origem, encoding="utf-8")) if os.path.exists(arq_origem) else {}
    tmp = tempfile.mkdtemp(prefix="quartzo_sync_")
    trocados = []  # originais substituídos em upstream/; a versão anterior fica em upstream/_<nome>_anterior
    gerados = []   # (motor, pasta de trabalho) já montados e ainda não instalados
    try:
        if not offline:
            for nome, url in REPOS.items():
                print(f"Baixando {nome}...")
                pasta, commit, data = baixar(nome, url, tmp)
                antigo = origem.get(nome, {}).get("commit", "")
                print(f"  commit {commit[:12]} ({data})" + ("  [sem mudanças]" if commit == antigo else ""))
                dest = os.path.join(RAIZ, "upstream", nome)
                anterior = os.path.join(RAIZ, "upstream", f"_{nome}_anterior")
                if os.path.exists(anterior):
                    shutil.rmtree(anterior)
                if os.path.exists(dest):
                    os.rename(dest, anterior)
                trocados.append(nome)
                shutil.copytree(pasta, dest)
                origem.setdefault(nome, {}).update({"repositorio": url, "commit": commit,
                                                    "data_commit": data, "obtido_em": date.today().isoformat()})
        # Os dois motores são montados antes de qualquer troca: se um falhar,
        # nenhum dos dois muda.
        for motor in ("ufade", "alex"):
            print(f"\nRegenerando motor {motor}...")
            trabalho, _ = traduzir.gerar(motor, verbose=False)
            gerados.append((motor, trabalho))
    except BaseException:
        for motor, _ in gerados:
            traduzir.descartar(motor)
        for nome in trocados:
            dest = os.path.join(RAIZ, "upstream", nome)
            anterior = os.path.join(RAIZ, "upstream", f"_{nome}_anterior")
            shutil.rmtree(dest, ignore_errors=True)
            if os.path.isdir(anterior):
                os.rename(anterior, dest)
        print("\nAtualização interrompida: upstream/ voltou ao que era e os motores em uso não foram alterados.",
              file=sys.stderr)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for nome in trocados:
        shutil.rmtree(os.path.join(RAIZ, "upstream", f"_{nome}_anterior"), ignore_errors=True)
    if not offline:
        with open(arq_origem, "w", encoding="utf-8") as fh:
            json.dump(origem, fh, indent=2, ensure_ascii=False)
    for motor, trabalho in gerados:
        traduzir.instalar(motor, trabalho)
        traduzir.cmd_relatorio(motor)
    subprocess.run([sys.executable, os.path.join(RAIZ, "ferramentas", "gerar_integridade.py")],
                   check=True, creationflags=_SEM_JANELA)
    print("\nPronto. Revise as linhas FALTA acima e acrescente as traduções em i18n/*.pt_BR.json;"
          "\ndepois rode:  python ferramentas/sincronizar.py --offline")


if __name__ == "__main__":
    main()
