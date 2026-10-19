# -*- coding: utf-8 -*-
"""
Quartzo - janela "Acompanhamento da extração".

Copyright (C) 2026 Jean Carlos Fonseca. GNU GPL v3 ou posterior.

Roda no processo do lançador (separado do motor), de modo que continua
respondendo mesmo quando a interface do motor está ocupada. A cada segundo lê
o arquivo de estado escrito pela sonda do motor (quartzo_progresso.py) e, a
cada cinco segundos, mede de forma independente a pasta da extração: volume
gravado, arquivos extraídos (os que estão na pasta mais as entradas gravadas
dentro dos ZIP, lidas por quartzo_app/leitor_zip.py), ritmo em arquivos por
segundo, tamanho médio recente e a pasta do aparelho em extração no momento.

Nada é gravado na pasta da extração por esta janela, e os ZIP são lidos em
modo compartilhado, sem bloquear o motor. Os eventos de início e término de
cada etapa, e os trechos de muitos arquivos pequenos, vão para o log da sessão.
"""
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta

import customtkinter as ctk

from quartzo_app import ICONE_ICO
from quartzo_app.leitor_zip import LeitorZip
from quartzo_app.progresso import (Estimador, formatar_bytes, formatar_decimal, formatar_duracao,
                                   formatar_inteiro)

COR_FUNDO = "#1c2127"
COR_PAINEL = "#252c34"
COR_TEXTO = "#dbe2ea"
COR_SUAVE = "#8794a3"
COR_OK = "#4c9a6a"
COR_ALERTA = "#c9862b"
COR_CRISTAL = "#8fd3e8"

SITUACOES = {
    "aguardando": ("Aguardando início da transferência", COR_SUAVE),
    "calculando": ("Calculando estimativa…", COR_SUAVE),
    "estimado": ("Em andamento", COR_OK),
    "concluindo": ("Etapa concluída", COR_OK),
    "parado": ("Sem avanço", COR_ALERTA),
    "sem_total": ("Em andamento (total desconhecido)", COR_OK),
}

# Trecho de "muitos arquivos pequenos": ritmo e média medidos no último minuto.
PEQUENOS_RITMO = 2.0              # arquivos por segundo, no mínimo
PEQUENOS_MEDIA = 256 * 1024       # tamanho médio abaixo disto
PEQUENOS_ENTRAR_S = 30            # condição contínua por 30 s para declarar o trecho
PEQUENOS_SAIR_S = 60              # e por 60 s fora dela para encerrá-lo
JANELA_MEDIDOR_S = 60


