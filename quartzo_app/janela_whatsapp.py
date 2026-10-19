# -*- coding: utf-8 -*-
"""Janela do lançador para descriptografar backups do WhatsApp com a chave de 64 dígitos.

A lógica fica em whatsapp_cripto.py; aqui só há a interface. O trabalho pesado roda
numa thread e devolve as atualizações de tela pela fila do lançador (no_principal).

Copyright (C) 2026 Jean Carlos Fonseca. GNU GPL v3 ou posterior. Sem qualquer garantia.
"""
import os
import re
import sys
import threading
import zipfile
from tkinter import filedialog, messagebox

import customtkinter as ctk

from quartzo_app import whatsapp_cripto as wa

COR_FUNDO = "#1c2127"
COR_PAINEL = "#252c34"
COR_BORDA = "#34404c"
COR_TEXTO = "#dbe2ea"
COR_SUAVE = "#8794a3"
COR_WHATS = "#2e7d6b"
COR_ALERTA = "#c9862b"
COR_ERRO = "#c0504d"
COR_OK = "#4c9a6a"

AVISO = ("A chave fica só na memória durante o processamento e é apagada da tela ao final. O registro "
         "guarda apenas a impressão digital (SHA-256 da chave), que identifica a chave usada sem revelá-la. "
         "Os resultados são gravados fora da pasta da extração, que não é alterada. Ativar o backup "
         "criptografado no aparelho para obter a chave é alteração do estado do aparelho: só com "
         "autorização judicial e com registro no relatório.")


def _formatar_tamanho(n):
    for unidade in ("B", "KB", "MB", "GB"):
        if n < 1024 or unidade == "GB":
            return f"{n:.0f} {unidade}" if unidade == "B" else f"{n:.1f} {unidade}".replace(".", ",")
        n /= 1024


