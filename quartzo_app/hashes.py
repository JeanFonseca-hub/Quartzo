# -*- coding: utf-8 -*-
"""Manifesto de integridade: MD5, SHA-1 e SHA-256 de cada arquivo de uma pasta,
com reverificação (segunda leitura de cada arquivo) e registro do resultado."""
import csv
import hashlib
import os
from datetime import datetime, timezone

BLOCO = 4 * 1024 * 1024


def hash_arquivo(caminho, cancelar=None):
    md5, sha1, sha256 = hashlib.md5(), hashlib.sha1(), hashlib.sha256()
    with open(caminho, "rb") as fh:
        while True:
            if cancelar is not None and cancelar.is_set():
                raise InterruptedError
            b = fh.read(BLOCO)
            if not b:
                break
            md5.update(b)
            sha1.update(b)
            sha256.update(b)
    return md5.hexdigest(), sha1.hexdigest(), sha256.hexdigest()


def sha256_arquivo(caminho):
    return hash_arquivo(caminho)[2]


def dentro_de(caminho, pasta):
    """True se `caminho` é a própria `pasta` ou fica dentro dela."""
    a = os.path.normcase(os.path.abspath(caminho))
    b = os.path.normcase(os.path.abspath(pasta))
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:  # unidades diferentes no Windows
        return False


