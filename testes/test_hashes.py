# -*- coding: utf-8 -*-
"""Testes do manifesto de hashes, da reverificação e do registro
(python -m unittest discover testes)."""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quartzo_app import hashes  # noqa: E402


class TesteManifesto(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="quartzo_teste_")
        os.makedirs(os.path.join(self.dir, "sub"))
        for nome, dados in (("a.bin", b"abc"), ("sub/b.txt", b"x" * 5000), ("QUARTZO_sessao_1.log", b"linha\n")):
            with open(os.path.join(self.dir, *nome.split("/")), "wb") as fh:
                fh.write(dados)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_manifesto_reverificacao_e_registro(self):
        csv_p, selo, n, tot = hashes.gerar_manifesto(self.dir)
        self.assertEqual(n, 3)
        rev = hashes.reverificar_manifesto(csv_p)
        self.assertEqual(rev, (3, 3, []))
        reg = hashes.gravar_registro(csv_p, selo, n, tot, rev, datetime.now().astimezone(),
                                     datetime.now().astimezone(), os.path.join(self.dir, "QUARTZO_sessao_1.log"))
        texto = open(reg, encoding="utf-8").read()
        self.assertIn("3 de 3 arquivo(s) íntegro(s)", texto)
        self.assertIn("QUARTZO_sessao_1.log (SHA-256", texto)
        self.assertIn("encerrado antes do cálculo", texto)
        linhas = open(selo, encoding="utf-8").read().splitlines()
        self.assertEqual(len(linhas), 2)
        for linha in linhas:
            h, nome = linha.split("  ", 1)
            with open(os.path.join(self.dir, nome), "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), h)
        # a reverificação acusa alteração posterior
        with open(os.path.join(self.dir, "a.bin"), "wb") as fh:
            fh.write(b"abd")
        total, ok, div = hashes.reverificar_manifesto(csv_p)
        self.assertEqual((total, ok), (3, 2))
        self.assertEqual(div[0][0], "a.bin")
        # um novo manifesto não inclui o registro nem o selo anteriores
        csv2 = hashes.gerar_manifesto(self.dir)[0]
        self.assertNotIn("registro", open(csv2, encoding="utf-8-sig").read())

    def test_manifesto_em_pasta_irma(self):
        # Selagem no ato da coleta: o manifesto vai para fora da evidência e a
        # pasta de evidência não recebe nenhum arquivo novo.
        antes = set(os.listdir(self.dir))
        saida = tempfile.mkdtemp(prefix="quartzo_selo_")
        try:
            csv_p, selo, n, tot = hashes.gerar_manifesto(self.dir, pasta_saida=saida)
            self.assertEqual(n, 3)
            self.assertEqual(os.path.dirname(os.path.abspath(csv_p)), os.path.abspath(saida))
            self.assertEqual(set(os.listdir(self.dir)), antes)  # evidência intocada
            rev = hashes.reverificar_manifesto(csv_p, pasta=self.dir)
            self.assertEqual(rev, (3, 3, []))
            reg = hashes.gravar_registro(csv_p, selo, n, tot, rev, datetime.now().astimezone(),
                                         datetime.now().astimezone(), pasta_evidencia=self.dir,
                                         dados_caso={"numero": "IP 42/2026", "examinador": "Fulano"})
            texto = open(reg, encoding="utf-8").read()
            self.assertIn(os.path.abspath(self.dir), texto)
            self.assertIn("3 de 3 arquivo(s) íntegro(s)", texto)
            self.assertIn("IP 42/2026", texto)  # dados do caso no registro
            self.assertIn("Fulano", texto)
            conf = hashes.gravar_conferencia(csv_p, self.dir, rev, datetime.now().astimezone(),
                                             datetime.now().astimezone())
            self.assertIn("conferem", open(conf, encoding="utf-8").read())
        finally:
            shutil.rmtree(saida, ignore_errors=True)

    def test_dentro_de(self):
        sub = os.path.join(self.dir, "sub")
        self.assertTrue(hashes.dentro_de(sub, self.dir))
        self.assertTrue(hashes.dentro_de(self.dir, self.dir))
        self.assertFalse(hashes.dentro_de(self.dir + "__SELO", self.dir))  # irmã com prefixo igual
        self.assertFalse(hashes.dentro_de(os.path.dirname(self.dir), self.dir))
        raiz = os.path.splitdrive(os.path.abspath(self.dir))[0] + os.sep
        self.assertTrue(hashes.dentro_de(os.path.join(raiz, "__SELO"), raiz))  # raiz de unidade


if __name__ == "__main__":
    unittest.main()