class MedidorPasta:
    """Mede a pasta da extração numa thread própria, a cada cinco segundos."""

    def __init__(self, pasta):
        self.pasta = pasta
        self.bytes = 0
        self.itens = 0            # arquivos na pasta (sem .zip e .log) + entradas dos ZIP
        self.local_atual = None   # pasta do aparelho (ou da extração) em gravação agora
        self.historico = []       # (instante, bytes, itens)
        self.zips = {}            # caminho -> LeitorZip
        self._trava = threading.Lock()
        self._parar = threading.Event()
        threading.Thread(target=self._laco, name="quartzo-medidor", daemon=True).start()

    def _varrer(self):
        total = n = 0
        zips = []
        recente_t, recente_p = 0.0, None
        pilha = [self.pasta]
        while pilha:
            p = pilha.pop()
            try:
                with os.scandir(p) as it:
                    for e in it:
                        try:
                            if e.is_dir(follow_symlinks=False):
                                pilha.append(e.path)
                            elif e.is_file(follow_symlinks=False):
                                nome = e.name.lower()
                                if nome.endswith(".log"):
                                    # logs do Quartzo e do motor crescem mesmo com o aparelho
                                    # parado; não são dados extraídos e mascarariam um travamento
                                    continue
                                if nome.endswith(".zip"):
                                    # No Windows, o tamanho que a listagem da pasta informa
                                    # para um arquivo ainda aberto fica defasado; o ZIP em
                                    # gravação é consultado direto (sem bloqueá-lo).
                                    total += os.stat(e.path).st_size
                                    zips.append(e.path)
                                else:
                                    st = e.stat(follow_symlinks=False)
                                    total += st.st_size
                                    n += 1
                                    # no Windows, st_ctime é a data de criação
                                    if st.st_ctime > recente_t:
                                        recente_t, recente_p = st.st_ctime, e.path
                        except OSError:
                            pass
            except OSError:
                pass
        itens_zip, local_zip = 0, None
        for z in zips:
            leitor = self.zips.get(z)
            if leitor is None:
                leitor = self.zips[z] = LeitorZip(z)
            if leitor.atualizar() and leitor.ultimo_nome:
                local_zip = leitor.ultimo_nome
            itens_zip += leitor.arquivos
        local = None
        if local_zip:
            local = os.path.dirname(local_zip) or local_zip
        elif recente_p and time.time() - recente_t < 30:
            local = os.path.dirname(os.path.relpath(recente_p, self.pasta)).replace("\\", "/") or "."
        return total, n + itens_zip, local

    def medir_agora(self):
        with self._trava:
            b, itens, local = self._varrer()
            agora = time.time()
            self.bytes, self.itens = b, itens
            if local:
                self.local_atual = local
            self.historico.append((agora, b, itens))
            self.historico = [h for h in self.historico if agora - h[0] <= 2 * JANELA_MEDIDOR_S]

    def _laco(self):
        while not self._parar.is_set():
            try:
                self.medir_agora()
            except Exception:
                pass
            self._parar.wait(5)

    def _extremos(self, seg=JANELA_MEDIDOR_S):
        h = list(self.historico)
        if len(h) < 2:
            return None
        ult = h[-1]
        janela = [x for x in h if ult[0] - x[0] <= seg]
        if len(janela) < 2 or ult[0] - janela[0][0] < 9:
            return None
        return janela[0], ult

    def taxa(self):
        """Bytes por segundo no último minuto, ou None."""
        e = self._extremos()
        if not e:
            return None
        (t0, b0, _), (t1, b1, _) = e
        return max(0.0, (b1 - b0) / (t1 - t0))

    def ritmo_itens(self):
        """Arquivos extraídos por segundo no último minuto, ou None."""
        e = self._extremos()
        if not e:
            return None
        (t0, _, i0), (t1, _, i1) = e
        return max(0.0, (i1 - i0) / (t1 - t0))

    def media_recente(self):
        """Tamanho médio dos arquivos extraídos no último minuto, ou None."""
        e = self._extremos()
        if not e:
            return None
        (_, b0, i0), (_, b1, i1) = e
        if i1 - i0 < 5:
            return None
        return max(0.0, (b1 - b0) / (i1 - i0))

    def houve_atividade(self, seg=JANELA_MEDIDOR_S):
        e = self._extremos(seg)
        if not e:
            return False
        (_, b0, i0), (_, b1, i1) = e
        return b1 > b0 or i1 > i0

    def parar(self):
        self._parar.set()