def gerar_manifesto(pasta, progresso=None, cancelar=None, pasta_saida=None):
    """Percorre `pasta` e grava o manifesto manifesto_hashes_<data>.csv.

    Sem `pasta_saida`, o manifesto fica dentro de `pasta`. Informando
    `pasta_saida` (uma pasta irmã, fora da evidência), o manifesto, o selo e o
    registro vão para ela e a pasta de evidência não recebe nenhum arquivo novo.
    Os caminhos do manifesto são sempre relativos a `pasta`.
    progresso(feitos, total, arquivo_atual) é chamado a cada arquivo.
    Retorna (caminho_csv, caminho_sha256_do_csv, quantidade, bytes_totais).
    """
    pasta = os.path.abspath(pasta)
    dentro = pasta_saida is None
    saida = pasta if dentro else os.path.abspath(pasta_saida)
    os.makedirs(saida, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_csv = f"manifesto_hashes_{carimbo}.csv"
    caminho_csv = os.path.join(saida, nome_csv)
    arquivos = []
    for base, dirs, nomes in os.walk(pasta):
        dirs.sort()
        for n in sorted(nomes):
            p = os.path.join(base, n)
            if os.path.abspath(p) == caminho_csv or (dentro and n.startswith("manifesto_hashes_")):
                continue
            if os.path.isfile(p) and not os.path.islink(p):
                arquivos.append(p)
    total_bytes = 0
    with open(caminho_csv, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["caminho_relativo", "tamanho_bytes", "modificado_utc", "md5", "sha1", "sha256"])
        for i, p in enumerate(arquivos, 1):
            if progresso:
                progresso(i - 1, len(arquivos), p)
            st = os.stat(p)
            md5, sha1, sha256 = hash_arquivo(p, cancelar)
            total_bytes += st.st_size
            w.writerow([os.path.relpath(p, pasta).replace("\\", "/"), st.st_size,
                        datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        md5, sha1, sha256])
        if progresso:
            progresso(len(arquivos), len(arquivos), "")
    selo = caminho_csv + ".sha256"
    with open(selo, "w", encoding="utf-8") as fh:
        fh.write(f"{sha256_arquivo(caminho_csv)}  {nome_csv}\n")
    return caminho_csv, selo, len(arquivos), total_bytes


def reverificar_manifesto(caminho_csv, progresso=None, cancelar=None, pasta=None):
    """Segunda leitura de cada arquivo listado no manifesto, logo depois de gerado.

    Confere tamanho, MD5, SHA-1 e SHA-256 com o que foi gravado no CSV.
    `pasta` é a pasta de evidência a que os caminhos do manifesto se referem;
    omitida, assume-se a pasta do próprio manifesto (manifesto gravado dentro
    da evidência).
    Retorna (total, integros, divergencias), em que divergencias é uma lista
    de (caminho_relativo, motivo).
    """
    pasta = os.path.abspath(pasta) if pasta else os.path.dirname(os.path.abspath(caminho_csv))
    with open(caminho_csv, "r", newline="", encoding="utf-8-sig") as fh:
        linhas = list(csv.DictReader(fh, delimiter=";"))
    divergencias = []
    for i, r in enumerate(linhas, 1):
        rel = r["caminho_relativo"]
        p = os.path.join(pasta, *rel.split("/"))
        if progresso:
            progresso(i - 1, len(linhas), p)
        if not os.path.isfile(p):
            divergencias.append((rel, "arquivo não encontrado"))
            continue
        if os.path.getsize(p) != int(r["tamanho_bytes"]):
            divergencias.append((rel, f"tamanho {os.path.getsize(p)} (manifesto: {r['tamanho_bytes']})"))
            continue
        md5, sha1, sha256 = hash_arquivo(p, cancelar)
        if (md5, sha1, sha256) != (r["md5"], r["sha1"], r["sha256"]):
            divergencias.append((rel, "hash diferente do manifesto"))
    if progresso:
        progresso(len(linhas), len(linhas), "")
    return len(linhas), len(linhas) - len(divergencias), divergencias


def gravar_registro(caminho_csv, selo, quantidade, total_bytes, reverificacao, inicio, fim, log_sessao=None,
                    pasta_evidencia=None, dados_caso=None):
    """Grava manifesto_hashes_<data>.registro.txt ao lado do manifesto e acrescenta
    o SHA-256 desse registro ao arquivo .sha256 (selo). Retorna o caminho do registro.

    `pasta_evidencia` é a pasta a que o manifesto se refere; omitida, assume-se a
    pasta do próprio manifesto. O registro existe para que nada precise ser escrito
    no log de sessão depois que o manifesto calculou o hash dele.
    """
    total, integros, diverg = reverificacao
    nome_csv = os.path.basename(caminho_csv)
    registro = caminho_csv[:-4] + ".registro.txt"
    pasta_ev = os.path.abspath(pasta_evidencia) if pasta_evidencia else os.path.dirname(os.path.abspath(caminho_csv))
    with open(selo, "r", encoding="utf-8") as fh:
        sha_csv = fh.read().split()[0]
    caso = dados_caso or {}
    cabecalho_caso = []
    if any(caso.get(k) for k in ("numero", "nome", "evidencia", "examinador")):
        cabecalho_caso = [
            f"Nº do procedimento .........: {caso.get('numero', '')}",
            f"Nome do caso ...............: {caso.get('nome', '')}",
            f"Nº do item / lacre .........: {caso.get('evidencia', '')}",
            f"Responsável ................: {caso.get('examinador', '')}",
        ]
    linhas = [
        "REGISTRO DO MANIFESTO DE HASHES (Quartzo)",
        *cabecalho_caso,
        f"Pasta de evidência .........: {pasta_ev}",
        f"Manifesto gravado em .......: {os.path.dirname(os.path.abspath(caminho_csv))}",
        f"Manifesto ..................: {nome_csv} ({quantidade} arquivo(s), {total_bytes} bytes)",
        f"SHA-256 do manifesto .......: {sha_csv}",
        f"Início do cálculo ..........: {inicio.isoformat(timespec='seconds')}",
        f"Fim da reverificação .......: {fim.isoformat(timespec='seconds')}",
        f"Reverificação ..............: {integros} de {total} arquivo(s) íntegro(s) na segunda leitura; "
        f"{len(diverg)} divergência(s)",
    ]
    for rel, motivo in diverg[:200]:
        linhas.append(f"    - {rel}: {motivo}")
    if log_sessao:
        sha_log = sha256_arquivo(log_sessao) if os.path.isfile(log_sessao) else "(arquivo não encontrado)"
        linhas.append(f"Log de sessão ..............: {os.path.basename(log_sessao)} (SHA-256 {sha_log})")
        linhas.append("    encerrado antes do cálculo e incluído no manifesto; nada foi gravado nele depois.")
    linhas += [
        "Assinatura e carimbo de tempo: o Quartzo não assina o manifesto nem obtém carimbo de tempo",
        "(RFC 3161). Para vincular este selo ao procedimento, transcreva no documento oficial o",
        "SHA-256 do manifesto informado acima.",
    ]
    with open(registro, "w", encoding="utf-8") as fh:
        fh.write("\n".join(linhas) + "\n")
    with open(selo, "a", encoding="utf-8") as fh:
        fh.write(f"{sha256_arquivo(registro)}  {os.path.basename(registro)}\n")
    return registro


def gravar_conferencia(caminho_csv, pasta, reverificacao, inicio, fim, pasta_saida=None):
    """Grava um relatório da reconferência de `pasta` contra o manifesto `caminho_csv`,
    em `pasta_saida` (padrão: a pasta do manifesto). Retorna o caminho do relatório."""
    total, integros, diverg = reverificacao
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    saida = os.path.abspath(pasta_saida) if pasta_saida else os.path.dirname(os.path.abspath(caminho_csv))
    os.makedirs(saida, exist_ok=True)
    destino = os.path.join(saida, f"conferencia_{carimbo}.txt")
    linhas = [
        "CONFERÊNCIA DE EXTRAÇÃO CONTRA O MANIFESTO (Quartzo)",
        f"Manifesto ..................: {os.path.abspath(caminho_csv)}",
        f"Pasta conferida ............: {os.path.abspath(pasta)}",
        f"Início .....................: {inicio.isoformat(timespec='seconds')}",
        f"Fim ........................: {fim.isoformat(timespec='seconds')}",
        f"Resultado ..................: {integros} de {total} arquivo(s) conferem; {len(diverg)} divergência(s)",
        "-" * 78,
    ]
    if diverg:
        linhas.append("Arquivos que NÃO conferem (tamanho, MD5, SHA-1 ou SHA-256 diferentes, ou ausentes):")
        for rel, motivo in diverg[:1000]:
            linhas.append(f"    - {rel}: {motivo}")
    else:
        linhas.append("Todos os arquivos do manifesto conferem com a pasta (tamanho, MD5, SHA-1 e SHA-256).")
    with open(destino, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(linhas) + "\n")
    return destino