class JanelaWhatsApp(ctk.CTkToplevel):
    def __init__(self, app, origem_inicial=None):
        super().__init__(app)
        self.app = app
        self.title("WhatsApp: descriptografar backup com a chave")
        self.geometry("860x700")
        self.minsize(780, 620)
        self.configure(fg_color=COR_FUNDO)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.fechar)

        self.achados = []
        self.marcas = []
        self.trabalhando = False
        self.cancelar = threading.Event()
        self.fonte = ctk.CTkFont(size=13)
        self.fonte_peq = ctk.CTkFont(size=12)
        self.fonte_secao = ctk.CTkFont(size=14, weight="bold")
        self.fonte_mono = ctk.CTkFont(family="Consolas" if sys.platform == "win32" else "monospace", size=13)

        self._montar()
        if origem_inicial:
            self._definir_origem(origem_inicial)
        self.after(150, self.lift)

    # ------------------------------------------------------------- layout
    def _rotulo(self, pai, texto):
        ctk.CTkLabel(pai, text=texto, font=self.fonte_secao, text_color=COR_TEXTO, anchor="w").pack(
            fill="x", padx=16, pady=(12, 4))

    def _montar(self):
        corpo = ctk.CTkFrame(self, fg_color=COR_PAINEL, corner_radius=10)
        corpo.pack(fill="both", expand=True, padx=16, pady=(16, 8))

        self._rotulo(corpo, "1  Origem: ZIP da extração, pasta ou arquivo .crypt14 / .crypt15")
        linha = ctk.CTkFrame(corpo, fg_color="transparent")
        linha.pack(fill="x", padx=16)
        linha.grid_columnconfigure(0, weight=1)
        self.origem = ctk.CTkEntry(linha, height=32, font=self.fonte)
        self.origem.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(linha, text="ZIP ou arquivo", width=120, height=32, fg_color=COR_BORDA,
                      command=self.escolher_arquivo).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(linha, text="Pasta", width=80, height=32, fg_color=COR_BORDA,
                      command=self.escolher_pasta).grid(row=0, column=2, padx=(8, 0))

        self.lista = ctk.CTkScrollableFrame(corpo, height=150, fg_color=COR_FUNDO)
        self.lista.pack(fill="both", expand=True, padx=16, pady=(8, 2))
        self.resumo = ctk.CTkLabel(corpo, text="Escolha a origem para localizar os bancos cifrados.",
                                   font=self.fonte_peq, text_color=COR_SUAVE, anchor="w", justify="left")
        self.resumo.pack(fill="x", padx=16)

        self._rotulo(corpo, "2  Chave do backup")
        linha = ctk.CTkFrame(corpo, fg_color="transparent")
        linha.pack(fill="x", padx=16)
        linha.grid_columnconfigure(0, weight=1)
        self.chave = ctk.CTkEntry(linha, height=34, font=self.fonte_mono, show="•",
                                  placeholder_text="64 dígitos exibidos pelo WhatsApp (espaços são ignorados)")
        self.chave.grid(row=0, column=0, sticky="ew")
        self.chave.bind("<KeyRelease>", lambda e: self._contar())
        self.chave.bind("<<Paste>>", lambda e: self.after(10, self._contar))
        self.mostrar = ctk.CTkCheckBox(linha, text="Mostrar", width=80, font=self.fonte_peq,
                                       command=lambda: self.chave.configure(show="" if self.mostrar.get() else "•"))
        self.mostrar.grid(row=0, column=1, padx=(8, 0))
        self.contador = ctk.CTkLabel(corpo, text="0 de 64 dígitos", font=self.fonte_peq,
                                     text_color=COR_SUAVE, anchor="w")
        self.contador.pack(fill="x", padx=16)

        linha = ctk.CTkFrame(corpo, fg_color="transparent")
        linha.pack(fill="x", padx=16, pady=(6, 0))
        linha.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(linha, text="ou arquivo de chave (opcional):", font=self.fonte_peq,
                     text_color=COR_SUAVE).grid(row=0, column=0, padx=(0, 8))
        self.arq_chave = ctk.CTkEntry(linha, height=30, font=self.fonte_peq,
                                      placeholder_text="key (crypt14) ou encrypted_backup.key (crypt15)")
        self.arq_chave.grid(row=0, column=1, sticky="ew")
        ctk.CTkButton(linha, text="Procurar", width=90, height=30, fg_color=COR_BORDA,
                      command=self.escolher_arquivo_chave).grid(row=0, column=2, padx=(8, 0))

        self._rotulo(corpo, "3  Destino (fora da pasta da extração)")
        linha = ctk.CTkFrame(corpo, fg_color="transparent")
        linha.pack(fill="x", padx=16)
        linha.grid_columnconfigure(0, weight=1)
        self.destino = ctk.CTkEntry(linha, height=32, font=self.fonte)
        self.destino.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(linha, text="Procurar", width=90, height=32, fg_color=COR_BORDA,
                      command=self.escolher_destino).grid(row=0, column=1, padx=(8, 0))

        ctk.CTkLabel(corpo, text=AVISO, font=self.fonte_peq, text_color=COR_SUAVE, wraplength=780,
                     justify="left", anchor="w").pack(fill="x", padx=16, pady=(12, 14))

        rodape = ctk.CTkFrame(self, fg_color="transparent")
        rodape.pack(fill="x", padx=16, pady=(0, 14))
        rodape.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(rodape, text="", font=self.fonte_peq, text_color=COR_SUAVE, anchor="w")
        self.status.grid(row=0, column=0, sticky="ew")
        self.barra = ctk.CTkProgressBar(rodape, height=10)
        self.barra.set(0)
        self.barra.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self.bt_fechar = ctk.CTkButton(rodape, text="Fechar", width=110, height=36, fg_color=COR_BORDA,
                                       command=self.fechar)
        self.bt_fechar.grid(row=0, column=1, padx=(8, 0))
        self.bt_ir = ctk.CTkButton(rodape, text="Descriptografar", width=150, height=36, fg_color=COR_WHATS,
                                   hover_color="#256556", font=self.fonte_secao, command=self.iniciar)
        self.bt_ir.grid(row=0, column=2, padx=(8, 0))

    # ------------------------------------------------------------- origem
    def escolher_arquivo(self):
        p = filedialog.askopenfilename(parent=self, title="ZIP da extração ou arquivo cifrado do WhatsApp",
                                       filetypes=[("ZIP ou backup do WhatsApp", "*.zip *.crypt14 *.crypt15"),
                                                  ("Todos os arquivos", "*.*")])
        if p:
            self._definir_origem(os.path.normpath(p))

    def escolher_pasta(self):
        p = filedialog.askdirectory(parent=self, title="Pasta da extração (ou a pasta Databases do WhatsApp)")
        if p:
            self._definir_origem(os.path.normpath(p))

    def escolher_arquivo_chave(self):
        p = filedialog.askopenfilename(parent=self, title="Arquivo de chave do WhatsApp")
        if p:
            self.arq_chave.delete(0, "end")
            self.arq_chave.insert(0, os.path.normpath(p))

    def escolher_destino(self):
        p = filedialog.askdirectory(parent=self, title="Pasta onde gravar os bancos descriptografados")
        if p:
            self.destino.delete(0, "end")
            self.destino.insert(0, os.path.normpath(p))

    def _definir_origem(self, caminho):
        self.origem.delete(0, "end")
        self.origem.insert(0, caminho)
        self.destino.delete(0, "end")
        self.destino.insert(0, wa.destino_sugerido(caminho))
        self.resumo.configure(text="Procurando arquivos cifrados... (num ZIP grande isto leva alguns segundos)",
                              text_color=COR_SUAVE)
        for w in self.lista.winfo_children():
            w.destroy()
        self.achados, self.marcas = [], []

        def tarefa():
            try:
                achados = wa.localizar(caminho)
                formatos = {}
                zf = None
                try:
                    for o in achados:
                        if o.zip_caminho and zf is None:
                            zf = zipfile.ZipFile(o.zip_caminho)
                        try:
                            with o.abrir(zf) as fh:
                                formatos[id(o)] = wa.ler_cabecalho(fh)["formato"]
                        except wa.ErroWhatsApp:
                            formatos[id(o)] = "crypt12" if o.relativo.lower().endswith(".crypt12") else "?"
                finally:
                    if zf is not None:
                        zf.close()
                self.app.no_principal(lambda: self._mostrar_achados(achados, formatos))
            except Exception as e:
                self.app.no_principal(lambda e=e: self.resumo.configure(text=f"Erro ao ler a origem: {e}",
                                                                        text_color=COR_ERRO))
        threading.Thread(target=tarefa, daemon=True).start()

    def _mostrar_achados(self, achados, formatos):
        if not self.winfo_exists():
            return
        self.achados = achados
        self.marcas = []
        for o in achados:
            fmt = formatos.get(id(o), "?")
            var = ctk.BooleanVar(value=fmt in ("crypt15", "crypt14"))
            texto = f"{fmt:<8} {_formatar_tamanho(o.tamanho):>10}   {o.pacote:<18} {o.relativo}"
            ctk.CTkCheckBox(self.lista, text=texto, variable=var, font=self.fonte_mono,
                            text_color=COR_TEXTO).pack(fill="x", anchor="w", pady=1)
            self.marcas.append((var, o, fmt))
        n15 = sum(1 for _, _, f in self.marcas if f == "crypt15")
        n14 = sum(1 for _, _, f in self.marcas if f == "crypt14")
        if not achados:
            self.resumo.configure(text="Nenhum arquivo .crypt14 ou .crypt15 encontrado nesta origem.",
                                  text_color=COR_ALERTA)
            return
        partes = [f"{len(achados)} arquivo(s) cifrado(s)."]
        if n15:
            partes.append(f"{n15} crypt15: abrem com a chave de 64 dígitos.")
        if n14:
            partes.append(f"{n14} crypt14: criptografia local, abrem só com o arquivo key da pasta interna "
                          "do WhatsApp (root); a chave de 64 dígitos não serve para eles.")
        self.resumo.configure(text="  ".join(partes), text_color=COR_ALERTA if n14 and not n15 else COR_SUAVE)

    # -------------------------------------------------------------- chave
    def _contar(self):
        bruto = self.chave.get()
        limpo = re.sub(r"[\s\-]", "", bruto)
        invalidos = re.sub(r"[0-9a-fA-F]", "", limpo)
        if invalidos:
            self.contador.configure(text=f"{len(limpo)} de 64 dígitos  ·  caractere(s) inválido(s): "
                                         f"{' '.join(sorted(set(invalidos)))}", text_color=COR_ERRO)
        elif len(limpo) == 64:
            self.contador.configure(text="64 de 64 dígitos", text_color=COR_OK)
        else:
            self.contador.configure(text=f"{len(limpo)} de 64 dígitos",
                                    text_color=COR_ERRO if len(limpo) > 64 else COR_SUAVE)

    def _chaves(self):
        chaves = []
        if self.chave.get().strip():
            chaves.append(wa.chave_de_texto(self.chave.get()))
        if self.arq_chave.get().strip():
            chaves.append(wa.chave_de_arquivo(self.arq_chave.get().strip()))
        if not chaves:
            raise wa.ErroChave("Informe a chave de 64 dígitos ou um arquivo de chave.")
        return chaves

    # ------------------------------------------------------------ execução
    def iniciar(self):
        if self.trabalhando:
            return
        origem = self.origem.get().strip()
        destino = self.destino.get().strip()
        selecionados = [o for var, o, _ in self.marcas if var.get()]
        try:
            if not origem or not os.path.exists(origem):
                raise wa.ErroWhatsApp("Escolha a origem.")
            if not selecionados:
                raise wa.ErroWhatsApp("Marque ao menos um arquivo na lista.")
            if not destino:
                raise wa.ErroWhatsApp("Escolha a pasta de destino.")
            wa.conferir_destino(origem, destino)
            chaves = self._chaves()
        except (wa.ErroWhatsApp, OSError) as e:
            messagebox.showwarning("WhatsApp", str(e), parent=self)
            return

        self.trabalhando = True
        self.cancelar.clear()
        self.bt_ir.configure(state="disabled")
        self.bt_fechar.configure(text="Cancelar")
        self.barra.set(0)
        dados_caso = self.app.dados_caso() if hasattr(self.app, "dados_caso") else None

        def progresso(i, n, item, feitos, total):
            frac = ((i - 1) + (feitos / total if total else 1)) / n
            nome = os.path.basename(item.relativo)
            self.app.no_principal(lambda: self._progresso(frac, f"[{i}/{n}] {nome}  "
                                                                f"{feitos * 100 // max(total, 1)}%"))

        def tarefa():
            try:
                pasta, res, reg = wa.processar(origem, destino, chaves, selecionados, dados_caso,
                                               progresso, self.cancelar)
                self.app.no_principal(lambda: self._concluir(pasta, res, reg, chaves))
            except Exception as e:
                # o nome "e" deixa de existir ao sair do except; por isso vai como argumento
                self.app.no_principal(lambda e=e: self._falhar(e))
        threading.Thread(target=tarefa, daemon=True).start()

    def _progresso(self, frac, texto):
        if self.winfo_exists():
            self.barra.set(frac)
            self.status.configure(text=texto, text_color=COR_SUAVE)

    def _encerrar_trabalho(self):
        self.trabalhando = False
        if self.winfo_exists():
            self.bt_ir.configure(state="normal")
            self.bt_fechar.configure(text="Fechar")

    def _falhar(self, erro):
        self._encerrar_trabalho()
        if self.winfo_exists():
            self.status.configure(text=f"Erro: {erro}", text_color=COR_ERRO)
            messagebox.showerror("WhatsApp", str(erro), parent=self)

    def _concluir(self, pasta, res, reg, chaves):
        self._encerrar_trabalho()
        ok = [r for r in res if r["situacao"] == "OK"]
        falhas = [r for r in res if r["situacao"] != "OK"]
        sessao = getattr(self.app, "sessao", None)
        if sessao is not None:
            impressoes = ", ".join(c.impressao_digital()[:16] for c in chaves)
            sessao.evento(f"WhatsApp: {len(ok)} de {len(res)} banco(s) descriptografado(s) em {pasta} "
                          f"(impressão digital da chave {impressoes}...). Registro: {reg}")
        if ok:
            # a chave sai da tela assim que deixa de ser necessária
            self.chave.delete(0, "end")
            self._contar()
        linhas = [f"{len(ok)} de {len(res)} arquivo(s) descriptografado(s).", ""]
        for r in ok:
            extra = ""
            sq = r.get("sqlite") or {}
            if "contagens" in sq:
                msgs = sq["contagens"].get("message", sq["contagens"].get("messages"))
                if msgs is not None:
                    extra = f"  ({msgs} mensagens)"
                elif "wa_contacts" in sq["contagens"]:
                    extra = f"  ({sq['contagens']['wa_contacts']} contatos)"
            linhas.append(f"OK  {os.path.basename(r['destino'])}{extra}")
        for r in falhas:
            linhas.append(f"{r['situacao']}  {os.path.basename(r['relativo'])}: {r.get('erro', '')}")
        linhas += ["", f"Resultados e registro em:\n{pasta}"]
        self.barra.set(1 if ok else 0)
        self.status.configure(text=f"Concluído: {len(ok)} de {len(res)}.",
                              text_color=COR_OK if ok and not falhas else COR_ALERTA)
        caixa = messagebox.showinfo if ok and not falhas else messagebox.showwarning
        caixa("WhatsApp", "\n".join(linhas), parent=self)
        if ok and sys.platform == "win32":
            try:
                os.startfile(pasta)
            except OSError:
                pass

    def fechar(self):
        if self.trabalhando:
            if messagebox.askyesno("WhatsApp", "Interromper o processamento? O arquivo em andamento é "
                                               "descartado; os já concluídos são mantidos.", parent=self):
                self.cancelar.set()
            return
        self.chave.delete(0, "end")
        self.destroy()