class JanelaAcompanhamento(ctk.CTkToplevel):
    def __init__(self, mestre, arquivo_estado, pasta, rotulo_motor, dados_caso, registrar):
        super().__init__(mestre)
        self.arquivo = arquivo_estado
        self.pasta = pasta
        self.registrar = registrar  # função(texto) -> grava no log da sessão
        self.estimador = Estimador()
        self.medidor = MedidorPasta(pasta)
        self.fase_atual = None
        self.fase_registrada = None
        self.fase_encerrada = None
        self.inicio_sessao = None
        self.inicio_fase = None
        self.bytes_inicio_fase = 0
        self.itens_inicio_fase = 0
        self.mostrada = False
        self.fechada = False
        self.ultimo_estado = {}
        self.ultima_fracao_fase = None  # último percentual exibido na etapa em registro
        self.fase_oscilou = False        # a barra girou em ciclos (sem total) nesta etapa
        self.pequenos = False            # trecho de muitos arquivos pequenos em curso
        self._pequenos_desde = None
        self._pequenos_inicio = None
        self._fora_pequenos_desde = None

        self.title(f"Acompanhamento da extração · {rotulo_motor}")
        self.configure(fg_color=COR_FUNDO)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.recolher)
        if sys.platform == "win32" and os.path.isfile(ICONE_ICO):
            # o CTkToplevel aplica o ícone padrão dele aos 200 ms; o nosso vem depois
            self.after(260, self._aplicar_icone)
        f_tit = ctk.CTkFont(size=16, weight="bold")
        f_num = ctk.CTkFont(size=22, weight="bold")
        f = ctk.CTkFont(size=13)
        f_peq = ctk.CTkFont(size=12)
        f_mono = ctk.CTkFont(family="Consolas" if sys.platform == "win32" else "monospace", size=12)

        topo = ctk.CTkFrame(self, fg_color=COR_FUNDO)
        topo.pack(fill="x", padx=16, pady=(14, 4))
        self.lb_situacao = ctk.CTkLabel(topo, text="Aguardando início da transferência", font=f_tit,
                                        text_color=COR_TEXTO, anchor="w")
        self.lb_situacao.pack(fill="x")
        caso = dados_caso.get("numero") or "sem nº de procedimento"
        item = dados_caso.get("evidencia")
        ctk.CTkLabel(topo, text=f"{caso}" + (f"  ·  {item}" if item else "") + f"  ·  módulo {rotulo_motor}",
                     font=f_peq, text_color=COR_SUAVE, anchor="w").pack(fill="x")

        painel = ctk.CTkFrame(self, fg_color=COR_PAINEL, corner_radius=10)
        painel.pack(fill="x", padx=16, pady=6)
        self.lb_etapa = ctk.CTkLabel(painel, text="—", font=f, text_color=COR_TEXTO, anchor="w",
                                     justify="left", wraplength=440)
        self.lb_etapa.pack(fill="x", padx=14, pady=(12, 6))
        self.barra = ctk.CTkProgressBar(painel, height=14, progress_color=COR_CRISTAL)
        self.barra.set(0)
        self.barra.pack(fill="x", padx=14)
        self.modo_barra = "determinate"
        linha_pct = ctk.CTkFrame(painel, fg_color="transparent")
        linha_pct.pack(fill="x", padx=14, pady=(6, 0))
        self.lb_pct = ctk.CTkLabel(linha_pct, text="", font=f_num, text_color=COR_TEXTO, anchor="w")
        self.lb_pct.pack(side="left")
        self.lb_selo = ctk.CTkLabel(linha_pct, text="", font=ctk.CTkFont(size=11, weight="bold"),
                                    text_color=COR_FUNDO, fg_color="transparent", corner_radius=6)
        self.lb_selo.pack(side="right", pady=(4, 0))

        grade = ctk.CTkFrame(painel, fg_color="transparent")
        grade.pack(fill="x", padx=14, pady=(4, 6))
        grade.grid_columnconfigure((0, 1), weight=1, uniform="g")
        self.campos = {}
        itens = [("restante", "Tempo restante (etapa)"), ("termino", "Término previsto"),
                 ("decorrido_fase", "Duração da etapa"), ("decorrido", "Tempo total decorrido"),
                 ("itens", "Arquivos extraídos"), ("ritmo", "Ritmo (último minuto)"),
                 ("gravado", "Gravado na pasta"), ("taxa", "Taxa de gravação")]
        for i, (k, rotulo) in enumerate(itens):
            cel = ctk.CTkFrame(grade, fg_color="transparent")
            cel.grid(row=i // 2, column=i % 2, sticky="ew", pady=4)
            ctk.CTkLabel(cel, text=rotulo, font=f_peq, text_color=COR_SUAVE, anchor="w").pack(fill="x")
            lb = ctk.CTkLabel(cel, text="—", font=f, text_color=COR_TEXTO, anchor="w")
            lb.pack(fill="x")
            self.campos[k] = lb

        ctk.CTkLabel(painel, text="Pasta em extração agora", font=f_peq, text_color=COR_SUAVE,
                     anchor="w").pack(fill="x", padx=14)
        self.lb_local = ctk.CTkLabel(painel, text="—", font=f_mono, text_color=COR_CRISTAL, anchor="w",
                                     justify="left", wraplength=440)
        self.lb_local.pack(fill="x", padx=14, pady=(0, 12))

        self.lb_nota = ctk.CTkLabel(self, text="", font=f_peq, text_color=COR_SUAVE, anchor="w",
                                    justify="left", wraplength=460)
        self.lb_nota.pack(fill="x", padx=16)
        ctk.CTkLabel(self, text="Não desconecte nem bloqueie o aparelho até o fim da extração.",
                     font=f_peq, text_color=COR_ALERTA, anchor="w").pack(fill="x", padx=16, pady=(6, 0))

        rod = ctk.CTkFrame(self, fg_color="transparent")
        rod.pack(fill="x", padx=16, pady=(8, 14))
        self.sempre_visivel = ctk.CTkCheckBox(rod, text="Sempre visível", font=f_peq,
                                              command=self._aplicar_topo)
        self.sempre_visivel.select()
        self.sempre_visivel.pack(side="left")
        ctk.CTkButton(rod, text="Recolher", width=90, fg_color="#34404c", command=self.recolher).pack(side="right")

        self.withdraw()
        self._posicionar()
        self.after(1000, self._ciclo)

    # --------------------------------------------------------------- janela
    def _aplicar_icone(self):
        try:
            self.iconbitmap(ICONE_ICO)
        except Exception:
            pass

    def _posicionar(self):
        self.update_idletasks()
        try:
            escala = self._get_window_scaling()  # reqheight vem em pixels físicos
        except Exception:
            escala = 1.0
        # + espaço para as notas de até cinco linhas que aparecem durante a extração
        w, h = 500, int(self.winfo_reqheight() / escala) + 96
        x = max(0, self.winfo_screenwidth() - w - 24)
        self.geometry(f"{w}x{h}+{x}+40")

    def _aplicar_topo(self):
        try:
            self.attributes("-topmost", bool(self.sempre_visivel.get()))
        except Exception:
            pass

    def recolher(self):
        self.iconify()

    def mostrar(self):
        self.deiconify()
        self._aplicar_topo()
        self.lift()
        self.mostrada = True

    def encerrar(self, motivo):
        """Chamado pelo lançador quando o motor é fechado."""
        if self.fechada:
            return
        self.fechada = True
        try:
            self.medidor.medir_agora()  # números finais, não os de até 5 s atrás
        except Exception:
            pass
        self._encerrar_pequenos(time.time())
        self._registrar_fim_fase(motivo)
        if self.inicio_sessao:
            total = time.time() - self.inicio_sessao
            self.registrar(f"Acompanhamento: tempo total entre a primeira e a última atividade "
                           f"registrada: {formatar_duracao(total)}; volume na pasta: "
                           f"{formatar_bytes(self.medidor.bytes)}; arquivos extraídos: "
                           f"{formatar_inteiro(self.medidor.itens)} (arquivos na pasta somados às "
                           f"entradas dos ZIP).")
        self.medidor.parar()
        try:
            os.remove(self.arquivo)
        except OSError:
            pass
        self.destroy()

    # --------------------------------------------------------------- dados
    def _ler_estado(self):
        # Se a sonda tentar substituir o arquivo exatamente durante esta leitura,
        # o Windows recusa a troca; a sonda tenta de novo 0,5 s depois (sem perda).
        try:
            with open(self.arquivo, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None

    def _registrar_inicio_fase(self, est):
        self.fase_registrada = est["fase"]
        self.ultima_fracao_fase = None
        self.fase_oscilou = False
        self.inicio_fase = est.get("inicio_fase") or time.time()
        self.bytes_inicio_fase = self.medidor.bytes
        self.itens_inicio_fase = self.medidor.itens
        if self.inicio_sessao is None:
            self.inicio_sessao = self.inicio_fase
        desc = " | ".join(t.replace("\n", " ") for t in est.get("textos", [])[:3]) or "sem descrição"
        modo = "percentual" if est.get("modo") == "determinado" else "indeterminado"
        self.registrar(f"Acompanhamento: etapa {est['fase']} iniciada (progresso {modo}). Tela do motor: {desc}")

    def _registrar_fim_fase(self, motivo):
        if self.fase_registrada is None or self.fase_encerrada == self.fase_registrada:
            return
        self.fase_encerrada = self.fase_registrada
        dur = time.time() - (self.inicio_fase or time.time())
        pct = self.ultima_fracao_fase
        if self.fase_oscilou:
            pct_txt = "sem percentual (o motor não informou o total; barra em ciclos)"
        else:
            pct_txt = f"{formatar_decimal(pct * 100, 0)}%" if isinstance(pct, (int, float)) else "sem percentual"
        delta = max(0, self.medidor.bytes - self.bytes_inicio_fase)
        d_itens = max(0, self.medidor.itens - self.itens_inicio_fase)
        self.registrar(f"Acompanhamento: etapa {self.fase_registrada} encerrada ({motivo}). Duração "
                       f"{formatar_duracao(dur)}; último progresso exibido: {pct_txt}; volume gravado "
                       f"na pasta durante a etapa: {formatar_bytes(delta)}; arquivos extraídos na "
                       f"etapa: {formatar_inteiro(d_itens)}.")

    def _avaliar_pequenos(self, agora):
        """Declara (e registra) o trecho de muitos arquivos pequenos, com histerese."""
        rit, med = self.medidor.ritmo_itens(), self.medidor.media_recente()
        if self.inicio_sessao is None:
            return rit, med
        cond = rit is not None and med is not None and rit >= PEQUENOS_RITMO and med < PEQUENOS_MEDIA
        if cond:
            self._fora_pequenos_desde = None
            if self._pequenos_desde is None:
                self._pequenos_desde = agora
            if not self.pequenos and agora - self._pequenos_desde >= PEQUENOS_ENTRAR_S:
                self.pequenos = True
                self._pequenos_inicio = self._pequenos_desde
                onde = f" Pasta: {self.medidor.local_atual}." if self.medidor.local_atual else ""
                self.registrar(f"Acompanhamento: início de trecho com muitos arquivos pequenos "
                               f"(média de {formatar_bytes(med)} por arquivo, "
                               f"{formatar_decimal(rit)} arquivos/s).{onde}")
        else:
            self._pequenos_desde = None
            if self.pequenos:
                if self._fora_pequenos_desde is None:
                    self._fora_pequenos_desde = agora
                if agora - self._fora_pequenos_desde >= PEQUENOS_SAIR_S:
                    self._encerrar_pequenos(self._fora_pequenos_desde)
        return rit, med

    def _encerrar_pequenos(self, quando):
        if not self.pequenos:
            return
        self.pequenos = False
        self._fora_pequenos_desde = None
        dur = max(0, quando - (self._pequenos_inicio or quando))
        self.registrar(f"Acompanhamento: fim do trecho de muitos arquivos pequenos, que durou "
                       f"{formatar_duracao(dur)}.")

    def _ciclo(self):
        if self.fechada:
            return
        try:
            self._atualizar()
        except Exception as e:  # a janela nunca pode derrubar o lançador
            self.lb_nota.configure(text=f"Falha ao atualizar o acompanhamento: {e}")
        self.after(1000, self._ciclo)

    def _atualizar(self):
        agora = time.time()
        est = self._ler_estado()
        if est:
            self.ultimo_estado = est
            fase, modo, fracao = est.get("fase"), est.get("modo"), est.get("fracao")
            if est.get("ativo"):
                if self.fase_registrada != fase:
                    if self.fase_registrada is not None:
                        self._registrar_fim_fase("nova etapa iniciada no motor")
                    self._registrar_inicio_fase(est)
                if not self.mostrada:
                    self.mostrar()
                self.estimador.alimentar(fase, fracao, modo, agora)
                if fase == self.fase_registrada and self.estimador.oscilando:
                    self.fase_oscilou = True
                if fase == self.fase_registrada and modo == "determinado" and fracao is not None \
                        and not self.estimador.oscilando:
                    self.ultima_fracao_fase = fracao
            if est.get("encerrada") and self.fase_registrada == fase:
                self._registrar_fim_fase("barra de progresso finalizada pelo motor")
            textos = [t for t in est.get("textos", []) if t.strip()]
            self.lb_etapa.configure(text="\n".join(textos[-3:]) or "—")
        else:
            modo = "parado"

        r = self.estimador.estimativa(modo if est else "parado", agora)
        rit, med = self._avaliar_pequenos(agora)
        # Percentual parado, mas a pasta continua recebendo arquivos: não é travamento.
        parado_com_atividade = r["situacao"] == "parado" and self.medidor.houve_atividade()

        if est and est.get("encerrada") and r["situacao"] not in ("concluindo",):
            situacao_txt, cor = "Etapa finalizada pelo motor - veja a tela do módulo", COR_TEXTO
        elif parado_com_atividade:
            situacao_txt, cor = "Em andamento (percentual sem variação)", COR_OK
        else:
            situacao_txt, cor = SITUACOES.get(r["situacao"], ("—", COR_SUAVE))
        self.lb_situacao.configure(text=situacao_txt, text_color=cor)

        if self.pequenos:
            self.lb_selo.configure(text="  ARQUIVOS PEQUENOS  ", fg_color=COR_ALERTA)
        else:
            self.lb_selo.configure(text="", fg_color="transparent")

        # barra e percentual
        if r["situacao"] == "sem_total":
            if self.modo_barra != "indeterminate":
                self.barra.configure(mode="indeterminate")
                self.barra.start()
                self.modo_barra = "indeterminate"
            self.lb_pct.configure(text="")
        else:
            if self.modo_barra != "determinate":
                self.barra.stop()
                self.barra.configure(mode="determinate")
                self.modo_barra = "determinate"
            fr = r["fracao"] or 0
            self.barra.set(fr)
            self.lb_pct.configure(text=f"{formatar_decimal(fr * 100)} %" if r["fracao"] is not None else "")

        # tempos
        if r["situacao"] == "estimado":
            sufixo = " (ritmo atual)" if self.pequenos else ""
            self.campos["restante"].configure(text=formatar_duracao(r["restante_s"]) + sufixo,
                                              text_color=COR_ALERTA if self.pequenos else COR_TEXTO)
            fim = datetime.now() + timedelta(seconds=min(r["restante_s"], 30 * 86400))
            self.campos["termino"].configure(text=fim.strftime("%H:%M") + (
                "" if fim.date() == datetime.now().date() else fim.strftime(" (%d/%m)")),
                text_color=COR_ALERTA if self.pequenos else COR_TEXTO)
        elif r["situacao"] == "concluindo":
            self.campos["restante"].configure(text="0s", text_color=COR_TEXTO)
            self.campos["termino"].configure(text="agora", text_color=COR_TEXTO)
        else:
            txt = {"calculando": "calculando…", "sem_total": "indisponível",
                   "parado": "aguardando o percentual" if parado_com_atividade else "suspensa"}
            self.campos["restante"].configure(text=txt.get(r["situacao"], "—"), text_color=COR_TEXTO)
            self.campos["termino"].configure(text="—", text_color=COR_TEXTO)
        self.campos["decorrido_fase"].configure(
            text=formatar_duracao(agora - self.inicio_fase) if self.inicio_fase else "—")
        self.campos["decorrido"].configure(
            text=formatar_duracao(agora - self.inicio_sessao) if self.inicio_sessao else "—")

        # volume e ritmo
        self.campos["itens"].configure(text=formatar_inteiro(self.medidor.itens))
        if rit is None:
            self.campos["ritmo"].configure(text="—")
        else:
            txt = f"{formatar_decimal(rit)} arq./s"
            if med is not None:
                txt += f" · média {formatar_bytes(med)}"
            self.campos["ritmo"].configure(text=txt)
        self.campos["gravado"].configure(text=formatar_bytes(self.medidor.bytes))
        tx = self.medidor.taxa()
        self.campos["taxa"].configure(text=f"{formatar_bytes(tx)}/s" if tx is not None else "—")
        self.lb_local.configure(text=self.medidor.local_atual or "—")

        # nota explicativa
        notas = {
            "calculando": "A estimativa aparece depois de alguns segundos de transferência.",
            "estimado": "Estimativa pelo ritmo dos últimos 3 minutos desta etapa; pode variar. "
                        "A extração pode ter outras etapas depois desta.",
            "sem_total": "O motor não informa o volume total desta etapa, por isso não há como estimar "
                         "o tempo restante. Acompanhe pelos arquivos extraídos, pelo volume e pela taxa.",
            "parado": f"Nenhum avanço há {formatar_duracao(r['parado_s'])}. O aparelho pode estar "
                      "aguardando desbloqueio, senha ou confirmação na tela.",
            "concluindo": "A etapa chegou a 100 %. Confira a mensagem final na tela do módulo.",
            "aguardando": "",
        }
        nota = notas.get(r["situacao"], "")
        if parado_com_atividade:
            nota = ("O percentual da tela do módulo não mudou no último minuto, mas arquivos continuam "
                    "sendo gravados na pasta. Não é travamento.")
        if self.pequenos and med is not None:
            nota = (f"Trecho com muitos arquivos pequenos (média de {formatar_bytes(med)}). Aqui o tempo "
                    "vai no custo fixo de cada arquivo, não no volume: o percentual, que é calculado por "
                    "bytes, anda devagar e o tempo restante fica superestimado. O ritmo deve subir quando "
                    "a extração chegar a arquivos maiores.")
        self.lb_nota.configure(text=nota)
