# -*- coding: utf-8 -*-
"""Descriptografia dos bancos de backup do WhatsApp para Android (.crypt14 e .crypt15).

O WhatsApp grava em /sdcard/Android/media/com.whatsapp/WhatsApp/Databases (e em
Backups) cópias cifradas dos seus bancos. Há dois formatos em uso:

  crypt15  backup com criptografia de ponta a ponta ativada no aparelho. Abre com a
           chave de 64 dígitos hexadecimais exibida pelo WhatsApp ao ativar o recurso,
           ou com o arquivo files/encrypted_backup.key da pasta interna do app.
  crypt14  criptografia local, sempre presente quando o recurso acima está desligado.
           Só abre com o arquivo files/key da pasta interna do app (exige root ou
           extração de sistema de arquivos completo).

Estrutura de ambos: tamanho do cabeçalho (varint), cabeçalho protobuf com o IV, dados
em AES-256-GCM, tag GCM de 16 bytes e, salvo nos backups em vários arquivos, MD5 de
16 bytes de tudo o que vem antes. O conteúdo decifrado é um fluxo zlib (ou um ZIP,
em alguns backups auxiliares). O formato foi conferido com a implementação de
referência wa-crypt-tools (ElDavoo, GPL-3.0), contra a qual este módulo foi testado.

Garantias deste módulo:
  * nunca grava dentro da pasta da evidência nem altera o arquivo de origem;
  * só dá o arquivo por pronto depois de conferida a tag GCM (até lá ele existe
    apenas como <nome>.parcial e é apagado se a conferência falhar);
  * calcula MD5, SHA-1 e SHA-256 da origem e do resultado, relê o resultado e
    confere o SHA-256 (reverificação);
  * a chave fica apenas na memória: o registro traz só a impressão digital
    (SHA-256 da chave raiz), que permite provar qual chave foi usada sem revelá-la.

Uso pela linha de comando (a chave é pedida sem eco, para não ficar no histórico):
    python -m quartzo_app.whatsapp_cripto <origem> <pasta_destino> [--arquivo-chave F]
A origem pode ser um .crypt14/.crypt15, uma pasta ou o ZIP de uma extração PRFS.

Copyright (C) 2026 Jean Carlos Fonseca. GNU GPL v3 ou posterior. Sem qualquer garantia.
"""
import csv
import getpass
import hashlib
import hmac
import os
import platform
import re
import socket
import sqlite3
import sys
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

try:
    from quartzo_app import VERSAO
except ImportError:  # execução avulsa, fora do pacote
    VERSAO = "?"

BLOCO = 1 << 20
SUFIXOS = (".crypt15", ".crypt14", ".crypt12")
# primeiros bytes possíveis do conteúdo decifrado: cabeçalhos zlib e assinatura de ZIP
INICIOS_ZLIB = (b"\x78\x01", b"\x78\x5e", b"\x78\x9c", b"\x78\xda")
INICIO_ZIP = b"PK\x03\x04"
# serialização Java de um byte[]: é assim que o WhatsApp grava files/key e encrypted_backup.key
PREFIXO_JAVA_BYTES = bytes.fromhex("aced0005757200025b42acf317f8060854e00200007870")
TABELAS_CONTADAS = ("message", "messages", "chat", "chat_list", "jid", "message_media",
                    "call_log", "wa_contacts", "group_participant_user")


class ErroWhatsApp(Exception):
    """Falha que impede a descriptografia de um arquivo."""


class ErroChave(ErroWhatsApp):
    """Chave inválida, do tipo errado ou que não corresponde ao arquivo."""


class ErroFormato(ErroWhatsApp):
    """O arquivo não tem a estrutura esperada de um backup crypt14/crypt15."""


class ErroIntegridade(ErroWhatsApp):
    """A tag GCM ou o fluxo zlib não conferiram: arquivo corrompido ou adulterado."""


# --------------------------------------------------------------------- chaves
class Chave:
    """Chave de backup. `tipo` é 'crypt15' (chave raiz de 32 bytes) ou 'crypt14'
    (conteúdo de 131 bytes do arquivo files/key, cuja chave AES são os 32 finais)."""

    def __init__(self, tipo, material, origem):
        self.tipo = tipo
        self._material = material
        self.origem = origem  # texto para o registro; nunca contém a chave

    def chave_aes(self):
        if self.tipo == "crypt14":
            return self._material[99:131]
        # crypt15: a chave AES deriva da raiz por HMAC-SHA256 com o rótulo
        # "backup encryption" e o contador 1 no fim (HKDF de uma só rodada)
        prk = hmac.new(b"\x00" * 32, self._material, hashlib.sha256).digest()
        return hmac.new(prk, b"backup encryption\x01", hashlib.sha256).digest()

    def impressao_digital(self):
        return hashlib.sha256(self._material).hexdigest()


