# -*- coding: utf-8 -*-
"""
Quartzo - estimativa de tempo restante de uma etapa de extração.

Copyright (C) 2026 Jean Carlos Fonseca. GNU GPL v3 ou posterior.

Método (deliberadamente simples e auditável)
--------------------------------------------
1. Guarda as amostras (instante, fração concluída) da etapa atual.
2. Calcula o ritmo médio pela regressão linear das amostras dos últimos
   JANELA_S segundos (fração por segundo). Se o avanço nessa janela for menor
   que MIN_AVANCO, usa todas as amostras guardadas da etapa (até 2.000).
3. Tempo restante = (1 - fração atual) / ritmo, suavizado para não "pular".
4. Só mostra a estimativa depois de MIN_SEGUNDOS de observação e de um avanço
   mínimo, e nunca inventa número quando o motor não informa o total: se a
   barra estiver no modo indeterminado ou oscilar (o ALEX, quando não sabe o
   total, faz a barra girar em ciclos), a estimativa é declarada indisponível.
"""
import time
from collections import deque

JANELA_S = 180.0        # janela de observação do ritmo
MIN_SEGUNDOS = 15.0     # observação mínima antes de estimar
MIN_AVANCO = 0.005      # avanço mínimo (0,5 %) dentro da janela
PARADO_S = 60.0         # sem avanço por mais que isso = "sem avanço"
MAX_RECUOS = 3          # recuos da barra que caracterizam oscilação


class Estimador:
    def __init__(self):
        self.fase = None
        self.reiniciar(None)

    def reiniciar(self, fase):
        self.fase = fase
        self.amostras = deque(maxlen=2000)
        self.recuos = deque(maxlen=50)
        self.ultimo_avanco = None
        self.eta_suave = None
        self.t_eta = None
        self.oscilando = False

    def alimentar(self, fase, fracao, modo, agora=None):
        """Registra uma leitura. `fracao` em 0..1 ou None."""
        agora = time.time() if agora is None else agora
        if fase != self.fase:
            self.reiniciar(fase)
        if modo != "determinado" or fracao is None:
            return
        if self.amostras:
            ant = self.amostras[-1][1]
            if fracao < ant - 1e-6:
                self.recuos.append(agora)
                if ant - fracao > 0.5 and ant > 0.95 and len(self.recuos) == 1:
                    # a barra completou e recomeçou: nova etapa dentro da mesma tela
                    self.reiniciar(fase)
            elif fracao > ant + 1e-6:
                self.ultimo_avanco = agora
        elif fracao > 0:
            self.ultimo_avanco = agora
        if not self.amostras or self.amostras[-1][1] != fracao or agora - self.amostras[-1][0] >= 5:
            self.amostras.append((agora, fracao))
        recentes = [t for t in self.recuos if agora - t <= 60]
        self.oscilando = len(recentes) >= MAX_RECUOS

    def ritmo(self, agora=None):
        """Fração por segundo na janela recente, ou None."""
        agora = time.time() if agora is None else agora
        pts = [(t, f) for t, f in self.amostras if agora - t <= JANELA_S]
        if len(pts) < 3:
            return None
        if pts[-1][0] - pts[0][0] >= MIN_SEGUNDOS and pts[-1][1] - pts[0][1] < MIN_AVANCO:
            # Avanço lento (ex.: muitos arquivos pequenos): em vez de ficar
            # "calculando" para sempre, usa todas as amostras guardadas da etapa,
            # desde que cubram mais que a janela e somem o avanço mínimo.
            todos = list(self.amostras)
            if todos[-1][0] - todos[0][0] > JANELA_S and todos[-1][1] - todos[0][1] >= MIN_AVANCO:
                pts = todos
        if pts[-1][0] - pts[0][0] < MIN_SEGUNDOS or pts[-1][1] - pts[0][1] < MIN_AVANCO:
            return None
        n = len(pts)
        mt = sum(t for t, _ in pts) / n
        mf = sum(f for _, f in pts) / n
        num = sum((t - mt) * (f - mf) for t, f in pts)
        den = sum((t - mt) ** 2 for t, _ in pts)
        if den <= 0:
            return None
        r = num / den
        return r if r > 0 else None

    def estimativa(self, modo, agora=None):
        """Retorna dict com: situacao, restante_s (ou None), fracao, parado_s."""
        agora = time.time() if agora is None else agora
        fracao = self.amostras[-1][1] if self.amostras else None
        parado = (agora - self.ultimo_avanco) if self.ultimo_avanco else None
        if modo == "indeterminado":
            return {"situacao": "sem_total", "restante_s": None, "fracao": None, "parado_s": None}
        if modo != "determinado" or fracao is None:
            return {"situacao": "aguardando", "restante_s": None, "fracao": fracao, "parado_s": None}
        if self.oscilando:
            return {"situacao": "sem_total", "restante_s": None, "fracao": None, "parado_s": None}
        if fracao >= 0.999:
            return {"situacao": "concluindo", "restante_s": 0, "fracao": fracao, "parado_s": parado}
        if parado is not None and parado > PARADO_S:
            return {"situacao": "parado", "restante_s": None, "fracao": fracao, "parado_s": parado}
        r = self.ritmo(agora)
        if r is None:
            return {"situacao": "calculando", "restante_s": None, "fracao": fracao, "parado_s": parado}
        bruto = (1.0 - fracao) / r
        if self.eta_suave is None:
            self.eta_suave = bruto
        else:
            ajustado = max(0.0, self.eta_suave - (agora - self.t_eta))
            self.eta_suave = 0.7 * ajustado + 0.3 * bruto
        self.t_eta = agora
        return {"situacao": "estimado", "restante_s": self.eta_suave, "fracao": fracao, "parado_s": parado}


def formatar_duracao(seg):
    if seg is None:
        return "—"
    seg = int(round(seg))
    if seg >= 48 * 3600:
        return "mais de 48 h"
    h, r = divmod(seg, 3600)
    m, s = divmod(r, 60)
    return f"{h:d}h{m:02d}min" if h else (f"{m:d}min{s:02d}s" if m else f"{s:d}s")


def formatar_decimal(x, casas=1):
    """Número com vírgula decimal e ponto de milhar (padrão brasileiro)."""
    return f"{x:,.{casas}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def formatar_inteiro(n):
    return f"{int(n):,}".replace(",", ".")


def formatar_bytes(n):
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unidade == "TB":
            return f"{formatar_decimal(n)} {unidade}" if unidade != "B" else f"{int(n)} B"
        n /= 1024.0
