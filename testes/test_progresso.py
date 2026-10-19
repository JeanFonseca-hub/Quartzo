# -*- coding: utf-8 -*-
"""Testes do estimador de tempo restante (python -m unittest discover testes)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quartzo_app.progresso import Estimador, formatar_duracao  # noqa: E402


class TesteEstimador(unittest.TestCase):
    def test_ritmo_constante(self):
        # etapa de 600 s com avanço linear: aos 300 s devem restar ~300 s
        e = Estimador()
        for t in range(0, 301, 2):
            e.alimentar(1, t / 600, "determinado", agora=t)
            r = e.estimativa("determinado", agora=t)
        self.assertEqual(r["situacao"], "estimado")
        self.assertAlmostEqual(r["restante_s"], 300, delta=15)

    def test_nao_estima_cedo(self):
        e = Estimador()
        for t in range(0, 10):
            e.alimentar(1, t / 600, "determinado", agora=t)
        self.assertEqual(e.estimativa("determinado", agora=9)["situacao"], "calculando")

    def test_serra_do_alex_e_tratada_como_sem_total(self):
        # ALEX sem total conhecido: progress.set((bytes % 100) / 100)
        e = Estimador()
        b = 0
        for t in range(0, 120):
            b += 37
            e.alimentar(1, (b % 100) / 100, "determinado", agora=t)
        r = e.estimativa("determinado", agora=119)
        self.assertEqual(r["situacao"], "sem_total")
        self.assertIsNone(r["restante_s"])

    def test_indeterminado(self):
        e = Estimador()
        e.alimentar(1, None, "indeterminado", agora=0)
        self.assertEqual(e.estimativa("indeterminado", agora=5)["situacao"], "sem_total")

    def test_parado(self):
        e = Estimador()
        for t in range(0, 60, 2):
            e.alimentar(1, t / 600, "determinado", agora=t)
        for t in range(60, 200, 5):
            e.alimentar(1, 58 / 600, "determinado", agora=t)
        r = e.estimativa("determinado", agora=199)
        self.assertEqual(r["situacao"], "parado")
        self.assertGreater(r["parado_s"], 60)

    def test_nova_fase_reinicia(self):
        e = Estimador()
        for t in range(0, 100):
            e.alimentar(1, t / 100, "determinado", agora=t)
        e.alimentar(2, 0.0, "determinado", agora=101)
        self.assertEqual(len(e.amostras), 1)

    def test_avanco_lento_estima_com_janela_longa(self):
        # o caso real: 3 % em cerca de uma hora (miniaturas da galeria)
        e = Estimador()
        ritmo = 0.03 / 3900
        for t in range(0, 1801):
            e.alimentar(2, 0.01 + t * ritmo, "determinado", agora=t)
        r = e.estimativa("determinado", agora=1800)
        self.assertEqual(r["situacao"], "estimado")
        esperado = (1 - (0.01 + 1800 * ritmo)) / ritmo
        self.assertAlmostEqual(r["restante_s"], esperado, delta=esperado * 0.05)

    def test_formatar(self):
        self.assertEqual(formatar_duracao(42), "42s")
        self.assertEqual(formatar_duracao(125), "2min05s")
        self.assertEqual(formatar_duracao(3 * 3600 + 7 * 60), "3h07min")


if __name__ == "__main__":
    unittest.main()
