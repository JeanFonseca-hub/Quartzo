# -*- coding: utf-8 -*-
"""
Quartzo - sonda de progresso dos motores (copiada como motores/*/quartzo_progresso.py).

Copyright (C) 2026 Jean Carlos Fonseca. GNU GPL v3 ou posterior.

O que faz
---------
Os motores UFADE e ALEX mostram o andamento de cada operação numa barra de
progresso do CustomTkinter (CTkProgressBar), em modo percentual (``set``) ou
indeterminado (``start``/``stop``), acompanhada de rótulos de texto. Esta sonda
envolve esses métodos para COPIAR o que já é exibido na tela do motor (fração,
modo e textos próximos à barra) para um pequeno arquivo JSON de estado, que a
janela "Acompanhamento da extração" do lançador lê a cada segundo.

O que NÃO faz
-------------
Não altera nenhum valor, não chama o aparelho, não grava na pasta da extração
e não interfere na lógica do motor: cada envoltório chama o método original
primeiro e, se qualquer passo da sonda falhar, a falha é engolida em silêncio.
Sem a variável de ambiente QUARTZO_PROGRESSO (motor aberto fora do lançador),
a sonda não é instalada.
"""
import json
import os
import threading
import time

ARQUIVO = os.environ.get("QUARTZO_PROGRESSO", "")
_INSTALADA = False
_trava = threading.Lock()
_sujo = threading.Event()
_estado = {
    "versao": 1,
    "pid": os.getpid(),
    "fase": 0,            # incrementa a cada nova barra que entra em atividade
    "ativo": False,       # já houve progresso real nesta fase
    "modo": "parado",     # determinado | indeterminado | parado
    "fracao": None,       # 0..1 no modo determinado
    "textos": [],         # textos dos rótulos ao redor da barra, na ordem da tela
    "inicio_fase": None,  # epoch da primeira atividade da fase
    "atualizado": None,   # epoch da última mudança
    "encerrada": False,   # barra destruída ou parada depois de ativa
}
_barra_atual = None       # id() da barra que está sendo acompanhada
_mestre_atual = None      # id() do frame que contém a barra
_rotulos = {}             # id(rotulo) -> [ordem, texto]
_ordem = [0]
_IGNORAR = ("por Christian Peter",)


def _marcar():
    _estado["atualizado"] = time.time()
    _sujo.set()


def _escritor():
    while True:
        _sujo.wait()
        time.sleep(0.4)
        _sujo.clear()
        try:
            with _trava:
                textos = [t for _, t in sorted(_rotulos.values())]
                _estado["textos"] = [t for t in textos if t and not any(i in t for i in _IGNORAR)][-5:]
                dados = json.dumps(_estado, ensure_ascii=False)
            tmp = ARQUIVO + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(dados)
            os.replace(tmp, ARQUIVO)
        except Exception:
            _sujo.set()  # tenta de novo no próximo ciclo (ex.: arquivo aberto pelo leitor)
            time.sleep(0.5)


_todos_rotulos = {}  # id(rotulo) -> [id(mestre), texto]; preenchido sem chamar o Tk


def _vizinhos(barra):
    """Rótulos do mesmo quadro da barra, a partir do registro interno (sem Tk)."""
    mestre_id = id(getattr(barra, "master", None))
    return mestre_id, [(rid, txt) for rid, (mid, txt) in list(_todos_rotulos.items()) if mid == mestre_id]


def _adotar_barra(barra, mestre_id, vizinhos):
    """Passa a acompanhar `barra` (nova fase). Chamar COM a trava e SEM chamadas ao Tk."""
    global _barra_atual, _mestre_atual
    if _barra_atual == id(barra):
        return
    _barra_atual = id(barra)
    _mestre_atual = mestre_id
    _rotulos.clear()
    for rid, txt in vizinhos:
        _ordem[0] += 1
        _rotulos[rid] = [_ordem[0], txt]
    _estado.update({"fase": _estado["fase"] + 1, "ativo": False, "modo": "parado", "fracao": None,
                    "inicio_fase": None, "encerrada": False})