def chave_de_texto(texto):
    """Lê a chave de 64 dígitos como o WhatsApp a exibe: aceita espaços, hifens,
    quebras de linha e letras maiúsculas ou minúsculas."""
    limpo = re.sub(r"[\s\-]", "", texto or "").lower()
    if len(limpo) != 64:
        raise ErroChave(f"A chave deve ter 64 dígitos hexadecimais; foram informados {len(limpo)}.")
    if not re.fullmatch(r"[0-9a-f]{64}", limpo):
        invalidos = sorted(set(re.sub(r"[0-9a-f]", "", limpo)))
        raise ErroChave("A chave só pode conter os dígitos 0 a 9 e as letras a até f. "
                        f"Caracteres não aceitos: {' '.join(invalidos)}")
    return Chave("crypt15", bytes.fromhex(limpo), "chave de 64 dígitos digitada pelo operador")


def chave_de_arquivo(caminho):
    """Lê files/key (crypt14) ou files/encrypted_backup.key (crypt15)."""
    with open(caminho, "rb") as fh:
        dados = fh.read(4096)
    conteudo = _desserializar_bytes_java(dados)
    if conteudo is None:
        # há ferramentas que exportam a chave crua, sem a serialização Java
        conteudo = dados
    sha = hashlib.sha256(dados).hexdigest()
    nome = os.path.basename(caminho)
    if len(conteudo) == 32:
        return Chave("crypt15", conteudo, f"arquivo {nome} (SHA-256 {sha})")
    if len(conteudo) == 131:
        if hashlib.sha256(conteudo[35:51]).digest() != conteudo[51:83]:
            raise ErroChave(f"{nome}: o conteúdo não confere com o formato do arquivo key do WhatsApp.")
        return Chave("crypt14", conteudo, f"arquivo {nome} (SHA-256 {sha})")
    raise ErroChave(f"{nome}: tamanho de chave não reconhecido ({len(conteudo)} bytes). Esperado: "
                    "32 bytes (encrypted_backup.key, crypt15) ou 131 bytes (key, crypt14).")


def _desserializar_bytes_java(dados):
    # prefixo fixo (classe [B) seguido do tamanho do vetor em 4 bytes
    if not dados.startswith(PREFIXO_JAVA_BYTES) or len(dados) < 27:
        return None
    tamanho = int.from_bytes(dados[23:27], "big")
    conteudo = dados[27:27 + tamanho]
    return conteudo if len(conteudo) == tamanho else None


# ---------------------------------------------------------------- cabeçalho
def _varint(buf, pos):
    valor = desloc = 0
    while True:
        if pos >= len(buf):
            raise ErroFormato("Cabeçalho truncado.")
        b = buf[pos]
        pos += 1
        valor |= (b & 0x7F) << desloc
        if not b & 0x80:
            return valor, pos
        desloc += 7
        if desloc > 63:
            raise ErroFormato("Varint inválido no cabeçalho.")


def _campos_protobuf(buf):
    """Devolve {número: [valores]} de uma mensagem protobuf, sem esquema.
    Campos de tamanho variável vêm como bytes; inteiros, como int."""
    campos, pos = {}, 0
    while pos < len(buf):
        chave, pos = _varint(buf, pos)
        numero, tipo = chave >> 3, chave & 7
        if tipo == 0:
            valor, pos = _varint(buf, pos)
        elif tipo == 2:
            tam, pos = _varint(buf, pos)
            valor, pos = buf[pos:pos + tam], pos + tam
            if len(valor) != tam:
                raise ErroFormato("Campo truncado no cabeçalho.")
        elif tipo == 1:
            valor, pos = buf[pos:pos + 8], pos + 8
        elif tipo == 5:
            valor, pos = buf[pos:pos + 4], pos + 4
        else:
            raise ErroFormato(f"Tipo de campo protobuf não suportado ({tipo}).")
        campos.setdefault(numero, []).append(valor)
    return campos


