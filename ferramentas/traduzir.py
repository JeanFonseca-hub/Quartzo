#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quartzo - tradutor contextual de interface (pt-BR) para os motores UFADE e ALEX.

Copyright (C) 2026 Jean Carlos Fonseca. Licenciado sob a GNU GPL v3 ou posterior.

Funcionamento
-------------
O código-fonte original (pasta upstream/) nunca é editado à mão. Este script lê
cada arquivo .py do motor, localiza as strings literais com o módulo `tokenize`
e descobre, pela árvore sintática (`ast`), em que contexto cada uma é usada.
Somente as strings em contexto de INTERFACE (parâmetros text=, title=, values=,
placeholder_text=, conteúdo de relatório PDF, variáveis de texto exibidas ao
usuário) e as comparações com essas mesmas strings são substituídas pela
tradução do dicionário i18n/<motor>.pt_BR.json.

Strings usadas como nome de arquivo, pasta, chave de plist/dicionário,
comando ADB/shell ou caminho dentro de arquivo ZIP/TAR NUNCA são traduzidas,
para preservar a estrutura das extrações (UFED-style, IPED, CASE/UCO).

Ao final, o script compara a AST do arquivo traduzido com a do original:
as duas devem ser idênticas, salvo o conteúdo das strings traduzidas. Qualquer
outra diferença aborta a geração.

Uso
---
  python ferramentas/traduzir.py extrair  <motor>   # lista strings candidatas
  python ferramentas/traduzir.py aplicar   <motor>   # gera motores/<destino>
  python ferramentas/traduzir.py relatorio <motor>   # cobertura da tradução
  <motor> = ufade | alex
