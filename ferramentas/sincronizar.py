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
import stat
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


def apagar(caminho):
    """Apaga uma pasta inteira, inclusive os arquivos somente leitura.

    No Windows o Git grava os pacotes de objetos (.git/objects/pack) como
    somente leitura, e o shutil.rmtree desiste deles. Aqui o atributo é retirado
    e a remoção é repetida; se mesmo assim algo ficar para trás, o erro sobe,
    em vez de passar em silêncio."""
    def liberar(funcao, alvo, _erro):
        os.chmod(alvo, stat.S_IWRITE)
        funcao(alvo)
    if os.path.lexists(caminho):
        shutil.rmtree(caminho, onerror=liberar)


def restaurar_upstream(nomes):
    """Devolve a upstream/ as versões guardadas em upstream/_<nome>_anterior.

    Cada original é restaurado por conta própria: um erro no UFADE não impede a
    restauração do ALEX, e vice-versa. Devolve a lista de falhas (vazia quando
    tudo voltou ao lugar)."""
    falhas = []
    for nome in nomes:
        dest = os.path.join(RAIZ, "upstream", nome)
        anterior = os.path.join(RAIZ, "upstream", f"_{nome}_anterior")
        try:
            apagar(dest)
            if os.path.isdir(anterior):
                os.rename(anterior, dest)
        except OSError as erro:
            falhas.append(f"{nome}: {erro}")
    return falhas


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
        apagar(os.path.join(alvo, ".git"))
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
    origem_antes = None  # conteúdo original do ORIGEM.json, para devolver se a atualização falhar
    if os.path.exists(arq_origem):
        with open(arq_origem, "rb") as fh:
            origem_antes = fh.read()
    origem = json.loads(origem_antes.decode("utf-8")) if origem_antes else {}
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
                apagar(anterior)
                if os.path.exists(dest):
                    os.rename(dest, anterior)
                trocados.append(nome)
                # .git fica de fora mesmo que algo tenha escapado da limpeza em baixar()
                shutil.copytree(pasta, dest, ignore=shutil.ignore_patterns(".git"))
                origem.setdefault(nome, {}).update({"repositorio": url, "commit": commit,
                                                    "data_commit": data, "obtido_em": date.today().isoformat()})
            # O aviso no cabeçalho dos motores cita o commit lido do ORIGEM.json; por
            # isso ele é gravado antes da geração (e volta ao que era se algo falhar).
            with open(arq_origem, "w", encoding="utf-8", newline="") as fh:  # LF, como no repositório
                json.dump(origem, fh, indent=2, ensure_ascii=False)
        # Os dois motores são montados antes de qualquer troca: se um falhar,
        # nenhum dos dois muda.
        for motor in ("ufade", "alex"):
            print(f"\nRegenerando motor {motor}...")
            trabalho, _ = traduzir.gerar(motor, verbose=False)
            gerados.append((motor, trabalho))
    except BaseException:
        for motor, _ in gerados:
            traduzir.descartar(motor)
        if not offline and origem_antes is not None:
            with open(arq_origem, "wb") as fh:
                fh.write(origem_antes)
        falhas = restaurar_upstream(trocados)
        if falhas:
            print("\nAtualização interrompida. Os motores em uso não foram alterados, mas upstream/ não voltou "
                  "por completo ao que era:\n  " + "\n  ".join(falhas) +
                  "\nA versão anterior continua em upstream/_<nome>_anterior. Feche o Quartzo e, na pasta "
                  "do programa, execute: git checkout -- upstream", file=sys.stderr)
        else:
            print("\nAtualização interrompida: upstream/ voltou ao que era e os motores em uso não foram "
                  "alterados.", file=sys.stderr)
        raise
    finally:
        try:
            apagar(tmp)
        except OSError:
            pass  # sobra na pasta temporária do Windows; não afeta o Quartzo
    for nome in trocados:
        try:
            apagar(os.path.join(RAIZ, "upstream", f"_{nome}_anterior"))
        except OSError as erro:
            print(f"Aviso: não foi possível apagar upstream/_{nome}_anterior ({erro}); apague-a à mão.")
    for motor, trabalho in gerados:
        traduzir.instalar(motor, trabalho)
        traduzir.cmd_relatorio(motor)
    subprocess.run([sys.executable, os.path.join(RAIZ, "ferramentas", "gerar_integridade.py")],
                   check=True, creationflags=_SEM_JANELA)
    print("\nPronto. Revise as linhas FALTA acima e acrescente as traduções em i18n/*.pt_BR.json;"
          "\ndepois rode:  python ferramentas/sincronizar.py --offline")


if __name__ == "__main__":
    main()