def ler_cabecalho(fluxo):
    """Lê o cabeçalho no início de `fluxo`. Devolve um dict com formato, iv,
    tamanho do cabeçalho e os metadados que o WhatsApp grava em claro."""
    inicio = fluxo.read(16)
    if not inicio:
        raise ErroFormato("Arquivo vazio.")
    tam, pos = _varint(inicio + b"\x00", 0)
    if tam == 0 or tam > 65536:
        raise ErroFormato("Tamanho de cabeçalho incoerente; o arquivo pode ser crypt12 ou outro formato.")
    resto = fluxo.read(max(0, pos + tam - len(inicio)))
    bruto = (inicio + resto)[pos:pos + tam]
    if len(bruto) != tam:
        raise ErroFormato("Cabeçalho truncado.")
    try:
        campos = _campos_protobuf(bruto)
    except ErroFormato:
        raise ErroFormato("Cabeçalho ilegível; o arquivo pode ser crypt12 ou outro formato.") from None

    info = {"tamanho_cabecalho": pos + tam, "versao_whatsapp": "", "modelo": "", "final_jid": ""}
    if 3 in campos:  # e2ee_key_data -> crypt15
        info["formato"] = "crypt15"
        info["iv"] = _campos_protobuf(campos[3][0]).get(1, [b""])[0]
    elif 2 in campos:  # wa_provided_key_data -> crypt14
        info["formato"] = "crypt14"
        info["iv"] = _campos_protobuf(campos[2][0]).get(5, [b""])[0]
    elif 5 in campos:
        raise ErroFormato("Backup protegido por passkey; este formato não é suportado.")
    else:
        raise ErroFormato("Cabeçalho sem IV; o arquivo pode ser crypt12 ou outro formato.")
    if len(info["iv"]) != 16:
        raise ErroFormato(f"IV com {len(info['iv'])} bytes; o esperado são 16.")
    # key_type_new (campo 6): 2 = senha, 3 = chave de 64 dígitos, 4 = passkey.
    # passkey_encryption_metadata (campo 5) também marca backup por passkey. Servem só
    # para explicar uma chave recusada: o backup é cifrado com a chave raiz em todos os casos.
    info["key_type"] = campos[6][0] if 6 in campos and isinstance(campos[6][0], int) else None
    info["tem_passkey_meta"] = 5 in campos
    if 4 in campos:
        meta = _campos_protobuf(campos[4][0])
        for numero, nome in ((1, "versao_whatsapp"), (2, "modelo"), (3, "final_jid")):
            if numero in meta and isinstance(meta[numero][0], bytes):
                info[nome] = meta[numero][0].decode("utf-8", "replace")
    return info


# ------------------------------------------------------------------ origens
class Origem:
    """Um arquivo cifrado a processar: avulso no disco ou dentro de um ZIP."""

    def __init__(self, caminho, zip_caminho=None, info_zip=None, relativo=None):
        self.caminho = caminho          # no disco, ou nome do membro dentro do ZIP
        self.zip_caminho = zip_caminho
        self.info_zip = info_zip
        self.relativo = relativo or os.path.basename(caminho)

    @property
    def tamanho(self):
        return self.info_zip.file_size if self.info_zip else os.path.getsize(self.caminho)

    @property
    def descricao(self):
        if self.zip_caminho:
            return f"{self.zip_caminho} -> {self.caminho}"
        return self.caminho

    @property
    def pacote(self):
        p = self.relativo.replace("\\", "/").lower()
        if "com.whatsapp.w4b" in p or "whatsapp business" in p:
            return "WhatsApp Business"
        if "com.whatsapp" in p or "/whatsapp/" in p or p.startswith("whatsapp/"):
            return "WhatsApp"
        return "não identificado"

    def abrir(self, zf=None):
        if self.zip_caminho:
            return zf.open(self.info_zip)
        return open(self.caminho, "rb")


def localizar(origem):
    """Lista os .crypt14/.crypt15/.crypt12 em um arquivo, numa pasta ou num ZIP."""
    origem = os.path.abspath(origem)
    achados = []
    if os.path.isfile(origem) and zipfile.is_zipfile(origem) and not origem.lower().endswith(SUFIXOS):
        with zipfile.ZipFile(origem) as zf:
            for info in zf.infolist():
                if not info.is_dir() and info.filename.lower().endswith(SUFIXOS):
                    achados.append(Origem(info.filename, origem, info, info.filename))
    elif os.path.isfile(origem):
        achados.append(Origem(origem, relativo=os.path.basename(origem)))
    elif os.path.isdir(origem):
        for base, dirs, nomes in os.walk(origem):
            dirs.sort()
            for n in sorted(nomes):
                if n.lower().endswith(SUFIXOS):
                    p = os.path.join(base, n)
                    achados.append(Origem(p, relativo=os.path.relpath(p, origem)))
    else:
        raise FileNotFoundError(origem)
    # msgstore primeiro, depois wa.db, depois os demais
    def ordem(o):
        n = os.path.basename(o.relativo).lower()
        return (0 if n.startswith("msgstore.db") else 1 if n.startswith("msgstore") else
                2 if n.startswith("wa.db") else 3, o.relativo.lower())
    return sorted(achados, key=ordem)


