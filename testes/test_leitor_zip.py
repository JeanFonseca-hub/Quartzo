# -*- coding: utf-8 -*-
"""Testes do leitor de ZIP em gravação e do medidor da janela de acompanhamento
(python -m unittest discover testes)."""
import io
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quartzo_app.leitor_zip import LeitorZip  # noqa: E402
from quartzo_app.progresso import formatar_bytes, formatar_decimal, formatar_inteiro  # noqa: E402


def zip_em_bytes(entradas):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for nome, dados in entradas:
            zf.writestr(nome, dados)
    return buf.getvalue()


class TesteLeitorZip(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="quartzo_teste_")
        self.caminho = os.path.join(self.dir, "x.zip")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def ler_tudo(self):
        leitor = LeitorZip(self.caminho)
        while leitor.atualizar():
            pass
        return leitor

    def test_zip_fechado_confere_com_zipfile(self):
        interno = zip_em_bytes([("a.txt", b"interno")])  # arquivo do aparelho que também é ZIP
        entradas = [("dump/sdcard/", b"")] + [(f"dump/sdcard/f{i}.bin", os.urandom(i * 37)) for i in range(200)]
        entradas.append(("dump/sdcard/app.apk", interno))
        with open(self.caminho, "wb") as fh:
            fh.write(zip_em_bytes(entradas))
        leitor = self.ler_tudo()
        self.assertEqual(leitor.arquivos, 201)
        self.assertEqual(leitor.pastas, 1)
        self.assertTrue(leitor.finalizado)
        self.assertFalse(leitor.desistiu)
        self.assertEqual(leitor.bytes_dados, sum(len(d) for n, d in entradas if not n.endswith("/")))

    def test_cabecalho_provisorio_nao_e_contado(self):
        # segunda entrada com o cabeçalho ainda provisório (tamanho compactado 0)
        # e conteúdo que começa com PK\x03\x04: não pode ser contada nem "atravessada"
        interno = zip_em_bytes([("dentro.txt", b"y" * 50)])
        bruto = bytearray(zip_em_bytes([("um.txt", b"x" * 10), ("dois.apk", interno)]))
        pos2 = bruto.find(b"PK\x03\x04", 1)
        struct.pack_into("<I", bruto, pos2 + 18, 0)  # tamanho compactado = 0
        fim_dados = pos2 + 30 + len("dois.apk") + len(interno)
        with open(self.caminho, "wb") as fh:
            fh.write(bruto[:fim_dados])  # ZIP ainda aberto, sem diretório central
        leitor = self.ler_tudo()
        self.assertEqual(leitor.arquivos, 1)
        self.assertEqual(leitor.pos, pos2)
        self.assertFalse(leitor.desistiu)

    def test_leitura_concorrente_nunca_conta_a_mais(self):
        interno = zip_em_bytes([("i.txt", b"z" * 20)])
        total = 400
        gravadas = [0]
        terminou = threading.Event()

        def gravar():
            with zipfile.ZipFile(self.caminho, "w", compression=zipfile.ZIP_STORED) as zf:
                for i in range(total):
                    dados = interno if i % 25 == 0 else os.urandom((i * 131) % 5000)
                    zf.writestr(f"dump/sdcard/{i}.dat", dados)
                    gravadas[0] = i + 1
                    if i % 10 == 0:
                        time.sleep(0.002)
            terminou.set()

        threading.Thread(target=gravar, daemon=True).start()
        while not os.path.exists(self.caminho):
            time.sleep(0.001)
        leitor = LeitorZip(self.caminho)
        anterior = 0
        while not terminou.is_set():
            leitor.atualizar()
            self.assertLessEqual(leitor.arquivos, gravadas[0])
            self.assertGreaterEqual(leitor.arquivos, anterior)
            anterior = leitor.arquivos
        while leitor.atualizar():
            pass
        self.assertEqual(leitor.arquivos, total)
        self.assertFalse(leitor.desistiu)

    def test_descritor_posterior_faz_desistir_sem_inventar(self):
        with open(self.caminho, "wb") as fh:
            with zipfile.ZipFile(fh, "w") as zf:  # arquivo não-pesquisável força o descritor
                pass
        bruto = bytearray(zip_em_bytes([("a.txt", b"abc")]))
        struct.pack_into("<H", bruto, 6, 0x08)
        with open(self.caminho, "wb") as fh:
            fh.write(bruto)
        leitor = self.ler_tudo()
        self.assertTrue(leitor.desistiu)
        self.assertEqual(leitor.arquivos, 0)


class TesteFormatos(unittest.TestCase):
    def test_numeros_brasileiros(self):
        self.assertEqual(formatar_decimal(12.345), "12,3")
        self.assertEqual(formatar_decimal(1234.5), "1.234,5")
        self.assertEqual(formatar_inteiro(117813), "117.813")
        self.assertEqual(formatar_bytes(32 * 1024), "32,0 KB")
        self.assertEqual(formatar_bytes(41071828704), "38,3 GB")


class TesteMedidor(unittest.TestCase):
    def test_ritmo_e_media(self):
        from quartzo_app.acompanhamento import MedidorPasta
        d = tempfile.mkdtemp(prefix="quartzo_teste_")
        try:
            m = MedidorPasta(d)
            m.parar()
            time.sleep(0.2)
            # 60 s, 12 arquivos/s de 32 KB
            m.historico = [(1000.0 + t, t * 12 * 32768, t * 12) for t in range(0, 61, 5)]
            self.assertAlmostEqual(m.ritmo_itens(), 12.0, places=3)
            self.assertAlmostEqual(m.media_recente(), 32768, places=1)
            self.assertTrue(m.houve_atividade())
            m.historico = [(1000.0 + t, 500, 7) for t in range(0, 61, 5)]
            self.assertFalse(m.houve_atividade())
            self.assertIsNone(m.media_recente())
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
