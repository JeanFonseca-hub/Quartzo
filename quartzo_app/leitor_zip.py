# -*- coding: utf-8 -*-
"""
Quartzo - contagem dos arquivos que entram num ZIP ainda em gravação.

Copyright (C) 2026 Jean Carlos Fonseca. GNU GPL v3 ou posterior.

Por que existe
--------------
No Logical+ (padrão UFED) e em outros modos, o motor copia cada arquivo do
aparelho para uma pasta temporária, grava-o dentro de um ZIP e apaga a cópia.
Contar arquivos na pasta, portanto, não mostra quantos itens já foram
extraídos. Este leitor percorre os cabeçalhos locais do ZIP (assinatura
PK\\x03\\x04), na ordem em que o motor os grava, e conta as entradas.

Garantias
---------
1. Só leitura. No Windows o arquivo é aberto com compartilhamento total de
   leitura, escrita e exclusão (FILE_SHARE_READ | WRITE | DELETE), de modo que
   o motor nunca encontra o ZIP bloqueado; o arquivo fica aberto apenas durante
   a leitura de cada ciclo.
2. Só lê cabeçalhos (30 bytes mais o nome), nunca o conteúdo dos arquivos.
3. Uma entrada só é contada quando o cabeçalho seguinte (ou o diretório
   central) já existe. Assim, uma entrada que o motor ainda está gravando, cujo
   cabeçalho ainda tem tamanho provisório, nunca é contada pela metade; o custo
   é a contagem andar um arquivo atrás do motor.
4. Se encontrar algo fora do formato esperado (por exemplo, entradas com
   descritor de dados posterior), o leitor desiste daquele ZIP e a janela
   simplesmente deixa de mostrar a contagem dele; nada é inventado.
"""
import os
import struct
import sys
from collections import deque

SIG_LOCAL = b"PK\x03\x04"
SIG_CENTRAL = b"PK\x01\x02"
SIG_FIM = (b"PK\x05\x06", b"PK\x06\x06", b"PK\x06\x07")
SIG_DESCRITOR = b"PK\x07\x08"
SIGS_VALIDAS = (SIG_LOCAL, SIG_CENTRAL, SIG_DESCRITOR) + SIG_FIM
MAX_ENTRADAS_POR_CICLO = 20000


def abrir_somente_leitura(caminho):
    """Abre um arquivo em modo binário de leitura sem impedir que outro
    processo o grave, renomeie ou apague (Windows); nos demais sistemas,
    open() já não bloqueia."""
    if sys.platform != "win32":
        return open(caminho, "rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    GENERIC_READ = 0x80000000
    SHARE_TUDO = 0x1 | 0x2 | 0x4  # leitura, escrita e exclusão
    OPEN_EXISTING = 3
    h = k32.CreateFileW(caminho, GENERIC_READ, SHARE_TUDO, None, OPEN_EXISTING, 0x80, None)
    if h is None or h == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "não foi possível abrir para leitura", caminho)
    try:
        fd = msvcrt.open_osfhandle(h, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except Exception:
        k32.CloseHandle(h)
        raise
    return os.fdopen(fd, "rb")


class LeitorZip:
    """Acompanha, de forma incremental, as entradas gravadas num ZIP."""

    def __init__(self, caminho):
        self.caminho = caminho
        self.pos = 0                 # início do próximo cabeçalho a examinar
        self.arquivos = 0            # entradas de arquivo (sem contar pastas)
        self.pastas = 0
        self.bytes_dados = 0
        self.ultimo_nome = None
        self.recentes = deque(maxlen=500)  # tamanhos das últimas entradas
        self.finalizado = False      # diretório central encontrado
        self.desistiu = False
        self.motivo = ""

    def _zerar(self):
        self.__init__(self.caminho)

    @staticmethod
    def _tamanhos_zip64(extra, orig, comp):
        """Aplica o campo extra ZIP64 (id 0x0001): os valores 0xFFFFFFFF do
        cabeçalho são substituídos, nesta ordem, por original e compactado."""
        i = 0
        while i + 4 <= len(extra):
            ident, n = struct.unpack_from("<HH", extra, i)
            if ident == 0x0001:
                campos, j = extra[i + 4:i + 4 + n], 0
                if orig == 0xFFFFFFFF and j + 8 <= len(campos):
                    orig = struct.unpack_from("<Q", campos, j)[0]
                    j += 8
                if comp == 0xFFFFFFFF and j + 8 <= len(campos):
                    comp = struct.unpack_from("<Q", campos, j)[0]
                break
            i += 4 + n
        return orig, comp

    def atualizar(self):
        """Lê os cabeçalhos novos. Retorna o número de entradas acrescentadas."""
        if self.desistiu:
            return 0
        novas = 0
        try:
            with abrir_somente_leitura(self.caminho) as fh:
                fim = os.fstat(fh.fileno()).st_size
                if fim < self.pos:           # ZIP recriado ou truncado
                    self._zerar()
                while novas < MAX_ENTRADAS_POR_CICLO and self.pos + 30 <= fim:
                    fh.seek(self.pos)
                    cab = fh.read(30)
                    if len(cab) < 30:
                        break
                    sig = cab[:4]
                    if sig != SIG_LOCAL:
                        if sig == SIG_CENTRAL or sig in SIG_FIM:
                            # ZIP fechado. Se o motor reabrir em modo "a", novas
                            # entradas serão gravadas exatamente a partir daqui.
                            self.finalizado = True
                        elif sig != b"\x00\x00\x00\x00":
                            self.desistiu, self.motivo = True, f"assinatura inesperada no byte {self.pos}"
                        break
                    self.finalizado = False
                    (_v, flags, metodo, _hora, _data, _crc, comp, orig,
                     n_nome, n_extra) = struct.unpack_from("<HHHHHIIIHH", cab, 4)
                    if self.pos + 30 + n_nome + n_extra > fim:
                        break
                    resto = fh.read(n_nome + n_extra)
                    nome_b, extra = resto[:n_nome], resto[n_nome:]
                    if 0xFFFFFFFF in (comp, orig):
                        orig, comp = self._tamanhos_zip64(extra, orig, comp)
                    if flags & 0x08:
                        self.desistiu, self.motivo = True, "entradas com descritor de dados posterior"
                        break
                    # Cabeçalho provisório: o zipfile do Python grava primeiro o
                    # cabeçalho com tamanho compactado 0 e só o corrige depois de
                    # gravar os dados. Sem esta checagem, um arquivo do aparelho que
                    # também é ZIP (APK, DOCX) seria confundido com o próximo cabeçalho.
                    if (orig > 0 and comp == 0) or (metodo == 0 and comp != orig):
                        break
                    prox = self.pos + 30 + n_nome + n_extra + comp
                    if prox + 4 > fim:
                        break  # entrada ainda em gravação (ou a última do ZIP aberto)
                    fh.seek(prox)
                    if fh.read(4) not in SIGS_VALIDAS:
                        break  # próximo cabeçalho ainda não gravado
                    nome = nome_b.decode("utf-8" if flags & 0x800 else "cp437", errors="replace")
                    self.pos = prox
                    novas += 1
                    if nome.endswith("/"):
                        self.pastas += 1
                    else:
                        self.arquivos += 1
                        self.bytes_dados += comp
                        self.recentes.append(comp)
                        self.ultimo_nome = nome
        except OSError:
            pass
        return novas
