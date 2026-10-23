# -*- coding: utf-8 -*-
"""Quartzo - ferramenta unificada de extração forense de dispositivos móveis.

Fork unificado do UFADE (iOS/Apple) e do ALEX (Android), ambos de
Christian Peter (https://github.com/prosch88), com interface em português.

Copyright (C) 2026 Jean Carlos Fonseca
Este programa é software livre: você pode redistribuí-lo e/ou modificá-lo sob
os termos da GNU General Public License, versão 3 ou posterior, publicada pela
Free Software Foundation. Distribuído SEM QUALQUER GARANTIA. Veja LICENSE.
"""
import os as _os

VERSAO = "1.0.2"
ICONE_ICO = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "recursos", "quartzo.ico")
ICONE_PNG = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "recursos", "quartzo.png")
NOME = "Quartzo"
NOME_LONGO = "Quartzo · Extração forense de dispositivos móveis"
