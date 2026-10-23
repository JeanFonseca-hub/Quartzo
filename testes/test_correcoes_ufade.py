# -*- coding: utf-8 -*-
"""Testes das correções do motor iOS (quartzo_app/correcoes_ufade.py)
e do ajuste de integração (ferramentas/integrar.py).
python -m unittest discover testes"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "ferramentas"))
from quartzo_app import correcoes_ufade as qc  # noqa: E402
import integrar  # noqa: E402
import traduzir  # noqa: E402


class Var:
    """Substituto de ctk.IntVar para os testes."""
    def __init__(self):
        self.v = 0

    def set(self, v):
        self.v = v

    def get(self):
        return self.v


class BackupFalso:
    """Imita a API do iOSbackup usada pelo motor."""
    def __init__(self, registros):
        self.registros = registros  # fileID -> dict(flags, conteudo, domain, relativePath)

    def getFileManifestDBEntry(self, fileNameHash=None, relativePath=None):
        r = self.registros[fileNameHash]
        return {"fileID": fileNameHash, "domain": r["domain"], "relativePath": r["relativePath"],
                "flags": r["flags"], "manifest": {"LastModified": 0}}

    def getFileDecryptedCopy(self, manifestEntry=None, targetName=None, targetFolder=None):
        dados = self.registros[manifestEntry["fileID"]]["conteudo"]
        if dados == b"":
            raise ValueError("cannot mmap an empty file")
        with open(os.path.join(targetFolder, targetName), "wb") as fh:
            fh.write(dados)

    @staticmethod
    def getFileInfo(manifest):
        return {"lastModified": datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)}


class TesteResumo(unittest.TestCase):
    def test_sem_repetidos(self):
        log = []
        qc.resumo_descriptografia(1200, 1198, 2, log.append)
        self.assertIn("1,198 de 1,200", log[0])
        self.assertIn("2 falha(s)", log[0])
        self.assertNotIn("Skipped duplicate", log[0])

    def test_com_repetidos_do_prfs(self):
        log = []
        qc.resumo_descriptografia(1200, 1195, 2, log.append, 3)
        self.assertIn("1,195 de 1,200", log[0])
        self.assertIn("3 registro(s) com caminho já presente no ZIP", log[0])


class TesteDecifrar(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="quartzo_teste_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_arquivo_pasta_e_vazio(self):
        b = BackupFalso({
            "f1": {"flags": 1, "conteudo": b"SQLite format 3\x00", "domain": "HomeDomain", "relativePath": "Library/SMS/sms.db"},
            "f2": {"flags": 2, "conteudo": b"", "domain": "HomeDomain", "relativePath": "Library/Pasta"},
            "f3": {"flags": 1, "conteudo": b"", "domain": "HomeDomain", "relativePath": "Library/vazio.txt"},
        })
        p1 = qc.decifrar(b, "f1", "Library/SMS/sms.db", self.dir)
        with open(p1, "rb") as fh:
            self.assertTrue(fh.read().startswith(b"SQLite format 3"))
        self.assertTrue(os.path.isdir(qc.decifrar(b, "f2", "Library/Pasta", self.dir)))
        p3 = qc.decifrar(b, "f3", "Library/vazio.txt", self.dir)
        self.assertTrue(os.path.isfile(p3))
        self.assertEqual(os.path.getsize(p3), 0)

    def test_outro_erro_propaga(self):
        class B(BackupFalso):
            def getFileDecryptedCopy(self, **kw):
                raise ValueError("chave errada")
        b = B({"f": {"flags": 1, "conteudo": b"x", "domain": "D", "relativePath": "a"}})
        with self.assertRaises(ValueError):
            qc.decifrar(b, "f", "a", self.dir)


class TesteBackupDoZip(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="quartzo_teste_")
        self.evid = os.path.join(self.dir, "01_EVIDENCIA_ORIGINAL")
        self.sessao = os.path.join(self.dir, "sessao")
        os.makedirs(self.evid)
        os.makedirs(self.sessao)
        self.zip = os.path.join(self.evid, "Apple_X.zip")
        pref = "iPhoneDump/Backup Service/UDID123/Snapshot/"
        with zipfile.ZipFile(self.zip, "w") as z:
            z.writestr(pref + "Manifest.plist", b"plist")
            z.writestr(pref + "Info.plist", b"info")
            z.writestr(pref + "Manifest.db", b"db")
            z.writestr(pref + "ab/ab12", b"conteudo")
            z.writestr("iPhoneDump/AFC Service/x.jpg", b"jpg")
        sha = qc.hashes_arquivo(self.zip)[2]
        with open(os.path.join(self.evid, "Apple_X.ufd"), "w") as fh:
            fh.write(f"[SHA256]\nApple_X.zip={sha.upper()}\n")
        self.temp = os.path.join(self.dir, "temp_externo")
        self.cwd = os.getcwd()
        self.env_antigo = os.environ.get("QUARTZO_TEMP")
        os.environ["QUARTZO_TEMP"] = self.temp
        # simula a descriptografia na mesma sessão da extração: a sessão é a pasta da evidência
        os.chdir(self.evid)

    def tearDown(self):
        os.chdir(self.cwd)
        if self.env_antigo is None:
            os.environ.pop("QUARTZO_TEMP", None)
        else:
            os.environ["QUARTZO_TEMP"] = self.env_antigo
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_fluxo_completo_sem_gravar_na_evidencia(self):
        antes = sorted(os.listdir(self.evid))
        mtime = os.path.getmtime(self.zip)
        log = []
        dest, zip_origem, prefixo = qc.preparar_backup_de_zip(self.evid, log.append)
        self.assertEqual(os.path.basename(dest), "UDID123")
        self.assertTrue(dest.startswith(os.path.abspath(self.temp)))
        self.assertFalse(qc._dentro(dest, self.evid))
        self.assertTrue(os.path.isfile(os.path.join(dest, "Manifest.plist")))
        var = Var()
        qc.extrair_backup_de_zip(zip_origem, prefixo, dest, log.append, var)
        self.assertEqual(var.get(), 1)
        self.assertTrue(os.path.isfile(os.path.join(dest, "ab", "ab12")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "Manifest.db")))
        self.assertTrue(any("CONFERE" in l for l in log))
        qc.limpar_temporario(log.append)
        self.assertFalse(os.path.exists(os.path.dirname(dest)))
        self.assertEqual(sorted(os.listdir(self.evid)), antes)
        self.assertEqual(os.path.getmtime(self.zip), mtime)

    def test_recusa_temporario_dentro_da_evidencia(self):
        os.environ["QUARTZO_TEMP"] = os.path.join(self.evid, "tmp")
        log = []
        base = qc.escolher_pasta_temporaria(self.zip, 10, log.append, sessao=self.evid)
        self.assertTrue(any("recusada" in l for l in log))
        self.assertFalse(qc._dentro(base, self.evid))
        self.assertFalse(os.path.exists(os.path.join(self.evid, "tmp")))

    def test_sem_espaco(self):
        log = []
        self.assertIsNone(qc.escolher_pasta_temporaria(self.zip, 10 ** 18, log.append, sessao=self.evid))
        self.assertTrue(any("sem espaço" in l for l in log))

    def test_segunda_escolha_descarta_a_primeira(self):
        primeira = qc.preparar_backup_de_zip(self.evid, print)[0]
        segunda = qc.preparar_backup_de_zip(self.evid, print)[0]
        self.assertFalse(os.path.exists(os.path.dirname(primeira)) and primeira != segunda
                         and os.path.dirname(primeira) != os.path.dirname(segunda))
        qc.limpar_temporario(print)

    def test_pasta_sem_zip(self):
        self.assertIsNone(qc.preparar_backup_de_zip(self.sessao, print))


class TesteDiversos(unittest.TestCase):
    def test_restauracao_nao_remove_senha_previa(self):
        log = []
        qc.registrar_cripto_previa(log.append)
        self.assertTrue(qc.ESTADO["cripto_previa"])
        qc.registrar_restauracao(1, "12345", True, log.append)
        self.assertIn("mantida", log[-1])
        qc.registrar_restauracao(2, "12345", True, log.append)
        self.assertIn("PERMANECE", log[-1])
        qc.registrar_cripto_ativada(log.append)
        qc.registrar_restauracao(1, "12345", True, log.append)
        self.assertIn("desativada", log[-1])
        self.assertTrue(all("12345" not in l for l in log))

    def test_motivo_interrupcao(self):
        self.assertIn("cancelada", qc.motivo_interrupcao(2))
        self.assertIn("falha", qc.motivo_interrupcao(5))

    def test_completar_case_device(self):
        class L:
            all_values = {"CarrierBundleInfoArray": [{"IntegratedCircuitCardIdentity": "8955", "InternationalMobileSubscriberIdentity": "724"}]}
        cd = {}
        qc.completar_case_device(cd, "3593", L())
        self.assertEqual(cd["IMEI2"], "3593")
        self.assertEqual(cd["SIMs"], [{"ICCID": "8955", "IMSI": "724"}])
        cd2 = {}
        qc.completar_case_device(cd2, " ", None)
        self.assertEqual(cd2, {})


class TesteIntegrar(unittest.TestCase):
    def test_padrao_tolera_espacos_no_fim_da_linha(self):
        fonte = "a = 1   \nb = 2\t\nc = 3\n"
        p = integrar._padrao("a = 1\nb = 2\n")
        self.assertEqual(len(p.findall(fonte)), 1)
        self.assertEqual(p.sub(lambda m: "X\n", fonte), "X\nc = 3\n")

    def test_padrao_nao_casa_indentacao_diferente(self):
        p = integrar._padrao("\n    log(x)\n")
        self.assertEqual(len(p.findall("\n        log(x)\n")), 0)


class TesteGeracaoDosMotores(unittest.TestCase):
    """O motor em uso só é trocado quando tradução e integração terminam sem erro."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="quartzo_teste_")
        self.origem = os.path.join(self.dir, "upstream", "UFADE")
        self.destino = os.path.join(self.dir, "motores", "ios")
        os.makedirs(self.origem)
        os.makedirs(os.path.join(self.destino, "ufade_developer", "Developer"))
        self.gravar(os.path.join(self.origem, "ufade.py"), 'x = "versao nova"\n')
        self.gravar(os.path.join(self.destino, "ufade.py"), 'x = "versao em uso"\n')
        self.gravar(os.path.join(self.destino, "ufade_developer", "Developer", "imagem"), "imagem")
        self.motores_antes = traduzir.MOTORES
        self.aplicar_antes = integrar.aplicar
        traduzir.MOTORES = {"ufade": {"origem": self.origem, "destino": self.destino, "principal": "ufade.py"}}

    def tearDown(self):
        traduzir.MOTORES = self.motores_antes
        integrar.aplicar = self.aplicar_antes
        shutil.rmtree(self.dir, ignore_errors=True)

    @staticmethod
    def gravar(caminho, texto):
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write(texto)

    def ler_motor(self):
        with open(os.path.join(self.destino, "ufade.py"), encoding="utf-8") as fh:
            return fh.read()

    def test_falha_na_integracao_preserva_o_motor_em_uso(self):
        def falha(motor, destino):
            raise SystemExit("ERRO de integração: trecho esperado não encontrado")
        integrar.aplicar = falha
        with self.assertRaises(SystemExit):
            traduzir.cmd_aplicar("ufade", verbose=False)
        self.assertEqual(self.ler_motor(), 'x = "versao em uso"\n')
        self.assertEqual(os.listdir(os.path.dirname(self.destino)), ["ios"])  # sem sobras _novo/_antigo

    def test_geracao_completa_troca_o_motor_e_mantem_as_imagens(self):
        integrar.aplicar = lambda motor, destino: 0
        traduzir.cmd_aplicar("ufade", verbose=False)
        self.assertEqual(self.ler_motor(), 'x = "versao nova"\n')
        self.assertTrue(os.path.isfile(os.path.join(self.destino, "ufade_developer", "Developer", "imagem")))
        self.assertEqual(os.listdir(os.path.dirname(self.destino)), ["ios"])


if __name__ == "__main__":
    unittest.main()
