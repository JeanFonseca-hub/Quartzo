#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quartzo - Extração forense de dispositivos móveis (iOS + Android), em português.

Lançador unificado dos motores:
  * motores/ios      -> UFADE (Universal Forensic Apple Device Extractor)
  * motores/android  -> ALEX  (Android Logical Extractor)
ambos de Christian Peter (https://github.com/prosch88), licenciados sob GPL-3.0.

Copyright (C) 2026 Jean Carlos Fonseca
Este programa é software livre: você pode redistribuí-lo e/ou modificá-lo sob
os termos da GNU General Public License, versão 3 ou posterior. É distribuído
na esperança de ser útil, mas SEM QUALQUER GARANTIA. Veja o arquivo LICENSE.
"""
import getpass
import json
import os
import platform
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from datetime import datetime, timezone
from tkinter import filedialog, messagebox

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)

import customtkinter as ctk  # noqa: E402

from quartzo_app import ICONE_ICO, ICONE_PNG, NOME_LONGO, VERSAO  # noqa: E402
from quartzo_app import deteccao, hashes, integridade  # noqa: E402
from quartzo_app.acompanhamento import JanelaAcompanhamento  # noqa: E402
from quartzo_app.janela_whatsapp import JanelaWhatsApp  # noqa: E402

if sys.platform == "win32":
    # identidade própria na barra de tarefas (senão o Windows agrupa sob o pythonw)
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Quartzo.Lancador")
    except Exception:
        pass

MOTORES = {
    "ios": {"rotulo": "Apple", "script": os.path.join(RAIZ, "motores", "ios", "ufade.py"),
            "base": "UFADE", "repo": "https://github.com/prosch88/UFADE"},
    "android": {"rotulo": "Android", "script": os.path.join(RAIZ, "motores", "android", "alex.py"),
                "base": "ALEX", "repo": "https://github.com/prosch88/ALEX"},
}
TAMANHOS = {"Padrão (1100×600)": None, "Compacto (1024×600)": "1024",
            "Grande (1368×764)": "1368", "Reduzido (800, só iOS)": "800"}

if sys.platform == "win32":
    PASTA_CONFIG = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Quartzo")
else:
    PASTA_CONFIG = os.path.join(os.path.expanduser("~"), ".config", "quartzo")
ARQ_CONFIG = os.path.join(PASTA_CONFIG, "config.json")

COR_FUNDO = "#1c2127"
COR_PAINEL = "#252c34"
COR_BORDA = "#34404c"
COR_TEXTO = "#dbe2ea"
COR_SUAVE = "#8794a3"
COR_APPLE = "#3a6ea5"
COR_ANDROID = "#3d8b5f"
COR_ALERTA = "#c9862b"
COR_ERRO = "#c0504d"
COR_OK = "#4c9a6a"

CRIAR_SEM_JANELA = 0x08000000 if sys.platform == "win32" else 0


def agora_local():
    return datetime.now().astimezone()


def carregar_config():
    try:
        with open(ARQ_CONFIG, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def salvar_config(cfg):
    try:
        os.makedirs(PASTA_CONFIG, exist_ok=True)
        with open(ARQ_CONFIG, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def ler_versao_motor(chave):
    """Lê a versão declarada no código do motor sem importá-lo."""
    import re
    padrao = {"ios": r'^u_version\s*=\s*"([^"]+)"', "android": r'^a_version\s*=\s*([0-9.]+)'}[chave]
    try:
        with open(MOTORES[chave]["script"], "r", encoding="utf-8") as fh:
            m = re.search(padrao, fh.read(), re.M)
        return m.group(1) if m else "?"
    except Exception:
        return "?"


def ler_origem_upstream():
    try:
        with open(os.path.join(RAIZ, "upstream", "ORIGEM.json"), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _versoes_bibliotecas():
    """Versões das bibliotecas forenses relevantes, para o log da sessão."""
    import importlib.metadata as md
    libs = ("pymobiledevice3", "iOSbackup", "pyiosbackup", "adbutils", "customtkinter",
            "paramiko", "pdfme", "cryptography", "pycryptodome", "numpy", "pandas")
    partes = []
    for l in libs:
        try:
            partes.append(f"{l} {md.version(l)}")
        except Exception:
            pass
    return ", ".join(partes) or "não foi possível ler as versões"


def _estado_relogio():
    """Situação do relógio do Windows (w32tm): fonte e última sincronização, se houver.
    Todos os horários do log dependem do relógio, por isso ele fica registrado."""
    if sys.platform != "win32":
        return "não verificado (sistema não Windows)"
    try:
        # o w32tm escreve na página de código do console (OEM), não na do sistema
        r = subprocess.run(["w32tm", "/query", "/status"], capture_output=True, text=True, encoding="oem",
                           errors="replace", timeout=8, creationflags=CRIAR_SEM_JANELA)
    except Exception as e:
        return f"não foi possível verificar ({e.__class__.__name__})"
    if r.returncode != 0:
        saida = (r.stdout or "") + (r.stderr or "")
        if "0x80070426" in saida:
            return "NÃO SINCRONIZADO: o serviço de horário do Windows (W32Time) está parado"
        detalhe = next((l.strip() for l in saida.splitlines() if l.strip()), "sem mensagem")
        return f"não foi possível consultar o w32tm ({detalhe})"
    chaves = ("source", "origem", "stratum", "camada", "last successful sync", "última sincronização",
              "ultima sincronizacao", "leap indicator", "indicador de salto")
    relevantes = [l.strip() for l in r.stdout.splitlines() if any(c in l.lower() for c in chaves)]
    return "  |  ".join(relevantes) if relevantes else "w32tm respondeu (origem do horário não identificada)"


class RegistroSessao:
    """Log de sessão em texto simples, gravado na pasta de saída do caso."""

    def __init__(self):
        self.caminho = None

    def abrir(self, pasta, dados_caso, integ):
        carimbo = agora_local().strftime("%Y%m%d_%H%M%S")
        self.caminho = os.path.join(pasta, f"QUARTZO_sessao_{carimbo}.log")
        origem = ler_origem_upstream()
        linhas = [
            "=" * 78,
            f"{NOME_LONGO} - versão {VERSAO}",
            "REGISTRO DE SESSÃO",
            "=" * 78,
            f"Início (local) .........: {agora_local().isoformat(timespec='seconds')}",
            f"Início (UTC) ...........: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            f"Nº do procedimento .....: {dados_caso['numero']}",
            f"Nome do caso ...........: {dados_caso['nome']}",
            f"Nº do item / lacre .....: {dados_caso['evidencia']}",
            f"Responsável ............: {dados_caso['examinador']}",
            f"Usuário do sistema .....: {getpass.getuser()}",
            f"Computador .............: {socket.gethostname()}",
            f"Sistema operacional ....: {platform.platform()}",
            f"Python .................: {platform.python_version()} ({sys.executable})",
            f"Bibliotecas ............: {_versoes_bibliotecas()}",
            f"Relógio do Windows .....: {_estado_relogio()}",
            f"Pasta de saída .........: {pasta}",
            f"Motor iOS ..............: UFADE {ler_versao_motor('ios')} "
            f"(commit {origem.get('UFADE', {}).get('commit', '?')[:12]})",
            f"Motor Android ..........: ALEX {ler_versao_motor('android')} "
            f"(commit {origem.get('ALEX', {}).get('commit', '?')[:12]})",
            f"Integridade do código ..: {integ[0]}"
            + (f" ({len(integ[1])} divergência(s))" if integ[0] != "OK" else ""),
            f"SHA-256 da base ........: {integridade.sha256_da_base()}",
        ]
        for d in integ[1][:50]:
            linhas.append(f"    - {d}")
        linhas.append("-" * 78)
        self._gravar("\n".join(linhas))

    def evento(self, texto):
        if not self.caminho:
            return
        self._gravar(f"[{agora_local().isoformat(timespec='seconds')}] {texto}")

    def fechar(self, texto):
        """Grava a última linha e encerra o log: nada mais é escrito nele depois.
        Usado antes do manifesto de hashes da pasta que contém o log, para que o
        hash registrado no manifesto continue valendo."""
        if not self.caminho:
            return None
        self._gravar(f"[{agora_local().isoformat(timespec='seconds')}] {texto}")
        caminho, self.caminho = self.caminho, None
        return caminho

    def contido_em(self, pasta):
        if not self.caminho:
            return False
        try:
            return os.path.commonpath([os.path.abspath(self.caminho), os.path.abspath(pasta)]) == os.path.abspath(pasta)
        except ValueError:
            return False

    def _gravar(self, texto):
        try:
            with open(self.caminho, "a", encoding="utf-8") as fh:
                fh.write(texto + "\n")
        except Exception:
            pass


class Lancador(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title(f"{NOME_LONGO}  ·  v{VERSAO}")
        self.geometry("1040x680")
        self.minsize(960, 640)
        self.configure(fg_color=COR_FUNDO)
        try:
            if sys.platform == "win32" and os.path.isfile(ICONE_ICO):
                # default= vale também para as janelas filhas e caixas de mensagem
                self.iconbitmap(default=ICONE_ICO)
            else:
                self._icone_png = tk.PhotoImage(file=ICONE_PNG)  # referência mantida
                self.iconphoto(True, self._icone_png)
        except Exception:
            pass

        self.cfg = carregar_config()
        self.sessao = RegistroSessao()
        self.processo = None
        self.acomp = None
        self.dispositivos = []
        self.detectando = False
        self.cancelar_hash = threading.Event()
        self.integ = ("...", [])
        self.fila = queue.Queue()  # chamadas de threads de trabalho para a thread da interface

        self.fonte_titulo = ctk.CTkFont(size=26, weight="bold")
        self.fonte_secao = ctk.CTkFont(size=15, weight="bold")
        self.fonte = ctk.CTkFont(size=13)
        self.fonte_peq = ctk.CTkFont(size=12)
        self.fonte_mono = ctk.CTkFont(family="Consolas" if sys.platform == "win32" else "monospace", size=12)

        self._montar()
        self.protocol("WM_DELETE_WINDOW", self.ao_fechar)
        self.after(100, self._bombear_fila)
        self.after(200, self.verificar_integridade)
        self.after(400, self.atualizar_dispositivos)
        self.after(5000, self._ciclo_deteccao)

    # ------------------------------------------------------------------ layout
    def _montar(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        topo = ctk.CTkFrame(self, fg_color=COR_FUNDO)
        topo.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 6))
        topo.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(topo, text="Quartzo", font=self.fonte_titulo, text_color=COR_TEXTO).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(topo, text="Extração forense de dispositivos móveis  ·  iOS (UFADE) + Android (ALEX)",
                     font=self.fonte, text_color=COR_SUAVE).grid(row=0, column=1, sticky="w", padx=14, pady=(8, 0))
        self.selo_integ = ctk.CTkLabel(topo, text="Verificando integridade...", font=self.fonte_peq,
                                       text_color=COR_SUAVE, corner_radius=6, fg_color=COR_PAINEL, padx=10)
        self.selo_integ.grid(row=0, column=2, sticky="e")
        self.selo_integ.bind("<Button-1>", lambda e: self.mostrar_integridade())

        corpo = ctk.CTkFrame(self, fg_color=COR_FUNDO)
        corpo.grid(row=1, column=0, sticky="nsew", padx=24, pady=6)
        corpo.grid_columnconfigure(0, weight=5, uniform="c")
        corpo.grid_columnconfigure(1, weight=6, uniform="c")
        corpo.grid_rowconfigure(0, weight=1)

        self._painel_caso(corpo).grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self._painel_dispositivos(corpo).grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        self._painel_acoes().grid(row=2, column=0, sticky="ew", padx=24, pady=(8, 6))

        self.status = ctk.CTkLabel(self, text="Pronto.", anchor="w", font=self.fonte_peq,
                                   text_color=COR_SUAVE)
        self.status.grid(row=3, column=0, sticky="ew", padx=26, pady=(0, 4))
        self.barra = ctk.CTkProgressBar(self, height=6)
        self.barra.set(0)
        self.barra.grid(row=4, column=0, sticky="ew", padx=24, pady=(0, 12))
        self.barra.grid_remove()

    def _secao(self, pai, numero, titulo):
        f = ctk.CTkFrame(pai, fg_color=COR_PAINEL, corner_radius=10, border_width=1, border_color=COR_BORDA)
        cab = ctk.CTkFrame(f, fg_color="transparent")
        cab.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(cab, text=numero, width=26, height=26, corner_radius=13, fg_color=COR_BORDA,
                     font=self.fonte_secao, text_color=COR_TEXTO).pack(side="left")
        ctk.CTkLabel(cab, text=titulo, font=self.fonte_secao, text_color=COR_TEXTO).pack(side="left", padx=10)
        return f

    def _painel_caso(self, pai):
        f = self._secao(pai, "1", "Dados do caso")
        corpo = ctk.CTkFrame(f, fg_color="transparent")
        corpo.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        corpo.grid_columnconfigure((0, 1), weight=1, uniform="campos")
        self.campos = {}
        # (chave, rótulo, dica, linha, coluna, colunas ocupadas) - layout compacto para
        # caber em telas de 768/864 px de altura com escala de 125-150% no Windows
        defs = [("numero", "Nº do procedimento (BO / IP)", "número do procedimento (Processo / IP)", 0, 0, 2),
                ("nome", "Nome do caso", "ex.: Estelionato - vítima X", 2, 0, 1),
                ("evidencia", "Nº do item / lacre", "número do item ou do lacre", 2, 1, 1),
                ("examinador", "Responsável pela extração", "nome e cargo (ex.: investigador de polícia)", 4, 0, 2)]
        for chave, rotulo, dica, lin, col, span in defs:
            padx = (0, 6) if (span == 1 and col == 0) else ((6, 0) if span == 1 else 0)
            ctk.CTkLabel(corpo, text=rotulo, font=self.fonte_peq, text_color=COR_SUAVE, anchor="w").grid(
                row=lin, column=col, columnspan=span, sticky="w", pady=(4, 0), padx=padx)
            e = ctk.CTkEntry(corpo, placeholder_text=dica, font=self.fonte, height=30)
            e.grid(row=lin + 1, column=col, columnspan=span, sticky="ew", padx=padx)
            self.campos[chave] = e
        r = 6
        if self.cfg.get("examinador"):
            self.campos["examinador"].insert(0, self.cfg["examinador"])

        ctk.CTkLabel(corpo, text="Pasta de saída da extração", font=self.fonte_peq, text_color=COR_SUAVE,
                     anchor="w").grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 0))
        linha = ctk.CTkFrame(corpo, fg_color="transparent")
        linha.grid(row=r + 1, column=0, columnspan=2, sticky="ew")
        linha.grid_columnconfigure(0, weight=1)
        self.saida = ctk.CTkEntry(linha, font=self.fonte, height=32)
        self.saida.grid(row=0, column=0, sticky="ew")
        self.saida.insert(0, self.cfg.get("saida", os.path.join(os.path.expanduser("~"), "Quartzo_extracoes")))
        ctk.CTkButton(linha, text="Procurar", width=90, height=32, fg_color=COR_BORDA,
                      command=self.escolher_saida).grid(row=0, column=1, padx=(8, 0))
        self.subpasta = ctk.CTkCheckBox(corpo, text="Criar subpasta com nº do procedimento e data/hora",
                                        font=self.fonte_peq)
        self.subpasta.grid(row=r + 2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        if self.cfg.get("subpasta", True):
            self.subpasta.select()

        linha_tam = ctk.CTkFrame(corpo, fg_color="transparent")
        linha_tam.grid(row=r + 3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ctk.CTkLabel(linha_tam, text="Janela dos módulos:", font=self.fonte_peq, text_color=COR_SUAVE,
                     anchor="w").pack(side="left")
        self.tamanho = ctk.CTkOptionMenu(linha_tam, values=list(TAMANHOS), font=self.fonte, height=28,
                                         width=210, fg_color=COR_BORDA, button_color=COR_BORDA)
        self.tamanho.set(self.cfg.get("tamanho", "Padrão (1100×600)"))
        self.tamanho.pack(side="left", padx=(8, 0))
        return f

    def _painel_dispositivos(self, pai):
        f = self._secao(pai, "2", "Dispositivos conectados")
        self.lista = ctk.CTkScrollableFrame(f, fg_color=COR_FUNDO, corner_radius=8)
        self.lista.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        rodape = ctk.CTkFrame(f, fg_color="transparent")
        rodape.pack(fill="x", padx=16, pady=(0, 14))
        self.aviso_det = ctk.CTkLabel(rodape, text="", font=self.fonte_peq, text_color=COR_ALERTA,
                                      anchor="w", justify="left", wraplength=430)
        self.aviso_det.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(rodape, text="Atualizar", width=90, fg_color=COR_BORDA,
                      command=self.atualizar_dispositivos).pack(side="right")
        nota = ("Detecção passiva: no iPhone não abre pareamento (sem aviso “Confiar”); "
                "no Android apenas lê “adb devices -l”, sem executar comandos no aparelho.")
        ctk.CTkLabel(f, text=nota, font=self.fonte_peq, text_color=COR_SUAVE, wraplength=480,
                     justify="left", anchor="w").pack(fill="x", padx=16, pady=(0, 12))
        return f

    def _painel_acoes(self):
        f = self._secao(self, "3", "Abrir módulo de extração")
        linha = ctk.CTkFrame(f, fg_color="transparent")
        linha.pack(fill="x", padx=16, pady=(0, 14))
        linha.grid_columnconfigure((0, 1), weight=1, uniform="b")
        self.bt_ios = ctk.CTkButton(
            linha, text="Apple\niPhone · iPad · Watch · Apple TV", height=74, font=self.fonte_secao,
            fg_color=COR_APPLE, hover_color="#2f5a88", command=lambda: self.abrir_motor("ios"))
        self.bt_ios.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.bt_android = ctk.CTkButton(
            linha, text="Android\ncelulares · tablets · Wear OS · Fire OS", height=74, font=self.fonte_secao,
            fg_color=COR_ANDROID, hover_color="#2f6f4b", command=lambda: self.abrir_motor("android"))
        self.bt_android.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        lateral = ctk.CTkFrame(linha, fg_color="transparent")
        lateral.grid(row=0, column=2, sticky="ns")
        self.bt_hash = ctk.CTkButton(lateral, text="Manifesto de hashes", width=170, height=34,
                                     fg_color=COR_BORDA, command=self.gerar_manifesto)
        self.bt_hash.pack(pady=(0, 6))
        self.bt_conferir = ctk.CTkButton(lateral, text="Conferir extração", width=170, height=34,
                                         fg_color=COR_BORDA, command=self.conferir_extracao)
        self.bt_conferir.pack(pady=(0, 6))
        ctk.CTkButton(lateral, text="WhatsApp (chave)", width=170, height=34, fg_color=COR_BORDA,
                      command=self.abrir_whatsapp).pack(pady=(0, 6))
        ctk.CTkButton(lateral, text="Sobre e licença", width=170, height=34, fg_color=COR_BORDA,
                      command=self.sobre).pack()
        self.link_motores = ctk.CTkLabel(lateral, text="Atualizar motores (UFADE/ALEX)",
                                         font=self.fonte_peq, text_color=COR_APPLE, cursor="hand2")
        self.link_motores.pack(pady=(12, 0))
        self.link_motores.bind("<Button-1>", lambda e: self.atualizar_motores())
        return f

    def no_principal(self, func):
        """Agenda `func` para a thread da interface (seguro a partir de threads)."""
        self.fila.put(func)

    def _bombear_fila(self):
        try:
            while True:
                self.fila.get_nowait()()
        except queue.Empty:
            pass
        except Exception as e:  # nunca deixa a fila morrer
            self.status.configure(text=f"Erro interno: {e}")
        self.after(100, self._bombear_fila)

    # ----------------------------------------------------------- integridade
    def verificar_integridade(self):
        def tarefa():
            try:
                res = integridade.verificar()
            except Exception as e:
                res = ("ERRO", [str(e)])
            self.no_principal(lambda: self._mostrar_selo(res))
        threading.Thread(target=tarefa, daemon=True).start()

    def _mostrar_selo(self, res):
        self.integ = res
        if res[0] == "OK":
            self.selo_integ.configure(text="✔ Código íntegro", text_color=COR_OK)
        elif res[0] == "SEM_BASE":
            self.selo_integ.configure(text="Sem base de integridade", text_color=COR_ALERTA)
        else:
            self.selo_integ.configure(text=f"⚠ Código alterado ({len(res[1])})", text_color=COR_ERRO)

    def mostrar_integridade(self):
        s, det = self.integ
        texto = {"OK": "Todos os arquivos de código conferem com a base integridade.json.",
                 "SEM_BASE": "Não há base de integridade. Gere com ferramentas/gerar_integridade.py."}.get(
            s, "Arquivos divergentes da base:\n\n" + "\n".join(det[:30]))
        messagebox.showinfo("Integridade do código", texto + f"\n\nSHA-256 da base:\n{integridade.sha256_da_base()}")

    # ----------------------------------------------------------- dispositivos
    def _ciclo_deteccao(self):
        if self.processo is None and self.state() != "withdrawn":
            self.atualizar_dispositivos(silencioso=True)
        self.after(5000, self._ciclo_deteccao)

    def atualizar_dispositivos(self, silencioso=False):
        if self.detectando:
            return
        self.detectando = True
        if not silencioso:
            self.status.configure(text="Procurando dispositivos...")

        def tarefa():
            try:
                devs, avisos = deteccao.detectar()
            except Exception as e:
                devs, avisos = [], [f"erro na detecção: {e}"]
            self.no_principal(lambda: self._mostrar_dispositivos(devs, avisos, silencioso))
        threading.Thread(target=tarefa, daemon=True).start()

    def _mostrar_dispositivos(self, devs, avisos, silencioso):
        self.detectando = False
        assinatura = [(d["plataforma"], d["id"], d["estado"]) for d in devs]
        if silencioso and assinatura == [(d["plataforma"], d["id"], d["estado"]) for d in self.dispositivos]:
            return
        self.dispositivos = devs
        for w in self.lista.winfo_children():
            w.destroy()
        if not devs:
            ctk.CTkLabel(self.lista, text="Nenhum dispositivo detectado.\n\n"
                         "iPhone/iPad: conecte, desbloqueie e confie no computador quando o módulo pedir.\n"
                         "Android: ative a Depuração USB nas opções do desenvolvedor.",
                         font=self.fonte, text_color=COR_SUAVE, justify="left", wraplength=440).pack(
                anchor="w", padx=10, pady=12)
        for d in devs:
            cor = COR_APPLE if d["plataforma"] == "Apple" else COR_ANDROID
            card = ctk.CTkFrame(self.lista, fg_color=COR_PAINEL, corner_radius=8, border_width=1, border_color=cor)
            card.pack(fill="x", padx=6, pady=5)
            card.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(card, text=d["plataforma"], width=74, fg_color=cor, corner_radius=6,
                         font=self.fonte_peq, text_color="white").grid(row=0, column=0, rowspan=2, padx=10, pady=10)
            ctk.CTkLabel(card, text=d["modelo"], font=self.fonte_secao, anchor="w", text_color=COR_TEXTO).grid(
                row=0, column=1, sticky="w", pady=(8, 0))
            ctk.CTkLabel(card, text=f"{d['id']}   ·   {d['estado']}   ·   {d['conexao']}", font=self.fonte_mono,
                         anchor="w", text_color=COR_SUAVE).grid(row=1, column=1, sticky="w", pady=(0, 8))
            alvo = "ios" if d["plataforma"] == "Apple" else "android"
            ctk.CTkButton(card, text="Abrir", width=70, fg_color=cor,
                          command=lambda a=alvo: self.abrir_motor(a)).grid(row=0, column=2, rowspan=2, padx=10)
        self.aviso_det.configure(text="\n".join(avisos))
        n = len(devs)
        self.status.configure(text=f"{n} dispositivo(s) detectado(s) às {agora_local().strftime('%H:%M:%S')}.")

    # ----------------------------------------------------------------- caso
    def escolher_saida(self):
        p = filedialog.askdirectory(initialdir=self.saida.get() or os.path.expanduser("~"),
                                    title="Escolha a pasta de saída")
        if p:
            self.saida.delete(0, "end")
            self.saida.insert(0, os.path.normpath(p))

    def dados_caso(self):
        return {k: e.get().strip() for k, e in self.campos.items()}

    def preparar_saida(self, rotulo):
        base = self.saida.get().strip()
        if not base:
            raise ValueError("Informe a pasta de saída.")
        pasta = base
        if self.subpasta.get():
            dados = self.dados_caso()
            num = dados["numero"] or "sem_numero"
            seguro = "".join(c if c.isalnum() or c in "-_." else "_" for c in num).strip("_")
            nome = f"{agora_local().strftime('%Y%m%d_%H%M%S')}_{rotulo}"
            # o item/lacre entra no nome da pasta para identificar a evidência sem abrir o log
            item = "".join(c if c.isalnum() or c in "-_." else "_" for c in dados["evidencia"]).strip("_")[:40]
            if item:
                nome += "_" + (item if item.lower().startswith(("item", "lacre")) else f"item_{item}")
            pasta = os.path.join(base, seguro, nome)
        os.makedirs(pasta, exist_ok=True)
        return os.path.abspath(pasta)

    # ----------------------------------------------------------------- motor
    def abrir_motor(self, chave):
        if self.processo is not None:
            messagebox.showwarning("Quartzo", "Já existe um módulo aberto. Feche-o antes de abrir outro.")
            return
        motor = MOTORES[chave]
        if not os.path.isfile(motor["script"]):
            messagebox.showerror("Quartzo", f"Módulo não encontrado:\n{motor['script']}")
            return
        dados = self.dados_caso()
        self.dados_atual = dados  # usado na selagem automática ao fim desta extração
        if not dados["numero"] or not dados["examinador"]:
            if not messagebox.askyesno("Dados do caso incompletos",
                                       "O nº do procedimento e/ou o responsável não foram preenchidos.\n"
                                       "Eles aparecerão em branco no log da sessão e nos relatórios.\n\n"
                                       "Deseja continuar assim mesmo?"):
                return
        try:
            pasta = self.preparar_saida(motor["rotulo"])
        except Exception as e:
            messagebox.showerror("Quartzo", f"Não foi possível preparar a pasta de saída:\n{e}")
            return
        self.cfg.update({"saida": self.saida.get().strip(), "examinador": dados["examinador"],
                         "tamanho": self.tamanho.get(), "subpasta": bool(self.subpasta.get())})
        salvar_config(self.cfg)

        self.sessao.abrir(pasta, dados, self.integ)
        visiveis = [d for d in self.dispositivos if (d["plataforma"] == "Apple") == (chave == "ios")]
        for d in visiveis:
            self.sessao.evento(f"Dispositivo detectado pelo lançador: {d['plataforma']} {d['modelo']} "
                               f"id={d['id']} estado={d['estado']} conexão={d['conexao']}")
        env = dict(os.environ)
        env.update({"QUARTZO_CASO_NUMERO": dados["numero"], "QUARTZO_CASO_NOME": dados["nome"],
                    "QUARTZO_EVIDENCIA": dados["evidencia"], "QUARTZO_EXAMINADOR": dados["examinador"],
                    "QUARTZO_SESSAO_LOG": self.sessao.caminho or "", "QUARTZO_VERSAO": VERSAO,
                    "PYTHONIOENCODING": "utf-8"})
        # arquivo de estado da sonda de progresso: fica na pasta temporária do
        # sistema, nunca na pasta da extração
        arq_estado = os.path.join(tempfile.gettempdir(),
                                  f"quartzo_progresso_{os.getpid()}_{int(time.time())}.json")
        env["QUARTZO_PROGRESSO"] = arq_estado
        # garante que o motor Android encontre o adb/fastboot mesmo que o PATH
        # do Windows ainda não tenha sido atualizado após instalar o Platform Tools
        adb = deteccao.caminho_adb()
        if adb and os.path.dirname(adb) not in env.get("PATH", "").split(os.pathsep):
            env["PATH"] = os.path.dirname(adb) + os.pathsep + env.get("PATH", "")
        cmd = [sys.executable, motor["script"]]
        tam = TAMANHOS.get(self.tamanho.get())
        if tam and not (tam == "800" and chave == "android"):
            cmd.append(tam)
        # O motor recebe saídas padrão reais. Aberto pelo pythonw (sem console), ele
        # herdaria saídas inexistentes, e a biblioteca de terminal usada pelo
        # pymobiledevice3 (blessed) derruba o motor iOS na importação. A saída de
        # erros vai para um arquivo temporário (fora da pasta da extração), lido se
        # o módulo terminar com erro.
        self.arq_erros = os.path.join(tempfile.gettempdir(),
                                      f"quartzo_motor_{os.getpid()}_{int(time.time())}.txt")
        try:
            self._fh_erros = open(self.arq_erros, "w", encoding="utf-8", errors="replace")
            self.processo = subprocess.Popen(cmd, cwd=pasta, env=env, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=self._fh_erros)
        except Exception as e:
            self.sessao.evento(f"FALHA ao iniciar o módulo {motor['base']}: {e}")
            messagebox.showerror("Quartzo", f"Falha ao iniciar o módulo:\n{e}")
            return
        self.sessao.evento(f"Módulo {motor['rotulo']} (motor {motor['base']} {ler_versao_motor(chave)}) "
                           f"iniciado. PID {self.processo.pid}. Pasta de trabalho: {pasta}")
        self.motor_aberto = motor
        self.pasta_atual = pasta
        try:
            self.acomp = JanelaAcompanhamento(self, arq_estado, pasta, motor["rotulo"], dados, self.sessao.evento)
        except Exception as e:
            self.acomp = None
            self.sessao.evento(f"Janela de acompanhamento indisponível: {e}")
        self.status.configure(text=f"Módulo {motor['rotulo']} em execução. Pasta: {pasta}")
        self.withdraw()
        self.after(1000, self._acompanhar_motor)

    def _acompanhar_motor(self):
        if self.processo is None:
            return
        cod = self.processo.poll()
        if cod is None:
            self.after(1000, self._acompanhar_motor)
            return
        if self.acomp is not None:
            try:
                self.acomp.encerrar("módulo fechado")
            except Exception:
                pass
            self.acomp = None
        self.sessao.evento(f"Módulo {self.motor_aberto['rotulo']} encerrado (código de saída {cod}).")
        self.processo = None
        try:
            self._fh_erros.close()
        except Exception:
            pass
        self.deiconify()
        self.lift()
        self.status.configure(text=f"Módulo encerrado. Pasta da extração: {self.pasta_atual}")
        if cod != 0:
            self._relatar_falha_motor(cod)
        # só sela se o módulo gravou algo além do log da sessão
        conteudo = [n for n in os.listdir(self.pasta_atual) if not n.startswith("QUARTZO_sessao_")]
        if not conteudo:
            self.sessao.evento("Nenhum arquivo foi gravado pelo módulo nesta sessão; não há o que selar.")
            self.atualizar_dispositivos()
            return
        # Selagem automática no ato da coleta: assim que o módulo termina de gravar,
        # os hashes de cada arquivo são calculados na própria estação e o selo é
        # gravado numa pasta irmã, sem acrescentar nada à pasta da evidência.
        selo = self._destino_selo(self.pasta_atual)
        if selo is None:
            self.sessao.evento("ATENÇÃO: selagem automática não realizada; o operador cancelou a escolha do "
                               "local do selo (a pasta da extração é a raiz de uma unidade).")
            self.status.configure(text="Selagem não realizada. Use o botão Manifesto de hashes.")
            self.atualizar_dispositivos()
            return
        self.sessao.evento(f"Selagem automática da extração ao término da coleta: manifesto de hashes "
                           f"(MD5, SHA-1 e SHA-256) gravado em {selo}.")
        self.status.configure(text="Selando a extração (hashes de cada arquivo)...")
        self.gerar_manifesto(self.pasta_atual, destino=selo, automatico=True)
        self.atualizar_dispositivos()

    def _relatar_falha_motor(self, cod):
        """Registra no log da sessão e mostra na tela o motivo da falha do módulo."""
        try:
            with open(self.arq_erros, "r", encoding="utf-8", errors="replace") as fh:
                linhas = [l.rstrip() for l in fh.readlines()
                          if l.strip() and "DeprecationWarning" not in l and "text.replace" not in l
                          and "font = kw.pop" not in l]
        except Exception:
            linhas = []
        trecho = linhas[-25:]
        self.sessao.evento(f"FALHA: o módulo {self.motor_aberto['rotulo']} terminou com código {cod}. "
                           "Últimas linhas da saída de erros do motor:"
                           + ("".join(f"\n    {l}" for l in trecho) if trecho else " (vazia)"))
        resumo = "\n".join(trecho[-6:]) or "O motor não deixou mensagem de erro."
        messagebox.showerror("Falha no módulo",
                             f"O módulo {self.motor_aberto['rotulo']} foi encerrado com erro (código {cod}).\n\n"
                             f"{resumo}\n\nO motivo foi registrado no log da sessão. Para mais detalhes, "
                             "abra o Quartzo pelo QUARTZO_CONSOLE.bat e repita a operação.")

    # ------------------------------------------------------------- hashes
    def _pasta_selo(self, pasta):
        """Pasta irmã (fora da evidência) onde fica o selo de integridade da extração."""
        pasta = os.path.abspath(pasta)
        return os.path.join(os.path.dirname(pasta), os.path.basename(pasta) + "__SELO")

    def _destino_selo(self, pasta):
        """Onde gravar o selo de `pasta`: a pasta irmã ou, se ela cairia dentro da
        própria pasta (raiz de unidade), outra escolhida pelo operador. None se cancelar."""
        destino = self._pasta_selo(pasta)
        if not hashes.dentro_de(destino, pasta):
            return destino
        messagebox.showinfo("Selo de integridade",
                            "A pasta conferida é a raiz de uma unidade, e o selo não pode ser gravado dentro "
                            "dela. Escolha a seguir onde gravar o manifesto, fora da pasta conferida "
                            "(por exemplo, em outra unidade).")
        while True:
            escolha = filedialog.askdirectory(title="Onde gravar o selo (fora da pasta conferida)")
            if not escolha:
                return None
            if not hashes.dentro_de(escolha, pasta):
                return os.path.abspath(escolha)
            messagebox.showwarning("Selo de integridade", "Essa pasta fica dentro da pasta conferida. Escolha outra.")

    def gerar_manifesto(self, pasta=None, destino=None, automatico=False):
        if pasta is None:
            pasta = filedialog.askdirectory(initialdir=self.saida.get() or os.path.expanduser("~"),
                                            title="Pasta para o manifesto de hashes")
            if not pasta:
                return
            # o manifesto manual também fica fora da pasta conferida, que não recebe nada
            destino = self._destino_selo(pasta)
            if destino is None:
                return
        dados = (getattr(self, "dados_atual", None) or self.dados_caso()) if automatico else self.dados_caso()
        self.bt_hash.configure(state="disabled")
        self.barra.grid()
        self.barra.set(0)
        self.cancelar_hash.clear()
        self._selando = True
        # Se o log da sessão está dentro da pasta, ele é encerrado ANTES do cálculo:
        # o manifesto inclui o log e nada mais pode ser escrito nele depois.
        log_encerrado = None
        if self.sessao.contido_em(pasta):
            log_encerrado = self.sessao.fechar(
                "Encerramento do registro de sessão: a seguir é gerado o manifesto de hashes da pasta, "
                "que inclui este arquivo. Nada mais é gravado neste log; o resultado do manifesto e da "
                "reverificação fica em manifesto_hashes_<data_hora>.registro.txt.")
        inicio = agora_local()

        def progresso_de(etapa):
            def progresso(feitos, total, atual):
                frac = feitos / total if total else 1
                nome = os.path.basename(atual)
                self.no_principal(lambda: (self.barra.set(frac),
                                           self.status.configure(text=f"{etapa}: {feitos}/{total}  {nome[:70]}")))
            return progresso

        def tarefa():
            trabalho = None
            criou_destino = not os.path.isdir(destino)
            try:
                os.makedirs(destino, exist_ok=True)
                # o conjunto só aparece completo no destino ao final; enquanto é calculado ele
                # fica na subpasta _parcial, descartada se o processo não terminar
                trabalho = os.path.join(destino, "_parcial")
                if os.path.isdir(trabalho):
                    shutil.rmtree(trabalho, ignore_errors=True)
                csv_p, selo, n, tot = hashes.gerar_manifesto(pasta, progresso_de("Hashes"), self.cancelar_hash,
                                                             pasta_saida=trabalho)
                rev = hashes.reverificar_manifesto(csv_p, progresso_de("Reverificação"), self.cancelar_hash,
                                                   pasta=pasta)
                registro = hashes.gravar_registro(csv_p, selo, n, tot, rev, inicio, agora_local(), log_encerrado,
                                                  pasta_evidencia=pasta, dados_caso=dados)
                # finaliza: os três arquivos sobem para o destino e a subpasta some
                finais = {}
                for p in (csv_p, selo, registro):
                    alvo = os.path.join(destino, os.path.basename(p))
                    os.replace(p, alvo)
                    finais[p] = alvo
                os.rmdir(trabalho)
                trabalho = None
                csv_f, selo_f, reg_f = finais[csv_p], finais[selo], finais[registro]
                situacao = ("todos os arquivos conferiram na segunda leitura" if not rev[2]
                            else f"ATENÇÃO: {len(rev[2])} divergência(s) na segunda leitura")
                abertura = "Selagem automática da extração concluída.\n\n" if automatico else ""
                local = (f"\n\nO selo foi gravado fora da pasta conferida, em\n{destino}\n"
                         "e a pasta conferida não recebeu nenhum arquivo novo." if destino else "")
                msg = (f"{abertura}Manifesto gerado com {n} arquivo(s) ({tot / 1024 / 1024:.1f} MB).\n"
                       f"Reverificação: {situacao}.{local}\n\n{csv_f}\n\nRegistro e selo SHA-256:\n{reg_f}\n{selo_f}")
                self.sessao.evento(f"Manifesto de hashes gerado: {csv_f} ({n} arquivos). Reverificação: {situacao}. "
                                   f"Registro: {reg_f}. Selo: {selo_f}")
                titulo = "Selagem da extração" if automatico else "Manifesto de hashes"
                self.no_principal(lambda: (messagebox.showwarning if rev[2] else messagebox.showinfo)(titulo, msg))
                self.no_principal(lambda: self.status.configure(text=f"Manifesto gerado: {csv_f}"))
            except InterruptedError:
                self.no_principal(lambda: self.status.configure(text="Geração do manifesto cancelada."))
            except Exception as e:
                self.no_principal(lambda e=e: messagebox.showerror("Manifesto de hashes", f"Erro: {e}"))
            finally:
                self._selando = False
                if trabalho and os.path.isdir(trabalho):
                    shutil.rmtree(trabalho, ignore_errors=True)
                    # não deixa para trás uma pasta de selo vazia criada por esta tentativa
                    try:
                        if criou_destino and not os.listdir(destino):
                            os.rmdir(destino)
                    except OSError:
                        pass
                self.no_principal(lambda: (self.bt_hash.configure(state="normal"), self.barra.grid_remove()))
        threading.Thread(target=tarefa, daemon=True).start()

    def conferir_extracao(self):
        if getattr(self, "_selando", False) or getattr(self, "_conferindo", False):
            messagebox.showinfo("Conferir extração", "Aguarde terminar a operação em andamento.")
            return
        csv_p = filedialog.askopenfilename(
            title="Escolha o manifesto a conferir",
            filetypes=[("Manifesto de hashes", "manifesto_hashes_*.csv"), ("CSV", "*.csv"), ("Todos", "*.*")])
        if not csv_p:
            return
        pasta = filedialog.askdirectory(title="Pasta da evidência que o manifesto descreve")
        if not pasta:
            return
        # o relatório fica ao lado do manifesto; se o manifesto estiver dentro da própria
        # evidência (manifestos antigos), vai para a pasta irmã, para não alterá-la
        saida_relatorio = os.path.dirname(os.path.abspath(csv_p))
        if hashes.dentro_de(saida_relatorio, pasta):
            saida_relatorio = self._destino_selo(pasta)
            if saida_relatorio is None:
                return
        self._conferindo = True
        self.bt_conferir.configure(state="disabled")
        self.barra.grid()
        self.barra.set(0)
        self.cancelar_hash.clear()
        inicio = agora_local()

        def progresso(feitos, total, atual):
            frac = feitos / total if total else 1
            nome = os.path.basename(atual)
            self.no_principal(lambda: (self.barra.set(frac),
                                       self.status.configure(text=f"Conferindo: {feitos}/{total}  {nome[:70]}")))

        def tarefa():
            try:
                rev = hashes.reverificar_manifesto(csv_p, progresso, self.cancelar_hash, pasta=pasta)
                relatorio = hashes.gravar_conferencia(csv_p, pasta, rev, inicio, agora_local(),
                                                      pasta_saida=saida_relatorio)
                total, ok, div = rev
                self.sessao.evento(f"Conferência de extração: {ok} de {total} arquivo(s) conferem com {csv_p}. "
                                   f"Relatório: {relatorio}")
                if div:
                    corpo = "\n".join(f"- {rel}: {motivo}" for rel, motivo in div[:15])
                    extra = f"\n... e mais {len(div) - 15}." if len(div) > 15 else ""
                    msg = (f"ATENÇÃO: {len(div)} de {total} arquivo(s) DIVERGIRAM do manifesto.\n\n"
                           f"{corpo}{extra}\n\nRelatório: {relatorio}")
                    self.no_principal(lambda: messagebox.showwarning("Conferir extração", msg))
                else:
                    msg = (f"Conferência concluída: todos os {total} arquivo(s) conferem com o manifesto "
                           f"(tamanho, MD5, SHA-1 e SHA-256).\n\nRelatório: {relatorio}")
                    self.no_principal(lambda: messagebox.showinfo("Conferir extração", msg))
                self.no_principal(lambda: self.status.configure(text=f"Conferência concluída: {ok}/{total} íntegros."))
            except InterruptedError:
                self.no_principal(lambda: self.status.configure(text="Conferência cancelada."))
            except Exception as e:
                self.no_principal(lambda e=e: messagebox.showerror("Conferir extração", f"Erro: {e}"))
            finally:
                self._conferindo = False
                self.no_principal(lambda: (self.bt_conferir.configure(state="normal"), self.barra.grid_remove()))
        threading.Thread(target=tarefa, daemon=True).start()

    # -------------------------------------------------------------- whatsapp
    def abrir_whatsapp(self):
        janela = getattr(self, "janela_wa", None)
        if janela is not None and janela.winfo_exists():
            janela.lift()
            janela.focus()
            return
        # se acabou de haver extração, ela já vem como origem
        self.janela_wa = JanelaWhatsApp(self, getattr(self, "pasta_atual", None))

    # ----------------------------------------------------------------- sobre
    def sobre(self):
        origem = ler_origem_upstream()
        texto = (
            f"{NOME_LONGO}\nVersão {VERSAO}\n\n"
            "Fork unificado, com interface em português, de duas ferramentas livres de "
            "Christian Peter (prosch88):\n\n"
            f"• UFADE {ler_versao_motor('ios')} - Universal Forensic Apple Device Extractor\n"
            f"   {MOTORES['ios']['repo']}  (commit {origem.get('UFADE', {}).get('commit', '?')[:12]})\n"
            f"• ALEX {ler_versao_motor('android')} - Android Logical Extractor\n"
            f"   {MOTORES['android']['repo']}  (commit {origem.get('ALEX', {}).get('commit', '?')[:12]})\n\n"
            "Os motores mantêm a lógica de extração original, com os textos de interface e dos "
            "relatórios PDF traduzidos. No motor iOS foram corrigidas falhas encontradas em teste "
            "(lista em ferramentas/integrar.py e no README), todas marcadas com \"# QUARTZO\". "
            "Os formatos de saída (UFED/UFD, UFDR, CASE/UCO, TAR/ZIP) são os mesmos do original.\n\n"
            "Cadeia de custódia: ao término de cada coleta, o Quartzo sela a extração (MD5, "
            "SHA-1 e SHA-256 de cada arquivo, com segunda leitura) e grava o manifesto numa "
            "pasta irmã, sem alterar a evidência. Transcreva no documento oficial o SHA-256 do "
            "manifesto; o Quartzo não assina o manifesto nem obtém carimbo de tempo.\n\n"
            "Licença: GNU GPL v3 ou posterior. Sem qualquer garantia."
        )
        messagebox.showinfo("Sobre o Quartzo", texto)

    # ---------------------------------------------------- atualização dos motores
    def atualizar_motores(self):
        if self.processo is not None:
            messagebox.showwarning("Quartzo", "Feche o módulo de extração antes de atualizar os motores.")
            return
        if getattr(self, "_atualizando", False):
            return
        origem = ler_origem_upstream()

        def descr(base):
            d = origem.get(base, {})
            return (f"{base}: commit {d.get('commit', '?')[:12]} "
                    f"({(d.get('data_commit') or '?')[:10]}; obtido em {d.get('obtido_em', '?')})")

        if not messagebox.askyesno(
                "Atualizar motores",
                "Esta ação baixa o código mais recente do UFADE e do ALEX dos repositórios originais, "
                "regenera os motores traduzidos e refaz a base de integridade do código.\n\n"
                "Versões em uso agora:\n" + descr("UFADE") + "\n" + descr("ALEX") + "\n\n"
                "Requer conexão com a internet e, de preferência, o Git instalado.\n\n"
                "Não atualize às vésperas nem logo após uma coleta real: para um caso concreto, mantenha "
                "a versão congelada e registre no relatório o commit utilizado.\n\n"
                "Deseja atualizar agora?"):
            return
        self._atualizando = True
        self.link_motores.configure(text="Atualizando motores...", text_color=COR_SUAVE)
        self.status.configure(text="Atualizando motores a partir dos repositórios originais...")
        self.barra.grid()
        self.barra.configure(mode="indeterminate")
        self.barra.start()

        def tarefa():
            script = os.path.join(RAIZ, "ferramentas", "sincronizar.py")
            env = dict(os.environ)
            env["QUARTZO_SILENCIOSO"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            try:
                r = subprocess.run([sys.executable, script], cwd=RAIZ, env=env, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", creationflags=CRIAR_SEM_JANELA)
                saida, ok = (r.stdout or "") + (r.stderr or ""), r.returncode == 0
            except Exception as e:
                saida, ok = str(e), False
            self.no_principal(lambda: self._fim_atualizacao(ok, saida))
        threading.Thread(target=tarefa, daemon=True).start()

    def _fim_atualizacao(self, ok, saida):
        self._atualizando = False
        self.barra.stop()
        self.barra.configure(mode="determinate")
        self.barra.grid_remove()
        self.link_motores.configure(text="Atualizar motores (UFADE/ALEX)", text_color=COR_APPLE)
        self.verificar_integridade()
        linhas = saida.splitlines()
        trecho = "\n".join(l for l in linhas if l.strip())[-1500:]
        if ok:
            origem = ler_origem_upstream()
            novo = "\n".join(
                f"{b}: commit {origem.get(b, {}).get('commit', '?')[:12]} "
                f"({(origem.get(b, {}).get('data_commit') or '?')[:10]})" for b in ("UFADE", "ALEX"))
            # o relatório do tradutor marca cada string pendente com "FALTA:" no início da linha
            faltas = [l for l in linhas if l.startswith("FALTA:")]
            resumo = [l.strip() for l in linhas if l.startswith(("[ufade]", "[alex]"))]
            aviso = ""
            if faltas:
                aviso = (f"\n\nATENÇÃO: {len(faltas)} string(s) nova(s) de interface ainda sem tradução. "
                         "Elas aparecem em inglês até serem acrescentadas em i18n/ e o motor ser "
                         "regenerado (ATUALIZAR_MOTORES.bat --offline):\n" + "\n".join(faltas[:15]))
            self.status.configure(text="Motores atualizados.")
            messagebox.showinfo("Atualização dos motores",
                                "Motores atualizados e base de integridade refeita.\n\n" + novo
                                + (("\n\n" + "\n".join(resumo)) if resumo else "") + aviso)
        else:
            self.status.configure(text="Falha ao atualizar os motores.")
            messagebox.showerror(
                "Atualização dos motores",
                "Não foi possível concluir a atualização. Causas comuns: sem conexão com a internet, Git "
                "ausente, ou o autor original alterou um trecho em que o Quartzo faz ajuste de integração "
                "(nesse caso, nada em motores/ é sobrescrito).\n\n" + (trecho or ""))

    def ao_fechar(self):
        if self.processo is not None:
            messagebox.showwarning("Quartzo", "Feche o módulo de extração antes de sair.")
            return
        pendentes = []
        if getattr(self, "_selando", False):
            pendentes.append("a selagem/manifesto de hashes")
        if getattr(self, "_conferindo", False):
            pendentes.append("a conferência da extração")
        if getattr(self, "_atualizando", False):
            pendentes.append("a atualização dos motores")
        jw = getattr(self, "janela_wa", None)
        if jw is not None and jw.winfo_exists() and getattr(jw, "trabalhando", False):
            pendentes.append("a descriptografia do WhatsApp")
        if pendentes:
            messagebox.showwarning("Quartzo", "Há trabalho em andamento (" + ", ".join(pendentes) +
                                   "). Aguarde terminar antes de fechar o Quartzo, para não deixar um "
                                   "resultado pela metade. A janela do WhatsApp tem o próprio botão de cancelar.")
            return
        self.cancelar_hash.set()
        self.destroy()


if __name__ == "__main__":
    app = Lancador()
    app.mainloop()