"""
import ast
import io
import json
import os
import re
import shutil
import sys
import tokenize

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MOTORES = {
    "ufade": {"origem": os.path.join(RAIZ, "upstream", "UFADE"),
              "destino": os.path.join(RAIZ, "motores", "ios"),
              "principal": "ufade.py"},
    "alex": {"origem": os.path.join(RAIZ, "upstream", "ALEX"),
             "destino": os.path.join(RAIZ, "motores", "android"),
             "principal": "alex.py"},
}

# Arquivos que não contêm interface (tabelas de modelos de aparelho etc.)
# ufed_style.py e case_uco.py geram XML/JSON lidos por outras ferramentas
# (Physical Analyzer, IPED, CASE/UCO) e por isso jamais são traduzidos.
IGNORAR_ARQUIVOS = {"irecv_devices.py", "linux_dpi.py", "__init__.py",
                    "ufed_style.py", "case_uco.py"}

# Parâmetros nomeados que carregam texto exibido ao usuário
KW_INTERFACE = {"text", "title", "values", "placeholder_text", "message",
                "label_text", "button_text", "detail"}
# Funções/métodos cujos argumentos posicionais são texto exibido
FUNC_INTERFACE = {"title", "showinfo", "showerror", "showwarning", "askyesno",
                  "askokcancel", "askquestion", "askretrycancel"}
# Nomes de variáveis/atributos que guardam texto exibido
RE_VAR_INTERFACE = re.compile(
    r"(text|txt|msg|message|label|title|desc|hint|warn|nodevice|tooltip|status_str"
    r"|^device$|^device_info$|^dev_state$)",
    re.I)
# Funções cujos argumentos são dados (nunca traduzir)
FUNC_DADOS = {"join", "open", "Path", "PurePosixPath", "write", "writestr", "add",
              "mkdir", "makedirs", "shell", "run", "Popen", "check_output",
              "check_call", "get", "getprop", "pop", "setdefault", "startswith",
              "endswith", "split", "rsplit", "replace", "strip", "find", "index",
              "exists", "isfile", "isdir", "listdir", "remove", "rename", "copy",
              "copyfile", "copytree", "move", "pull", "push", "push_file",
              "pull_file", "pull_dir_mod", "SubElement", "Element", "set",
              "execute", "encode", "decode", "strftime", "strptime", "basename",
              "dirname", "abspath", "TarInfo", "ZipInfo", "gettarinfo",
              "getmember", "extract", "extractall", "load_font", "CTkFont",
              "CTkImage", "PhotoImage", "geometry", "bind", "after", "getenv",
              "environ", "lstrip", "rstrip", "search", "match", "sub", "findall",
              "compile", "fullmatch"}

TRANSPARENTES = (ast.BinOp, ast.List, ast.Tuple, ast.Set, ast.Starred, ast.BoolOp)


def _nome_func(call):
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _nome_alvo(t):
    if isinstance(t, ast.Name):
        return t.id
    if isinstance(t, ast.Attribute):
        return t.attr
    if isinstance(t, ast.Subscript):
        return _nome_alvo(t.value)
    if isinstance(t, (ast.Tuple, ast.List)):
        return " ".join(_nome_alvo(e) for e in t.elts)
    return ""


def classificar(no, pais):
    """Retorna (contexto, detalhe). Contextos: UI, CMP, DADO, LOG, OUTRO."""
    atual = no
    while True:
        pai = pais.get(id(atual))
        if pai is None:
            return "OUTRO", "modulo"
        if isinstance(pai, TRANSPARENTES):
            atual = pai
            continue
        if isinstance(pai, ast.IfExp) and atual is not pai.test:
            atual = pai
            continue
        if isinstance(pai, ast.Attribute) and pai.attr in ("format", "ljust", "rjust", "center", "upper", "lower", "title"):
            # "texto".format(...) -> sobe até o Call
            avo = pais.get(id(pai))
            if isinstance(avo, ast.Call):
                atual = avo
                continue
            return "OUTRO", "attr"
        if isinstance(pai, ast.Call):
            nome = _nome_func(pai)
            if atual in pai.args:
                if nome == "format" and isinstance(pai.func, ast.Attribute):
                    atual = pai
                    continue
                if nome in FUNC_INTERFACE:
                    return "UI", nome
                if nome == "insert" and pai.args.index(atual) >= 1:
                    return "UI", "insert"
                if nome in ("log", "print"):
                    return "LOG", nome
                if nome in FUNC_DADOS:
                    return "DADO", nome
                if nome in ("str", "CTkLabel", "CTkButton"):
                    atual = pai
                    continue
                return "OUTRO", "arg:" + nome
            if atual is pai.func:
                return "OUTRO", "func"
            return "OUTRO", "call"
        if isinstance(pai, ast.keyword):
            if pai.arg in KW_INTERFACE:
                return "UI", "kw:" + pai.arg
            return "OUTRO", "kw:" + str(pai.arg)
        if isinstance(pai, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            alvos = pai.targets if isinstance(pai, ast.Assign) else [pai.target]
            # <elemento XML>.text = "..." é conteúdo de relatório UFDR/XML: dado
            if any(isinstance(t, ast.Attribute) and t.attr in ("text", "tail") for t in alvos):
                return "DADO", "xml.text"
            nomes = " ".join(_nome_alvo(t) for t in alvos)
            if RE_VAR_INTERFACE.search(nomes):
                return "UI", "var:" + nomes
            return "OUTRO", "var:" + nomes
        if isinstance(pai, ast.Dict):
            if atual in pai.keys:
                return "DADO", "dictkey"
            i = pai.values.index(atual)
            k = pai.keys[i]
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                if k.value.startswith("."):  # estrutura de texto do pdfme
                    return "UI", "pdf:" + k.value
                return "OUTRO", "dictval:" + k.value
            return "OUTRO", "dictval"
        if isinstance(pai, ast.Subscript):
            return "DADO", "subscript"
        if isinstance(pai, ast.Compare):
            return "CMP", "compare"
        if isinstance(pai, ast.Return):
            return "OUTRO", "return"
        if isinstance(pai, ast.Expr):
            return "OUTRO", "expr"
        if isinstance(pai, ast.FormattedValue):
            return "OUTRO", "fvalue"
        return "OUTRO", type(pai).__name__


def _mapear_pais(arvore):
    pais = {}
    for n in ast.walk(arvore):
        for f in ast.iter_child_nodes(n):
            pais[id(f)] = n
    return pais


def _byte_para_char(linha, col_byte):
    return len(linha.encode("utf-8")[:col_byte].decode("utf-8", errors="ignore"))


def _dividir_token(src):
    """Separa prefixo, aspas e conteúdo bruto de um token STRING."""
    m = re.match(r"^([rRbBuUfF]*)('''|\"\"\"|'|\")", src)
    prefixo, aspas = m.group(1), m.group(2)
    conteudo = src[len(prefixo) + len(aspas):-len(aspas)]
    return prefixo, aspas, conteudo


def analisar(caminho):
    with open(caminho, "r", encoding="utf-8") as fh:
        fonte = fh.read()
    linhas = fonte.splitlines(True)
    arvore = ast.parse(fonte)
    pais = _mapear_pais(arvore)
    nos = {}
    for n in ast.walk(arvore):
        alvo = None
        if isinstance(n, ast.JoinedStr):
            alvo = n
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            if isinstance(pais.get(id(n)), ast.JoinedStr):
                continue  # parte literal de f-string: tratada no JoinedStr
            alvo = n
        if alvo is None:
            continue
        # docstrings não são interface
        pai = pais.get(id(alvo))
        if isinstance(pai, ast.Expr):
            continue
        lin = alvo.lineno
        col = _byte_para_char(linhas[lin - 1], alvo.col_offset)
        nos[(lin, col)] = alvo
    ocorrencias = []
    toks = list(tokenize.generate_tokens(io.StringIO(fonte).readline))
    for i, t in enumerate(toks):
        if t.type != tokenize.STRING:
            continue
        no = nos.get(t.start)
        if no is None:
            continue  # token secundário de concatenação implícita / docstring
        # concatenação implícita "a" "b": não traduz (sinaliza)
        multi = (no.end_lineno, _byte_para_char(linhas[no.end_lineno - 1], no.end_col_offset)) != t.end
        prefixo, aspas, conteudo = _dividir_token(t.string)
        if "b" in prefixo.lower():
            continue
        ctx, det = classificar(no, pais)
        ocorrencias.append({"linha": t.start[0], "col": t.start[1], "fim": t.end,
                            "token": t.string, "prefixo": prefixo, "aspas": aspas,
                            "conteudo": conteudo, "ctx": ctx, "det": det, "multi": multi})
    return fonte, ocorrencias


def arquivos_motor(motor):
    origem = MOTORES[motor]["origem"]
    saida = []
    for base, dirs, nomes in os.walk(origem):
        dirs[:] = [d for d in dirs if d not in (".git", "ufade_developer", "__pycache__")]
        for n in sorted(nomes):
            if n.endswith(".py") and n not in IGNORAR_ARQUIVOS:
                saida.append(os.path.join(base, n))
    return sorted(saida)


def tem_texto_humano(s):
    return bool(re.search(r"[A-Za-z]{2,}", re.sub(r"\{[^{}]*\}", "", s)))


def caminho_dicionario(motor):
    return os.path.join(RAIZ, "i18n", f"{motor}.pt_BR.json")


def carregar_dicionario(motor):
    """Junta o glossário comum (i18n/comum.pt_BR.json) com o do motor."""
    d = {"traducoes": {}, "nao_traduzir_cmp": []}
    for p in (os.path.join(RAIZ, "i18n", "comum.pt_BR.json"), caminho_dicionario(motor)):
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as fh:
                parte = json.load(fh)
            d["traducoes"].update(parte.get("traducoes", {}))
            d["nao_traduzir_cmp"] += parte.get("nao_traduzir_cmp", [])
            d.setdefault("manter_em_ingles", []).extend(parte.get("manter_em_ingles", []))
    return d


def cmd_extrair(motor):
    candidatos = {}
    for arq in arquivos_motor(motor):
        _, ocs = analisar(arq)
        rel = os.path.relpath(arq, MOTORES[motor]["origem"])
        for o in ocs:
            if o["ctx"] == "UI" and tem_texto_humano(o["conteudo"]) and not o["multi"]:
                c = candidatos.setdefault(o["conteudo"], [])
                c.append(f"{rel}:{o['linha']}")
    print(json.dumps(candidatos, ensure_ascii=False, indent=1))


def _expressoes_fstring(s):
    # extrai {expr} de primeiro nível, ignorando {{ }}
    s2 = s.replace("{{", "").replace("}}", "")
    return sorted(re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", s2))


def _normalizar_ast(arvore):
    """Zera o conteúdo de todas as strings para comparar estrutura."""
    for n in ast.walk(arvore):
        if isinstance(n, ast.JoinedStr):
            # numa f-string só importam as expressões {..}, não o texto nem a ordem
            n.values = sorted((v for v in n.values if not isinstance(v, ast.Constant)),
                              key=lambda v: ast.dump(v))
    for n in ast.walk(arvore):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            n.value = "§"
        for campo in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
            if hasattr(n, campo):
                setattr(n, campo, 0)
    return ast.dump(arvore)


def traduzir_fonte(fonte, ocorrencias, trad, nao_cmp, estat):
    linhas = fonte.splitlines(True)
    # aplica de trás para frente para não deslocar colunas
    for o in sorted(ocorrencias, key=lambda x: (x["linha"], x["col"]), reverse=True):
        if o["multi"]:
            continue
        c = o["conteudo"]
        if c not in trad:
            continue
        if o["ctx"] == "UI":
            pass
        elif o["ctx"] == "CMP" and c not in nao_cmp:
            estat.setdefault("cmp", []).append((c, o["linha"]))
        else:
            estat["preservadas"].append((c, o["ctx"], o["det"], o["linha"]))
            continue
        novo_conteudo = trad[c]
        if "f" in o["prefixo"].lower() and _expressoes_fstring(c) != _expressoes_fstring(novo_conteudo):
            raise ValueError(f"Expressões da f-string divergem na tradução de: {c!r}")
        aspas = o["aspas"]
        if len(aspas) == 1 and re.search(r"(?<!\\)" + re.escape(aspas), novo_conteudo):
            raise ValueError(f"Aspas não escapadas na tradução de: {c!r}")
        novo_token = o["prefixo"] + aspas + novo_conteudo + aspas
        (l1, c1), (l2, c2) = (o["linha"], o["col"]), o["fim"]
        if l1 != l2:
            antes = linhas[l1 - 1][:c1]
            depois = linhas[l2 - 1][c2:]
            linhas[l1 - 1:l2] = [antes + novo_token + depois]
        else:
            ln = linhas[l1 - 1]
            linhas[l1 - 1] = ln[:c1] + novo_token + ln[c2:]
        estat["traduzidas"] += 1
    return "".join(linhas)


def gerar(motor, verbose=True):
    """Monta o motor traduzido e integrado numa pasta de trabalho ao lado da pasta
    em uso (motores/<destino>_novo) e devolve (pasta_de_trabalho, estatísticas).

    A pasta em uso não é tocada aqui. Se a tradução ou a integração falhar, a
    pasta de trabalho é apagada e o erro segue para quem chamou; o motor em uso
    continua o mesmo. A troca é feita depois, por instalar()."""
    cfg = MOTORES[motor]
    d = carregar_dicionario(motor)
    trad = {k: v for k, v in d["traducoes"].items() if v}
    nao_cmp = set(d["nao_traduzir_cmp"])
    trabalho = cfg["destino"] + "_novo"
    if os.path.exists(trabalho):
        shutil.rmtree(trabalho)  # sobra de uma geração interrompida
    shutil.copytree(cfg["origem"], trabalho,
                    ignore=shutil.ignore_patterns(".git", "__pycache__"))
    estat = {"traduzidas": 0, "preservadas": []}
    try:
        # Se uma string traduzível também é usada como DADO em qualquer ponto do
        # motor (ex.: doc_list.append("yes") ... if doc_list[i] == "yes"), as
        # comparações com ela não podem ser traduzidas.
        analises = {arq: analisar(arq) for arq in arquivos_motor(motor)}
        for _, ocs in analises.values():
            for o in ocs:
                if o["conteudo"] in trad and o["ctx"] not in ("UI", "CMP", "LOG"):
                    nao_cmp.add(o["conteudo"])
        for arq in arquivos_motor(motor):
            fonte, ocs = analises[arq]
            nova = traduzir_fonte(fonte, ocs, trad, nao_cmp, estat)
            # verificação de equivalência estrutural
            a1 = _normalizar_ast(ast.parse(fonte))
            a2 = _normalizar_ast(ast.parse(nova))
            if a1 != a2:
                raise SystemExit(f"ERRO: estrutura do código alterada em {arq}")
            rel = os.path.relpath(arq, cfg["origem"])
            with open(os.path.join(trabalho, rel), "w", encoding="utf-8", newline="") as fh:
                fh.write(nova)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import integrar
        n_aj = integrar.aplicar(motor, trabalho)
    except BaseException:
        shutil.rmtree(trabalho, ignore_errors=True)
        raise
    if verbose:
        print(f"[{motor}] ajustes de integração aplicados: {n_aj}")
        print(f"[{motor}] strings substituídas: {estat['traduzidas']}")
        print(f"[{motor}] ocorrências preservadas (contexto de dados): {len(estat['preservadas'])}")
        for c, ctx, det, ln in estat["preservadas"]:
            print(f"    linha {ln:5d} {ctx:5s} {det:20s} {c[:60]!r}")
        print(f"[{motor}] comparações traduzidas junto com a interface: {len(estat.get('cmp', []))}")
        for c, ln in estat.get("cmp", []):
            print(f"    linha {ln:5d} CMP   {c[:60]!r}")
    return trabalho, estat


def instalar(motor, trabalho):
    """Põe o motor montado em `trabalho` no lugar do motor em uso.

    O motor em uso é renomeado para <destino>_antigo, o novo assume o nome e só
    então o antigo é apagado; se a troca falhar, o antigo volta ao lugar. As
    DeveloperDiskImages do iOS (ufade_developer), baixadas à parte, passam do
    motor antigo para o novo."""
    destino = MOTORES[motor]["destino"]
    antigo = destino + "_antigo"
    if os.path.exists(antigo):
        shutil.rmtree(antigo)
    if os.path.exists(destino):
        os.rename(destino, antigo)
    try:
        os.rename(trabalho, destino)
    except OSError:
        if os.path.isdir(antigo) and not os.path.exists(destino):
            os.rename(antigo, destino)
        raise
    dev_antigo = os.path.join(antigo, "ufade_developer")
    if os.path.isdir(dev_antigo) and os.listdir(dev_antigo):
        dev_novo = os.path.join(destino, "ufade_developer")
        if os.path.isdir(dev_novo):
            shutil.rmtree(dev_novo)
        shutil.move(dev_antigo, dev_novo)
    shutil.rmtree(antigo, ignore_errors=True)


def descartar(motor):
    """Apaga a pasta de trabalho de uma geração que não vai ser instalada."""
    shutil.rmtree(MOTORES[motor]["destino"] + "_novo", ignore_errors=True)


def cmd_aplicar(motor, verbose=True):
    trabalho, estat = gerar(motor, verbose)
    instalar(motor, trabalho)
    return estat


def cmd_relatorio(motor):
    d = carregar_dicionario(motor)
    trad = d["traducoes"]
    manter = set(d.get("manter_em_ingles", []))
    total = faltam = 0
    vistos = set()
    for arq in arquivos_motor(motor):
        _, ocs = analisar(arq)
        for o in ocs:
            if o["ctx"] == "UI" and tem_texto_humano(o["conteudo"]) and not o["multi"]:
                if o["conteudo"] in vistos:
                    continue
                vistos.add(o["conteudo"])
                total += 1
                if not trad.get(o["conteudo"]) and o["conteudo"] not in manter:
                    faltam += 1
                    print("FALTA:", repr(o["conteudo"])[:100], f"({os.path.basename(arq)}:{o['linha']})")
    print(f"[{motor}] strings de interface: {total}; traduzidas ou mantidas de propósito: "
          f"{total - faltam}; NOVAS sem tradução: {faltam}")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[2] not in MOTORES:
        print(__doc__)
        sys.exit(1)
    {"extrair": cmd_extrair, "aplicar": cmd_aplicar, "relatorio": cmd_relatorio}[sys.argv[1]](sys.argv[2])