# --------------------------------------------------------------- segurança
def _dentro_de(caminho, pasta):
    a = os.path.normcase(os.path.abspath(caminho))
    b = os.path.normcase(os.path.abspath(pasta))
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:
        return False


def pasta_da_evidencia(caminho):
    """Sobe a partir de `caminho` até achar a pasta de uma extração (a que tem o
    QUARTZO_sessao_*.log, um .ufd ou um .case.json). None se não achar."""
    atual = os.path.abspath(caminho if os.path.isdir(caminho) else os.path.dirname(caminho))
    while True:
        try:
            nomes = os.listdir(atual)
        except OSError:
            nomes = []
        if any(n.startswith("QUARTZO_sessao_") or n.lower().endswith((".ufd", ".case.json")) for n in nomes):
            return atual
        acima = os.path.dirname(atual)
        if acima == atual:
            return None
        atual = acima


def destino_sugerido(origem):
    """Pasta irmã da extração, no mesmo padrão do selo de hashes (<pasta>__WHATSAPP)."""
    base = pasta_da_evidencia(origem)
    if base is None:
        base = os.path.abspath(origem if os.path.isdir(origem) else os.path.dirname(origem))
    return os.path.join(os.path.dirname(base), os.path.basename(base) + "__WHATSAPP")


def conferir_destino(origem, destino):
    """Levanta ErroWhatsApp se gravar em `destino` alteraria a pasta da evidência."""
    origem = os.path.abspath(origem)
    proibidas = [origem if os.path.isdir(origem) else os.path.dirname(origem)]
    evid = pasta_da_evidencia(origem)
    if evid:
        proibidas.append(evid)
    for p in proibidas:
        if _dentro_de(destino, p):
            raise ErroWhatsApp(f"O destino fica dentro da pasta da evidência ({p}). "
                               "Escolha uma pasta fora dela; a evidência não pode receber arquivos.")


# ------------------------------------------------------- descriptografia
def _hashes():
    return hashlib.md5(), hashlib.sha1(), hashlib.sha256()


def _examinar(origem, zf, cancelar):
    """1ª leitura: hashes da origem, MD5 que o WhatsApp grava no fim e os 32 bytes finais."""
    tam = origem.tamanho
    if tam < 64:
        raise ErroFormato("Arquivo pequeno demais para ser um backup.")
    md5, sha1, sha256 = _hashes()
    md5_wa = hashlib.md5()
    lidos, cauda = 0, b""
    with origem.abrir(zf) as fh:
        while True:
            if cancelar is not None and cancelar.is_set():
                raise InterruptedError
            b = fh.read(BLOCO)
            if not b:
                break
            for h in (md5, sha1, sha256):
                h.update(b)
            limite = tam - 16 - lidos
            if limite > 0:
                md5_wa.update(b[:limite])
            lidos += len(b)
            cauda = (cauda + b)[-32:]
    if lidos != tam:
        raise ErroFormato(f"Foram lidos {lidos} de {tam} bytes.")
    return {"md5": md5.hexdigest(), "sha1": sha1.hexdigest(), "sha256": sha256.hexdigest(),
            "md5_confere": md5_wa.digest() == cauda[16:], "cauda": cauda}


def _tipo_conteudo(inicio):
    if inicio.startswith(b"SQLite format 3\x00"):
        return "SQLite"
    if inicio.startswith(INICIO_ZIP):
        return "ZIP"
    if inicio.lstrip()[:1] in (b"{", b"["):
        return "JSON"
    return "desconhecido"


