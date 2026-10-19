# -*- coding: utf-8 -*-
"""Testes da descriptografia de backups do WhatsApp (python -m unittest discover testes).

Os arquivos cifrados são montados aqui mesmo, no formato do WhatsApp, a partir de um
banco SQLite criado no teste. A compatibilidade com backups reais foi conferida à
parte, byte a byte, contra a ferramenta de referência wa-crypt-tools."""
import hashlib
import hmac
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
import zipfile
import zlib

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quartzo_app import whatsapp_cripto as wa  # noqa: E402

RAIZ = bytes(range(32))
CHAVE_TEXTO = RAIZ.hex()


def _campo(numero, dados):
    return _vint((numero << 3) | 2) + _vint(len(dados)) + dados


def _vint(n):
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def cifrar(claro, formato="crypt15", multiarquivo=False, chave14=None, iv=b"\x11" * 16, extra=b""):
    """Monta um arquivo no formato do WhatsApp (o caminho inverso do módulo).
    `extra` são campos acrescentados ao cabeçalho (por exemplo, o tipo de chave)."""
    meta = _campo(4, _campo(1, b"2.26.1.1") + _campo(2, b"POCO") + _campo(3, b"42"))
    if formato == "crypt15":
        cab = _campo(3, _campo(1, iv)) + meta + extra
        prk = hmac.new(b"\x00" * 32, RAIZ, hashlib.sha256).digest()
        chave = hmac.new(prk, b"backup encryption\x01", hashlib.sha256).digest()
    else:
        cab = _campo(2, _campo(5, iv)) + meta
        chave = chave14[99:131]
    cifrado = AESGCM(chave).encrypt(iv, claro, None)  # dados + tag de 16 bytes
    arq = _vint(len(cab)) + cab + cifrado
    return arq if multiarquivo else arq + hashlib.md5(arq).digest()


def conteudo_key14():
    salt = b"\x22" * 16
    return b"\x00\x01\x01" + b"\x33" * 32 + salt + hashlib.sha256(salt).digest() + b"\x00" * 16 + b"\x44" * 32


def ler(caminho, texto=False):
    with open(caminho, "r" if texto else "rb", **({"encoding": "utf-8-sig"} if texto else {})) as fh:
        return fh.read()


def sha(caminho):
    return hashlib.sha256(ler(caminho)).hexdigest()


def java_bytes(dados):
    return wa.PREFIXO_JAVA_BYTES + len(dados).to_bytes(4, "big") + dados


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="quartzo_wa_")
        db = os.path.join(self.dir, "modelo.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE message (_id INTEGER PRIMARY KEY, text_data TEXT)")
        con.execute("CREATE TABLE chat (_id INTEGER PRIMARY KEY)")
        con.executemany("INSERT INTO message (text_data) VALUES (?)", [(f"msg {i}",) for i in range(50)])
        con.commit()
        con.close()
        with open(db, "rb") as fh:
            self.banco = fh.read()
        os.remove(db)
        self.compactado = zlib.compress(self.banco, 1)
        # imita a pasta de uma extração do Quartzo
        self.evid = os.path.join(self.dir, "20260101_000000_Android_item_teste")
        self.dbs = os.path.join(self.evid, "dump", "sdcard", "Android", "media", "com.whatsapp", "WhatsApp", "Databases")
        os.makedirs(self.dbs)
        with open(os.path.join(self.evid, "QUARTZO_sessao_teste.log"), "w") as fh:
            fh.write("sessao\n")
        self.destino = os.path.join(self.dir, "derivados")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def gravar(self, nome, dados):
        p = os.path.join(self.dbs, nome)
        with open(p, "wb") as fh:
            fh.write(dados)
        return p


class TesteChaves(Base):
    def test_chave_com_espacos_e_maiusculas(self):
        texto = " ".join(CHAVE_TEXTO.upper()[i:i + 4] for i in range(0, 64, 4))
        self.assertEqual(wa.chave_de_texto(texto).impressao_digital(), hashlib.sha256(RAIZ).hexdigest())

    def test_chave_incompleta_ou_com_letra_invalida(self):
        with self.assertRaises(wa.ErroChave):
            wa.chave_de_texto(CHAVE_TEXTO[:63])
        with self.assertRaises(wa.ErroChave):
            wa.chave_de_texto(CHAVE_TEXTO[:63] + "g")

    def test_arquivos_de_chave(self):
        p15 = os.path.join(self.dir, "encrypted_backup.key")
        p14 = os.path.join(self.dir, "key")
        with open(p15, "wb") as fh:
            fh.write(java_bytes(RAIZ))
        with open(p14, "wb") as fh:
            fh.write(java_bytes(conteudo_key14()))
        self.assertEqual(wa.chave_de_arquivo(p15).tipo, "crypt15")
        self.assertEqual(wa.chave_de_arquivo(p14).tipo, "crypt14")
        with open(p14, "wb") as fh:
            fh.write(java_bytes(b"\x00" * 50))
        with self.assertRaises(wa.ErroChave):
            wa.chave_de_arquivo(p14)


