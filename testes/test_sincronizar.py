# -*- coding: utf-8 -*-
"""Testes da atualização dos motores (ferramentas/sincronizar.py).
python -m unittest discover testes"""
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "ferramentas"))
import sincronizar  # noqa: E402
import traduzir  # noqa: E402


def gravar(caminho, texto):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as fh:
        fh.write(texto)


def ler(caminho):
    with open(caminho, encoding="utf-8") as fh:
        return fh.read()


def somente_leitura(caminho):
    os.chmod(caminho, stat.S_IREAD)


class TesteApagar(unittest.TestCase):
    def test_apaga_pasta_com_arquivo_somente_leitura(self):
        base = tempfile.mkdtemp(prefix="quartzo_teste_")
        pacote = os.path.join(base, ".git", "objects", "pack", "pack-x.pack")
        gravar(pacote, "dados")
        somente_leitura(pacote)
        sincronizar.apagar(base)
        self.assertFalse(os.path.exists(base))

    def test_caminho_inexistente_nao_e_erro(self):
        sincronizar.apagar(os.path.join(tempfile.gettempdir(), "quartzo_pasta_que_nao_existe"))


class TesteAtualizacao(unittest.TestCase):
    """Simula o download com um clone que deixa pacotes do Git somente leitura,
    como acontece no Windows, sem acessar a rede."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="quartzo_teste_")
        self.upstream = os.path.join(self.dir, "upstream")
        for nome in ("UFADE", "ALEX"):
            gravar(os.path.join(self.upstream, nome, "codigo.py"), f"{nome} em uso\n")
        self.origem_json = os.path.join(self.upstream, "ORIGEM.json")
        gravar(self.origem_json, '{"UFADE": {"commit": "a"}, "ALEX": {"commit": "b"}}')
        self.raiz_antes = sincronizar.RAIZ
        sincronizar.RAIZ = self.dir
        self.argv_antes = sys.argv
        sys.argv = ["sincronizar.py"]

    def tearDown(self):
        sincronizar.RAIZ = self.raiz_antes
        sys.argv = self.argv_antes
        shutil.rmtree(self.dir, onerror=lambda f, p, _e: (os.chmod(p, stat.S_IWRITE), f(p)))

    @staticmethod
    def baixar_falso(nome, url, destino_tmp):
        alvo = os.path.join(destino_tmp, nome)
        gravar(os.path.join(alvo, "codigo.py"), f"{nome} novo\n")
        pacote = os.path.join(alvo, ".git", "objects", "pack", "pack-x.pack")
        gravar(pacote, "dados")
        somente_leitura(pacote)
        return alvo, "c" * 40, "2026-10-23T10:00:00+02:00"

    def conteudo(self, nome):
        return ler(os.path.join(self.upstream, nome, "codigo.py"))

    def test_falha_na_geracao_devolve_os_dois_originais(self):
        def falha(motor, verbose=True):
            self.assertEqual(json.loads(ler(self.origem_json))["UFADE"]["commit"], "c" * 40)
            raise SystemExit("ERRO de integração: trecho esperado não encontrado")
        with mock.patch.object(sincronizar, "baixar", self.baixar_falso), \
                mock.patch.object(traduzir, "gerar", falha), \
                mock.patch.object(traduzir, "descartar", lambda motor: None):
            with self.assertRaises(SystemExit):
                sincronizar.main()
        self.assertEqual(self.conteudo("UFADE"), "UFADE em uso\n")
        self.assertEqual(self.conteudo("ALEX"), "ALEX em uso\n")
        self.assertEqual(sorted(os.listdir(self.upstream)), ["ALEX", "ORIGEM.json", "UFADE"])
        self.assertFalse(os.path.exists(os.path.join(self.upstream, "UFADE", ".git")))
        self.assertEqual(json.loads(ler(self.origem_json))["UFADE"]["commit"], "a")

    def test_restauracao_segue_quando_um_original_falha(self):
        for nome in ("UFADE", "ALEX"):
            os.rename(os.path.join(self.upstream, nome), os.path.join(self.upstream, f"_{nome}_anterior"))
            gravar(os.path.join(self.upstream, nome, "codigo.py"), f"{nome} novo\n")
        apagar_real = sincronizar.apagar

        def apagar_com_falha(caminho):
            if caminho.endswith(os.path.join("upstream", "UFADE")):
                raise PermissionError("acesso negado")
            apagar_real(caminho)
        with mock.patch.object(sincronizar, "apagar", apagar_com_falha):
            falhas = sincronizar.restaurar_upstream(["UFADE", "ALEX"])
        self.assertEqual(len(falhas), 1)
        self.assertTrue(falhas[0].startswith("UFADE:"))
        self.assertEqual(self.conteudo("ALEX"), "ALEX em uso\n")
        self.assertTrue(os.path.isdir(os.path.join(self.upstream, "_UFADE_anterior")))

    def test_atualizacao_completa_nao_leva_o_git_para_upstream(self):
        trabalhos = []

        def gerar_falso(motor, verbose=True):
            # o aviso do cabeçalho do motor é montado a partir do ORIGEM.json lido aqui
            commit = json.loads(ler(self.origem_json))["UFADE"]["commit"]
            trabalhos.append((motor, commit))
            return os.path.join(self.dir, motor), {}
        with mock.patch.object(sincronizar, "baixar", self.baixar_falso), \
                mock.patch.object(traduzir, "gerar", gerar_falso), \
                mock.patch.object(traduzir, "instalar", lambda motor, trabalho: None), \
                mock.patch.object(traduzir, "cmd_relatorio", lambda motor: None), \
                mock.patch.object(sincronizar.subprocess, "run", lambda *a, **k: None):
            sincronizar.main()
        self.assertEqual(trabalhos, [("ufade", "c" * 40), ("alex", "c" * 40)])
        self.assertEqual(self.conteudo("UFADE"), "UFADE novo\n")
        self.assertEqual(sorted(os.listdir(self.upstream)), ["ALEX", "ORIGEM.json", "UFADE"])
        for nome in ("UFADE", "ALEX"):
            self.assertFalse(os.path.exists(os.path.join(self.upstream, nome, ".git")))
        self.assertEqual(json.loads(ler(self.origem_json))["ALEX"]["commit"], "c" * 40)


if __name__ == "__main__":
    unittest.main()