def _resumo_sqlite(caminho):
    """Abre o banco só para leitura, sem criar -wal nem -journal ao lado dele."""
    uri = Path(caminho).resolve().as_uri() + "?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    try:
        cheque = con.execute("PRAGMA quick_check").fetchone()[0]
        tabelas = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        contagens = {}
        for t in TABELAS_CONTADAS:
            if t in tabelas:
                contagens[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        return {"quick_check": cheque, "tabelas": len(tabelas), "contagens": contagens}
    finally:
        con.close()


def _dica_protecao(cab):
    """Complemento da mensagem de chave errada quando o cabeçalho indica senha ou passkey.

    A senha e a passkey só protegem a chave raiz junto aos servidores do WhatsApp; o backup
    continua cifrado com a mesma chave raiz, que fica em files/encrypted_backup.key."""
    if cab.get("tem_passkey_meta") or cab.get("key_type") == 4:
        motivo = "por passkey"
    elif cab.get("key_type") == 2:
        motivo = "por senha"
    else:
        return ""
    return (f" O cabeçalho indica backup protegido {motivo}: nesse modo o WhatsApp não exibe a chave de "
            "64 dígitos, e a senha ou a passkey não decifram o backup (só protegem a chave junto aos "
            "servidores do WhatsApp). O backup abre com o arquivo files/encrypted_backup.key da pasta "
            "interna do app (/data/data/com.whatsapp/), que guarda a chave raiz.")


def descriptografar(origem, chaves, destino_arquivo, zf=None, progresso=None, cancelar=None):
    """Descriptografa uma Origem para `destino_arquivo`. Devolve dict com o resultado.

    `chaves` é uma lista de Chave; usa-se a do mesmo tipo do arquivo."""
    if os.path.exists(destino_arquivo):
        raise ErroWhatsApp(f"Já existe um arquivo em {destino_arquivo}; nada foi sobrescrito.")
    res = {"origem": origem.descricao, "relativo": origem.relativo, "pacote": origem.pacote,
           "tamanho_origem": origem.tamanho, "destino": destino_arquivo}

    with origem.abrir(zf) as fh:
        cab = ler_cabecalho(fh)
    res.update({k: cab[k] for k in ("formato", "versao_whatsapp", "modelo", "final_jid")})
    chave = next((c for c in chaves if c.tipo == cab["formato"]), None)
    if chave is None:
        if cab["formato"] == "crypt14":
            raise ErroChave("O arquivo é crypt14 (criptografia local). Ele só abre com o arquivo key da "
                            "pasta interna do WhatsApp (/data/data/com.whatsapp/files/key); a chave de "
                            "64 dígitos vale apenas para backups crypt15.")
        raise ErroChave("O arquivo é crypt15. Informe a chave de 64 dígitos ou o arquivo encrypted_backup.key.")
    res["chave"] = chave.origem
    res["impressao_chave"] = chave.impressao_digital()

    ex = _examinar(origem, zf, cancelar)
    res.update({"md5_origem": ex["md5"], "sha1_origem": ex["sha1"], "sha256_origem": ex["sha256"]})
    # Com o MD5 final conferindo, os 32 bytes do fim são tag + MD5. Nos backups em
    # vários arquivos não há MD5 e os 16 bytes finais já são a tag.
    if ex["md5_confere"]:
        fim_dados, tag = origem.tamanho - 32, ex["cauda"][:16]
        res["md5_whatsapp"] = "conferido"
    else:
        fim_dados, tag = origem.tamanho - 16, ex["cauda"][16:]
        res["md5_whatsapp"] = "ausente (backup em vários arquivos) ou divergente; vale a tag GCM"
    a_ler = fim_dados - cab["tamanho_cabecalho"]
    if a_ler <= 0:
        raise ErroFormato("Não há dados cifrados depois do cabeçalho.")

    chave_aes = chave.chave_aes()
    os.makedirs(os.path.dirname(destino_arquivo), exist_ok=True)
    parcial = destino_arquivo + ".parcial"
    md5, sha1, sha256 = _hashes()
    escritos, inicio_saida = 0, b""
    try:
        dec = Cipher(algorithms.AES(chave_aes), modes.GCM(cab["iv"])).decryptor()
        z = zlib.decompressobj()
        modo = None
        with origem.abrir(zf) as fh, open(parcial, "xb") as out:
            fh.read(cab["tamanho_cabecalho"])
            restante = a_ler
            while restante:
                if cancelar is not None and cancelar.is_set():
                    raise InterruptedError
                bloco = fh.read(min(BLOCO, restante))
                if not bloco:
                    raise ErroFormato("O arquivo terminou antes do previsto.")
                restante -= len(bloco)
                claro = dec.update(bloco)
                if modo is None:
                    # o primeiro bloco já mostra se a chave é a certa: com chave errada o
                    # resultado é ruído e não começa por zlib nem por ZIP
                    if claro[:2] in INICIOS_ZLIB:
                        modo = "zlib"
                    elif claro.startswith(INICIO_ZIP):
                        modo = "zip"
                    else:
                        raise ErroChave("A chave não corresponde a este arquivo (o conteúdo decifrado não "
                                        "tem a estrutura de um backup do WhatsApp)." + _dica_protecao(cab))
                if modo == "zlib":
                    try:
                        claro = z.decompress(claro)
                    except zlib.error as e:
                        raise ErroIntegridade(f"Fluxo zlib inválido: {e}") from None
                if claro:
                    if len(inicio_saida) < 64:
                        inicio_saida += claro[:64 - len(inicio_saida)]
                    out.write(claro)
                    for h in (md5, sha1, sha256):
                        h.update(claro)
                    escritos += len(claro)
                if progresso:
                    progresso(a_ler - restante, a_ler)
            if modo == "zlib":
                cauda = z.flush()
                if cauda:
                    out.write(cauda)
                    for h in (md5, sha1, sha256):
                        h.update(cauda)
                    escritos += len(cauda)
            try:
                dec.finalize_with_tag(tag)
            except InvalidTag:
                raise ErroIntegridade("A tag de autenticação GCM não conferiu: o arquivo está corrompido "
                                      "ou foi alterado. O resultado foi descartado.") from None
            if modo == "zlib" and not z.eof:
                raise ErroIntegridade("O fluxo zlib terminou incompleto. O resultado foi descartado.")
        os.replace(parcial, destino_arquivo)
    except BaseException:
        try:
            os.remove(parcial)
        except OSError:
            pass
        raise

    res.update({"tag_gcm": "conferida", "compressao": modo, "tamanho_destino": escritos,
                "md5_destino": md5.hexdigest(), "sha1_destino": sha1.hexdigest(),
                "sha256_destino": sha256.hexdigest(), "tipo": _tipo_conteudo(inicio_saida),
                "zlib_sobra": len(z.unused_data) if modo == "zlib" else 0})
    # reverificação: relê o arquivo gravado e confere o SHA-256
    rel = hashlib.sha256()
    with open(destino_arquivo, "rb") as fh:
        for b in iter(lambda: fh.read(BLOCO), b""):
            rel.update(b)
    res["reverificacao"] = "conferida" if rel.hexdigest() == res["sha256_destino"] else "DIVERGENTE"
    if res["tipo"] == "SQLite":
        try:
            res["sqlite"] = _resumo_sqlite(destino_arquivo)
        except sqlite3.Error as e:
            res["sqlite"] = {"erro": str(e)}
    return res


def nome_destino(relativo):
    """Caminho relativo do resultado: o da origem, sem o sufixo .cryptNN e sem
    caracteres que o Windows não aceita."""
    partes = [re.sub(r'[<>:"|?*]', "-", p) for p in relativo.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    ultimo = partes[-1]
    for s in SUFIXOS:
        if ultimo.lower().endswith(s):
            ultimo = ultimo[:-len(s)]
            break
    partes[-1] = ultimo or "sem_nome"
    return os.path.join(*partes)


# ------------------------------------------------------------------- lote
def processar(origem, destino_base, chaves, selecionados=None, dados_caso=None,
              progresso=None, cancelar=None):
    """Descriptografa os arquivos de `origem` numa subpasta nova de `destino_base`.

    Grava, além dos resultados, registro_whatsapp_<data_hora>.txt, o CSV de hashes
    e o arquivo .sha256 com o hash dos dois. Devolve (pasta, resultados, registro)."""
    conferir_destino(origem, destino_base)
    inicio = datetime.now().astimezone()
    carimbo = inicio.strftime("%Y%m%d_%H%M%S")
    pasta = os.path.join(os.path.abspath(destino_base), f"WhatsApp_{carimbo}")
    os.makedirs(pasta, exist_ok=False)
    itens = selecionados if selecionados is not None else localizar(origem)
    resultados = []
    zip_aberto = None
    try:
        for i, item in enumerate(itens, 1):
            if cancelar is not None and cancelar.is_set():
                break
            if item.zip_caminho and zip_aberto is None:
                zip_aberto = zipfile.ZipFile(item.zip_caminho)
            prog = (lambda feitos, total, i=i, item=item: progresso(i, len(itens), item, feitos, total)) \
                if progresso else None
            dest = os.path.join(pasta, nome_destino(item.relativo))
            try:
                if item.relativo.lower().endswith(".crypt12"):
                    raise ErroFormato("Formato crypt12 (versões antigas do WhatsApp) não suportado por este módulo.")
                r = descriptografar(item, chaves, dest, zip_aberto, prog, cancelar)
                r["situacao"] = "OK"
            except InterruptedError:
                resultados.append({"origem": item.descricao, "relativo": item.relativo, "pacote": item.pacote,
                                   "situacao": "CANCELADO", "erro": "interrompido pelo operador"})
                break
            except (ErroWhatsApp, OSError, zipfile.BadZipFile) as e:
                r = {"origem": item.descricao, "relativo": item.relativo, "pacote": item.pacote,
                     "situacao": "ERRO", "erro": str(e)}
            resultados.append(r)
    finally:
        if zip_aberto is not None:
            zip_aberto.close()
    fim = datetime.now().astimezone()
    registro = _gravar_relatorio(pasta, carimbo, origem, chaves, resultados, inicio, fim, dados_caso)
    return pasta, resultados, registro


def _gravar_relatorio(pasta, carimbo, origem, chaves, resultados, inicio, fim, dados_caso):
    import cryptography
    csv_p = os.path.join(pasta, f"hashes_whatsapp_{carimbo}.csv")
    colunas = ("situacao", "pacote", "formato", "origem", "tamanho_origem", "md5_origem", "sha1_origem",
               "sha256_origem", "destino", "tamanho_destino", "md5_destino", "sha1_destino",
               "sha256_destino", "tag_gcm", "reverificacao", "erro")
    with open(csv_p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(colunas)
        for r in resultados:
            linha = dict(r)
            if linha.get("destino"):
                linha["destino"] = os.path.relpath(linha["destino"], pasta)
            w.writerow([linha.get(c, "") for c in colunas])

    caso = dados_caso or {}
    ok = sum(1 for r in resultados if r["situacao"] == "OK")
    linhas = [
        "=" * 78,
        f"Quartzo {VERSAO} - Descriptografia de backups do WhatsApp (crypt14/crypt15)",
        "REGISTRO DE PROCESSAMENTO",
        "=" * 78,
        f"Início (local) .........: {inicio.isoformat(timespec='seconds')}",
        f"Início (UTC) ...........: {inicio.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"Término (local) ........: {fim.isoformat(timespec='seconds')}",
        f"Nº do procedimento .....: {caso.get('numero', '')}",
        f"Nome do caso ...........: {caso.get('nome', '')}",
        f"Nº do item / lacre .....: {caso.get('evidencia', '')}",
        f"Responsável ............: {caso.get('examinador', '')}",
        f"Usuário do sistema .....: {getpass.getuser()}",
        f"Computador .............: {socket.gethostname()}",
        f"Sistema operacional ....: {platform.platform()}",
        f"Python / cryptography ..: {platform.python_version()} / {cryptography.__version__}",
        f"Origem .................: {os.path.abspath(origem)}",
        f"Pasta de resultados ....: {pasta}",
    ]
    for c in chaves:
        linhas.append(f"Chave ({c.tipo}) ........: {c.origem}")
        linhas.append(f"    impressão digital (SHA-256 da chave raiz): {c.impressao_digital()}")
    linhas += [
        "    A chave em si não é gravada neste registro nem em nenhum outro arquivo.",
        f"Resultado ..............: {ok} de {len(resultados)} arquivo(s) descriptografado(s)",
        "-" * 78,
        "Procedimento: cada arquivo é lido duas vezes. Na primeira leitura são calculados MD5, SHA-1 e",
        "SHA-256 da origem e conferido o MD5 que o WhatsApp grava no fim do arquivo. Na segunda, os dados",
        "são decifrados (AES-256-GCM, com o IV do cabeçalho), descompactados (zlib) e gravados como",
        "<nome>.parcial; o arquivo só recebe o nome definitivo depois de conferida a tag de autenticação",
        "GCM, que prova que o conteúdo decifrado é exatamente o que o WhatsApp cifrou. Em seguida o",
        "resultado é relido e o SHA-256 conferido. Bancos SQLite são abertos só para leitura (modo",
        "immutable), sem criação de arquivos auxiliares. A origem nunca é alterada.",
        "-" * 78,
    ]
    for n, r in enumerate(resultados, 1):
        linhas.append(f"[{n}] {r['situacao']} - {r['relativo']}  ({r['pacote']})")
        linhas.append(f"    origem ............: {r['origem']}")
        if r["situacao"] != "OK":
            linhas.append(f"    motivo ............: {r.get('erro', '')}")
            if r.get("sha256_origem"):
                linhas.append(f"    SHA-256 da origem .: {r['sha256_origem']}")
            linhas.append("")
            continue
        linhas += [
            f"    formato ...........: {r['formato']}  |  WhatsApp {r['versao_whatsapp'] or '?'}"
            f"  |  aparelho {r['modelo'] or '?'}  |  final do número {r['final_jid'] or '?'}",
            f"    tamanho / MD5 .....: {r['tamanho_origem']} bytes  |  {r['md5_origem']}",
            f"    SHA-1 / SHA-256 ...: {r['sha1_origem']}  |  {r['sha256_origem']}",
            f"    MD5 do WhatsApp ...: {r['md5_whatsapp']}",
            f"    tag GCM ...........: {r['tag_gcm']}",
            f"    resultado .........: {os.path.relpath(r['destino'], pasta)}  ({r['tipo']}, "
            f"{r['tamanho_destino']} bytes)",
            f"    MD5 / SHA-1 .......: {r['md5_destino']}  |  {r['sha1_destino']}",
            f"    SHA-256 ...........: {r['sha256_destino']}",
            f"    reverificação .....: {r['reverificacao']}",
        ]
        if r.get("zlib_sobra"):
            linhas.append(f"    atenção ...........: {r['zlib_sobra']} byte(s) após o fim do fluxo zlib")
        sq = r.get("sqlite")
        if sq:
            if "erro" in sq:
                linhas.append(f"    SQLite ............: não abriu ({sq['erro']})")
            else:
                cont = ", ".join(f"{t}={q}" for t, q in sq["contagens"].items()) or "nenhuma tabela conhecida"
                linhas.append(f"    SQLite ............: quick_check {sq['quick_check']}  |  {sq['tabelas']} tabela(s)"
                              f"  |  {cont}")
        linhas.append("")
    linhas += [
        "-" * 78,
        "Para o IPED: acrescente esta pasta ao caso como nova fonte de dados. O parser do WhatsApp",
        "reconhece msgstore.db e wa.db pela assinatura e vincula as mensagens às mídias já indexadas.",
        "=" * 78,
    ]
    reg_p = os.path.join(pasta, f"registro_whatsapp_{carimbo}.txt")
    with open(reg_p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(linhas) + "\n")
    selo = os.path.join(pasta, f"hashes_whatsapp_{carimbo}.sha256")
    with open(selo, "w", encoding="utf-8", newline="\n") as fh:
        for p in (csv_p, reg_p):
            h = hashlib.sha256()
            with open(p, "rb") as a:
                h.update(a.read())
            fh.write(f"{h.hexdigest()}  {os.path.basename(p)}\n")
    return reg_p


# --------------------------------------------------------- linha de comando
def _principal(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        prog="python -m quartzo_app.whatsapp_cripto",
        description="Descriptografa backups do WhatsApp (crypt14/crypt15) para uma pasta fora da evidência.")
    ap.add_argument("origem", help="arquivo .crypt14/.crypt15, pasta ou ZIP da extração")
    ap.add_argument("destino", nargs="?", help="pasta onde criar WhatsApp_<data_hora> (padrão: <extração>__WHATSAPP)")
    ap.add_argument("--arquivo-chave", action="append", default=[],
                    help="files/key (crypt14) ou files/encrypted_backup.key (crypt15); pode repetir")
    ap.add_argument("--sem-chave-digitada", action="store_true",
                    help="não pede a chave de 64 dígitos (use só os arquivos de chave)")
    ap.add_argument("--listar", action="store_true", help="só lista os arquivos encontrados")
    a = ap.parse_args(argv)

    achados = localizar(a.origem)
    for o in achados:
        print(f"  {o.tamanho:>14,} bytes  {o.relativo}".replace(",", "."))
    print(f"{len(achados)} arquivo(s) cifrado(s) encontrado(s).")
    if a.listar or not achados:
        return 0
    chaves = [chave_de_arquivo(p) for p in a.arquivo_chave]
    if not a.sem_chave_digitada:
        texto = getpass.getpass("Chave de 64 dígitos (Enter para pular): ")
        if texto.strip():
            chaves.append(chave_de_texto(texto))
    if not chaves:
        print("Nenhuma chave informada.")
        return 2
    destino = a.destino or destino_sugerido(a.origem)

    def prog(i, n, item, feitos, total):
        print(f"\r[{i}/{n}] {os.path.basename(item.relativo)[:50]:<50} {feitos * 100 // max(total, 1):3d}%",
              end="", flush=True)

    pasta, res, reg = processar(a.origem, destino, chaves, achados, progresso=prog)
    print()
    for r in res:
        print(f"{r['situacao']:<9} {r['relativo']}" + (f"  -> {r.get('erro')}" if r["situacao"] != "OK" else ""))
    print(f"Registro: {reg}")
    return 0 if all(r["situacao"] == "OK" for r in res) else 1


if __name__ == "__main__":
    sys.exit(_principal())