def _ativar():
    if not _estado["ativo"]:
        _estado["ativo"] = True
        _estado["inicio_fase"] = time.time()


def instalar():
    global _INSTALADA
    if _INSTALADA or not ARQUIVO:
        return
    try:
        import customtkinter as ctk
    except Exception:
        return
    Barra = ctk.CTkProgressBar
    Rotulo = ctk.CTkLabel
    orig_set, orig_start, orig_stop, orig_destroy = Barra.set, Barra.start, Barra.stop, Barra.destroy
    orig_rot_init, orig_rot_conf, orig_rot_destroy = Rotulo.__init__, Rotulo.configure, Rotulo.destroy

    def set_(self, value, *a, **k):
        r = orig_set(self, value, *a, **k)
        try:
            vizinhos = _vizinhos(self) if _barra_atual != id(self) else None
            with _trava:
                if vizinhos is not None:
                    _adotar_barra(self, *vizinhos)
                v = max(0.0, min(1.0, float(value)))
                anterior = _estado["fracao"]
                _estado["fracao"] = v
                if _estado["modo"] != "indeterminado":
                    _estado["modo"] = "determinado"
                # atividade real: valor positivo, ou mudança de valor depois do zero inicial
                if v > 0 or (anterior is not None and anterior != v):
                    _ativar()
                _marcar()
        except Exception:
            pass
        return r

    def start_(self, *a, **k):
        r = orig_start(self, *a, **k)
        try:
            vizinhos = _vizinhos(self) if _barra_atual != id(self) else None
            with _trava:
                if vizinhos is not None:
                    _adotar_barra(self, *vizinhos)
                _estado["modo"] = "indeterminado"
                _estado["fracao"] = None
                _estado["encerrada"] = False
                _ativar()
                _marcar()
        except Exception:
            pass
        return r

    def stop_(self, *a, **k):
        r = orig_stop(self, *a, **k)
        try:
            with _trava:
                if _barra_atual == id(self) and _estado["modo"] == "indeterminado":
                    _estado["modo"] = "parado"
                    _estado["encerrada"] = _estado["ativo"]
                    _marcar()
        except Exception:
            pass
        return r

    def destroy_(self, *a, **k):
        try:
            with _trava:
                if _barra_atual == id(self):
                    _estado["modo"] = "parado"
                    _estado["encerrada"] = _estado["ativo"]
                    _marcar()
        except Exception:
            pass
        return orig_destroy(self, *a, **k)

    def rot_init(self, *a, **k):
        orig_rot_init(self, *a, **k)
        try:
            txt = str(k.get("text", ""))[:300]
            mestre = a[0] if a else k.get("master")
            with _trava:
                _todos_rotulos[id(self)] = [id(mestre), txt]
                if _mestre_atual is not None and id(mestre) == _mestre_atual:
                    _ordem[0] += 1
                    _rotulos[id(self)] = [_ordem[0], txt]
                    _marcar()
        except Exception:
            pass

    def rot_destroy(self, *a, **k):
        try:
            with _trava:
                _todos_rotulos.pop(id(self), None)
                _rotulos.pop(id(self), None)
        except Exception:
            pass
        return orig_rot_destroy(self, *a, **k)

    def rot_conf(self, *a, **k):
        r = orig_rot_conf(self, *a, **k)
        try:
            if "text" in k:
                with _trava:
                    if id(self) in _todos_rotulos:
                        _todos_rotulos[id(self)][1] = str(k["text"])[:300]
                    if id(self) in _rotulos:
                        _rotulos[id(self)][1] = str(k["text"])[:300]
                        _marcar()
        except Exception:
            pass
        return r

    Barra.set, Barra.start, Barra.stop, Barra.destroy = set_, start_, stop_, destroy_
    Rotulo.__init__, Rotulo.configure, Rotulo.destroy = rot_init, rot_conf, rot_destroy
    threading.Thread(target=_escritor, name="quartzo-sonda", daemon=True).start()
    _INSTALADA = True


instalar()