class TesteDescriptografia(Base):
    def _um(self, nome, dados, chaves):
        p = self.gravar(nome, dados)
        destino = os.path.join(self.destino, "saida.db")
        return wa.descriptografar(wa.Origem(p), chaves, destino), destino

    def test_crypt15_com_md5_final(self):
        r, destino = self._um("msgstore.db.crypt15", cifrar(self.compactado), [wa.chave_de_texto(CHAVE_TEXTO)])
        with open(destino, "rb") as fh:
            self.assertEqual(fh.read(), self.banco)
        self.assertEqual((r["tag_gcm"], r["md5_whatsapp"], r["reverificacao"]), ("conferida", "conferido", "conferida"))
        self.assertEqual((r["versao_whatsapp"], r["modelo"], r["final_jid"]), ("2.26.1.1", "POCO", "42"))
        self.assertEqual(r["sqlite"]["contagens"]["message"], 50)
        self.assertEqual(r["sha256_destino"], hashlib.sha256(self.banco).hexdigest())
        # o banco foi aberto em modo imutável: nenhum arquivo auxiliar ao lado
        self.assertEqual(sorted(os.listdir(self.destino)), ["saida.db"])

    def test_crypt15_em_varios_arquivos(self):
        r, _ = self._um("msgstore.db.crypt15", cifrar(self.compactado, multiarquivo=True),
                        [wa.chave_de_texto(CHAVE_TEXTO)])
        self.assertEqual(r["tag_gcm"], "conferida")
        self.assertIn("ausente", r["md5_whatsapp"])

    def test_crypt14_com_arquivo_key(self):
        k = wa.Chave("crypt14", conteudo_key14(), "teste")
        r, destino = self._um("msgstore.db.crypt14", cifrar(self.compactado, "crypt14", chave14=conteudo_key14()), [k])
        with open(destino, "rb") as fh:
            self.assertEqual(fh.read(), self.banco)

    def test_crypt14_com_chave_de_64_digitos_explica_o_motivo(self):
        with self.assertRaisesRegex(wa.ErroChave, "files/key"):
            self._um("msgstore.db.crypt14", cifrar(self.compactado, "crypt14", chave14=conteudo_key14()),
                     [wa.chave_de_texto(CHAVE_TEXTO)])

    def test_chave_errada_nao_deixa_arquivo(self):
        with self.assertRaisesRegex(wa.ErroChave, "não corresponde"):
            self._um("msgstore.db.crypt15", cifrar(self.compactado), [wa.chave_de_texto("ab" * 32)])
        self.assertEqual(os.listdir(self.destino), [])

    def test_backup_por_senha_abre_com_a_chave_raiz(self):
        # a senha só protege a chave junto ao servidor; o backup é cifrado com a chave raiz
        senha = _vint(6 << 3) + _vint(2)  # key_type_new = senha
        r, destino = self._um("msgstore.db.crypt15", cifrar(self.compactado, extra=senha),
                              [wa.chave_de_texto(CHAVE_TEXTO)])
        self.assertEqual(r["tag_gcm"], "conferida")
        with open(destino, "rb") as fh:
            self.assertEqual(fh.read(), self.banco)

    def test_chave_errada_em_backup_por_senha_explica_o_motivo(self):
        senha = _vint(6 << 3) + _vint(2)
        with self.assertRaisesRegex(wa.ErroChave, "protegido por senha.*encrypted_backup.key"):
            self._um("msgstore.db.crypt15", cifrar(self.compactado, extra=senha), [wa.chave_de_texto("ab" * 32)])
        self.assertEqual(os.listdir(self.destino), [])

    def test_arquivo_adulterado_e_descartado(self):
        dados = bytearray(cifrar(self.compactado))
        dados[len(dados) // 2] ^= 0x01
        with self.assertRaises(wa.ErroIntegridade):
            self._um("msgstore.db.crypt15", bytes(dados), [wa.chave_de_texto(CHAVE_TEXTO)])
        self.assertEqual(os.listdir(self.destino), [])

    def test_nao_sobrescreve(self):
        os.makedirs(self.destino)
        with open(os.path.join(self.destino, "saida.db"), "wb") as fh:
            fh.write(b"existente")
        with self.assertRaises(wa.ErroWhatsApp):
            self._um("msgstore.db.crypt15", cifrar(self.compactado), [wa.chave_de_texto(CHAVE_TEXTO)])
        with open(os.path.join(self.destino, "saida.db"), "rb") as fh:
            self.assertEqual(fh.read(), b"existente")

    def test_cancelamento_apaga_o_parcial(self):
        parar = threading.Event()
        parar.set()
        p = self.gravar("msgstore.db.crypt15", cifrar(self.compactado))
        with self.assertRaises(InterruptedError):
            wa.descriptografar(wa.Origem(p), [wa.chave_de_texto(CHAVE_TEXTO)],
                               os.path.join(self.destino, "x.db"), cancelar=parar)
        self.assertFalse(os.path.exists(os.path.join(self.destino, "x.db.parcial")))


class TesteLote(Base):
    def test_pasta_da_extracao(self):
        self.gravar("msgstore.db.crypt15", cifrar(self.compactado))
        self.gravar("wa.db.crypt15", cifrar(self.compactado, iv=b"\x05" * 16))
        self.gravar("msgstore.db.crypt14", cifrar(self.compactado, "crypt14", chave14=conteudo_key14()))
        antes = {n: sha(os.path.join(self.dbs, n)) for n in os.listdir(self.dbs)}
        pasta, res, reg = wa.processar(self.evid, self.destino, [wa.chave_de_texto(CHAVE_TEXTO)],
                                       dados_caso={"numero": "teste", "examinador": "teste"})
        situacoes = {os.path.basename(r["relativo"]): r["situacao"] for r in res}
        self.assertEqual(situacoes, {"msgstore.db.crypt15": "OK", "wa.db.crypt15": "OK", "msgstore.db.crypt14": "ERRO"})
        # a ordem do relatório começa pelo msgstore
        self.assertTrue(res[0]["relativo"].endswith("msgstore.db.crypt14") or res[0]["relativo"].endswith("msgstore.db.crypt15"))
        texto = ler(reg, texto=True)
        self.assertIn("2 de 3 arquivo(s)", texto)
        self.assertNotIn(CHAVE_TEXTO, texto)
        self.assertNotIn(CHAVE_TEXTO, ler(reg.replace("registro_", "hashes_").replace(".txt", ".csv"), texto=True))
        # a origem e a pasta da evidência ficaram intactas
        depois = {n: sha(os.path.join(self.dbs, n)) for n in os.listdir(self.dbs)}
        self.assertEqual(antes, depois)
        self.assertTrue(os.path.isfile(os.path.join(pasta, "dump", "sdcard", "Android", "media", "com.whatsapp",
                                                    "WhatsApp", "Databases", "msgstore.db")))
        # o .sha256 confere com os arquivos que descreve
        for linha in ler(reg.replace("registro_", "hashes_").replace(".txt", ".sha256"), texto=True).splitlines():
            h, nome = linha.split("  ", 1)
            self.assertEqual(sha(os.path.join(pasta, nome)), h)

    def test_zip_da_extracao(self):
        zp = os.path.join(self.evid, "extracao.zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("dump/sdcard/Android/media/com.whatsapp.w4b/WhatsApp Business/Databases/msgstore.db.crypt15",
                        cifrar(self.compactado))
            zf.writestr("dump/sdcard/outro.txt", b"nada")
        achados = wa.localizar(zp)
        self.assertEqual(len(achados), 1)
        self.assertEqual(achados[0].pacote, "WhatsApp Business")
        pasta, res, _ = wa.processar(zp, self.destino, [wa.chave_de_texto(CHAVE_TEXTO)])
        self.assertEqual(res[0]["situacao"], "OK")
        self.assertEqual(res[0]["sha256_destino"], hashlib.sha256(self.banco).hexdigest())

    def test_recusa_destino_dentro_da_evidencia(self):
        self.gravar("msgstore.db.crypt15", cifrar(self.compactado))
        with self.assertRaises(wa.ErroWhatsApp):
            wa.processar(os.path.join(self.dbs, "msgstore.db.crypt15"), os.path.join(self.evid, "derivados"),
                         [wa.chave_de_texto(CHAVE_TEXTO)])
        self.assertFalse(os.path.exists(os.path.join(self.evid, "derivados")))

    def test_destino_sugerido_fica_ao_lado_da_extracao(self):
        p = self.gravar("msgstore.db.crypt15", cifrar(self.compactado))
        self.assertEqual(wa.destino_sugerido(p), self.evid + "__WHATSAPP")


if __name__ == "__main__":
    unittest.main()
