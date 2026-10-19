#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gera o ícone do Quartzo (quartzo_app/recursos/quartzo.ico e .png).

Copyright (C) 2026 Jean Carlos Fonseca. GNU GPL v3 ou posterior.

O desenho é feito inteiramente por código, para que qualquer pessoa possa
regenerá-lo e conferir que nada foi embutido no arquivo além da imagem:
    python ferramentas/gerar_icone.py

Conceito: uma drusa de quartzo (transparência) cuja ponta rompe o aro de um
mostrador de relógio exatamente às 12 horas (a precisão do cristal de quartzo
e o registro exato de data e hora). Nos tamanhos de 16 a 32 pixels o mostrador
e os cristais laterais são omitidos e as arestas engrossam, para que o ícone
continue legível na barra de tarefas.
"""
import math
import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESTINO = os.path.join(RAIZ, "quartzo_app", "recursos")
SS = 4  # superamostragem

FUNDO_ALTO = (30, 40, 54)
FUNDO_BAIXO = (16, 21, 28)
CIANO = (143, 211, 232)
GELO = (226, 246, 252)
VIOLETA = (150, 134, 236)
VIOLETA_ESCURO = (86, 74, 160)
AZUL_FUNDO = (46, 92, 140)


def mistura(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def gradiente(w, h, c1, c2, angulo_graus=90):
    """Imagem RGBA w x h com gradiente linear de c1 para c2. Ângulo 90 = de cima
    para baixo; valores menores ou maiores inclinam o gradiente."""
    lado = max(w, h)
    s = int(lado * 1.5) + 2
    base = Image.linear_gradient("L").resize((s, s), Image.BICUBIC)
    base = base.rotate(90 - angulo_graus, resample=Image.BICUBIC)
    o = (s - lado) // 2
    base = base.crop((o, o, o + lado, o + lado)).resize((max(1, w), max(1, h)), Image.BICUBIC)
    return Image.composite(Image.new("RGB", base.size, c2), Image.new("RGB", base.size, c1),
                           base).convert("RGBA")


def face(img, pts, c1, c2, angulo=90):
    """Preenche o polígono `pts` com um gradiente ajustado à própria face."""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, y0 = int(math.floor(min(xs))), int(math.floor(min(ys)))
    x1, y1 = int(math.ceil(max(xs))) + 1, int(math.ceil(max(ys))) + 1
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    mascara = Image.new("L", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(mascara).polygon([(x - x0, y - y0) for x, y in pts], fill=255)
    pedaco = gradiente(x1 - x0, y1 - y0, c1, c2, angulo)
    pedaco.putalpha(mascara)
    img.alpha_composite(pedaco, (x0, y0))


def transformar(pts, cx, cy, escala, giro_graus, ox, oy):
    a = math.radians(giro_graus)
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for x, y in pts:
        x, y = (x - cx) * escala, (y - cy) * escala
        out.append((ox + x * ca - y * sa, oy + x * sa + y * ca))
    return out


def cristal(img, u, base_x, base_y, escala, giro, tons, aresta_px, detalhe=True, opacidade=1.0,
            largura=1.0, fantasma=False):
    """Desenha um cristal hexagonal com terminação piramidal.

    Coordenadas do modelo: base no ponto (0, 0), ponta em (0, -1)."""
    w, w_in = 0.17, 0.065
    topo_ext, topo_int, ponta = -0.60, -0.575, -1.0
    base_ext, base_int = 0.0, 0.03
    modelo = {
        "esq": [(-w, topo_ext), (-w_in, topo_int), (-w_in, base_int), (-w, base_ext)],
        "meio": [(-w_in, topo_int), (w_in, topo_int), (w_in, base_int), (-w_in, base_int)],
        "dir": [(w_in, topo_int), (w, topo_ext), (w, base_ext), (w_in, base_int)],
        "t_esq": [(-w, topo_ext), (-w_in, topo_int), (0, ponta)],
        "t_meio": [(-w_in, topo_int), (w_in, topo_int), (0, ponta)],
        "t_dir": [(w_in, topo_int), (w, topo_ext), (0, ponta)],
    }
    if largura != 1.0:
        modelo = {k: [(x * largura, y) for x, y in v] for k, v in modelo.items()}
    p = {k: [(x * u, y * u) for x, y in v] for k, v in modelo.items()}
    p = {k: transformar(v, 0, 0, escala, giro, base_x, base_y) for k, v in p.items()}

    def pt(x, y):
        return transformar([(x * largura * u, y * u)], 0, 0, escala, giro, base_x, base_y)[0]

    # As faces vão numa camada própria (para poderem ficar translúcidas) e as
    # linhas em outra; ImageDraw substitui pixels em vez de misturar, por isso
    # nada semitransparente é desenhado direto sobre a imagem.
    corpo = Image.new("RGBA", img.size, (0, 0, 0, 0))
    escuro, medio, claro, brilho = tons
    face(corpo, p["esq"], mistura(escuro, medio, 0.25), escuro, 90)
    face(corpo, p["meio"], medio, mistura(medio, escuro, 0.40), 90)
    face(corpo, p["dir"], claro, mistura(claro, medio, 0.50), 90)
    face(corpo, p["t_esq"], mistura(escuro, medio, 0.60), escuro, 60)
    face(corpo, p["t_meio"], brilho, medio, 90)
    face(corpo, p["t_dir"], brilho, claro, 120)
    if opacidade < 1.0:
        corpo.putalpha(corpo.getchannel("A").point(lambda a: int(a * opacidade)))
    img.alpha_composite(corpo)

    linhas = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(linhas)
    contorno = p["esq"][:1] + [p["t_meio"][2]] + [p["dir"][1], p["dir"][2], p["dir"][3],
                                                  p["meio"][3], p["esq"][3], p["esq"][0]]
    d.line(contorno, fill=(9, 13, 20, 210), width=max(1, int(aresta_px * 1.2)), joint="curve")
    for a, b in ((p["meio"][0], p["meio"][3]), (p["meio"][1], p["meio"][2]),
                 (p["t_meio"][0], p["t_meio"][2]), (p["t_meio"][1], p["t_meio"][2]),
                 (p["meio"][0], p["meio"][1])):
        d.line([a, b], fill=GELO + (200,), width=max(1, int(aresta_px * 0.8)))
    if detalhe and fantasma:
        # "fantasma": a ponta de uma fase anterior de crescimento, visível por
        # dentro do cristal, como acontece no quartzo natural
        d.line([pt(-0.17, -0.36), pt(0, -0.70), pt(0.17, -0.36)], fill=GELO + (95,),
               width=max(1, int(aresta_px * 0.7)), joint="curve")
    if detalhe:
        # reflexo longo na face clara
        d.line([pt(0.118, -0.54), pt(0.132, -0.12)], fill=(255, 255, 255, 150),
               width=max(1, int(aresta_px * 1.6)))
    img.alpha_composite(linhas)
    return p["t_meio"][2]  # ponta


def rocha(img, T):
    """Base de rocha facetada que une os cristais numa drusa."""
    camada = Image.new("RGBA", img.size, (0, 0, 0, 0))
    contorno = [(0.25, 0.875), (0.30, 0.815), (0.37, 0.835), (0.44, 0.805), (0.52, 0.825),
                (0.58, 0.800), (0.66, 0.830), (0.72, 0.812), (0.765, 0.872), (0.70, 0.905),
                (0.30, 0.905)]
    pts = [(x * T, y * T) for x, y in contorno]
    face(camada, pts, (64, 76, 96), (24, 30, 40), 90)
    d = ImageDraw.Draw(camada)
    # facetas: algumas arestas claras no alto da rocha
    for a, b in ((1, 2), (3, 4), (5, 6), (7, 8)):
        d.line([pts[a], pts[b]], fill=(150, 170, 196, 140), width=max(1, int(T * 0.004)))
    d.line(pts + [pts[0]], fill=(9, 13, 20, 210), width=max(1, int(T * 0.006)), joint="curve")
    sombra = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sombra).ellipse([T * 0.24, T * 0.86, T * 0.78, T * 0.94], fill=(0, 0, 0, 120))
    img.alpha_composite(sombra.filter(ImageFilter.GaussianBlur(T * 0.015)))
    img.alpha_composite(camada)


def estrela(img, cx, cy, raio, cor=(255, 255, 255)):
    """Brilho de quatro pontas."""
    camada = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(camada)
    f = raio * 0.16
    d.polygon([(cx, cy - raio), (cx + f, cy - f), (cx + raio, cy), (cx + f, cy + f),
               (cx, cy + raio), (cx - f, cy + f), (cx - raio, cy), (cx - f, cy - f)], fill=cor + (255,))
    halo = camada.filter(ImageFilter.GaussianBlur(raio * 0.35))
    img.alpha_composite(halo)
    img.alpha_composite(camada)


def desenhar(tam, simples=False):
    T = tam * SS
    u = T  # unidade = lado do ícone
    img = Image.new("RGBA", (T, T), (0, 0, 0, 0))

    # azulejo de fundo com cantos arredondados e gradiente noturno
    raio_canto = int(T * (0.20 if not simples else 0.18))
    mascara = Image.new("L", (T, T), 0)
    margem = int(T * 0.03) if not simples else 0
    ImageDraw.Draw(mascara).rounded_rectangle([margem, margem, T - margem - 1, T - margem - 1],
                                              radius=raio_canto, fill=255)
    fundo = gradiente(T, T, FUNDO_ALTO, FUNDO_BAIXO, 90)
    # brilho difuso atrás do cristal
    glow = Image.new("RGBA", (T, T), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([T * 0.22, T * 0.20, T * 0.78, T * 0.86], fill=AZUL_FUNDO + (170,))
    fundo.alpha_composite(glow.filter(ImageFilter.GaussianBlur(T * 0.09)))
    img.paste(fundo, (0, 0), mascara)
    # borda interna sutil (em camada própria, para misturar e não substituir pixels)
    borda = Image.new("RGBA", (T, T), (0, 0, 0, 0))
    ImageDraw.Draw(borda).rounded_rectangle([margem, margem, T - margem - 1, T - margem - 1],
                                            radius=raio_canto, outline=(255, 255, 255, 30),
                                            width=max(1, int(T * 0.006)))
    img.alpha_composite(borda)

    cx, cy = T * 0.5, T * 0.53
    aresta = max(SS, int(T * (0.006 if not simples else 0.018)))

    if not simples:
        # mostrador: aro, 60 marcas de minuto e 12 de hora
        camada = Image.new("RGBA", (T, T), (0, 0, 0, 0))
        d = ImageDraw.Draw(camada)
        R = T * 0.355
        d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=CIANO + (120,), width=int(T * 0.010))
        for i in range(60):
            a = math.radians(i * 6 - 90)
            longo = i % 5 == 0
            r1 = R - T * (0.050 if longo else 0.026)
            r2 = R - T * 0.016
            d.line([(cx + r1 * math.cos(a), cy + r1 * math.sin(a)),
                    (cx + r2 * math.cos(a), cy + r2 * math.sin(a))],
                   fill=CIANO + ((170 if longo else 80),), width=int(T * (0.009 if longo else 0.004)))
        img.alpha_composite(camada)

        # cristais laterais, mais violetas (ametista), atrás do principal
        tons_lat = (VIOLETA_ESCURO, mistura(VIOLETA, VIOLETA_ESCURO, 0.35), VIOLETA, mistura(VIOLETA, GELO, 0.5))
        cristal(img, u, T * 0.40, T * 0.83, 0.47, -26, tons_lat, aresta, opacidade=0.95)
        cristal(img, u, T * 0.615, T * 0.835, 0.37, 23, tons_lat, aresta, opacidade=0.95)

    # cristal principal: do azul na base ao gelo na ponta; translúcido, deixa ver o mostrador
    tons = ((62, 88, 150), (98, 168, 214), CIANO, GELO)
    if simples:
        # aro ciano no azulejo, para destacá-lo de barras de título escuras
        aro = Image.new("RGBA", (T, T), (0, 0, 0, 0))
        ImageDraw.Draw(aro).rounded_rectangle([0, 0, T - 1, T - 1], radius=raio_canto,
                                              outline=CIANO + (150,), width=max(SS, int(T * 0.06)))
        img.alpha_composite(aro)
        tons_peq = ((84, 120, 186), (120, 192, 232), (170, 226, 242), (240, 252, 255))
        cristal(img, u, T * 0.5, T * 0.92, 0.80, 0, tons_peq, aresta, detalhe=False, largura=1.75)
    else:
        ponta = cristal(img, u, T * 0.5, T * 0.84, 0.69, 0, tons, aresta, opacidade=0.86, fantasma=True)
        rocha(img, T)
        # a ponta atravessa o aro às 12 h e acende um brilho
        estrela(img, ponta[0], ponta[1] + T * 0.004, T * 0.062)

    # recorta tudo pelo azulejo
    final = Image.new("RGBA", (T, T), (0, 0, 0, 0))
    final.paste(img, (0, 0), ImageChops.multiply(mascara, img.getchannel("A")))
    return final.resize((tam, tam), Image.LANCZOS)


def main():
    os.makedirs(DESTINO, exist_ok=True)
    grande = desenhar(1024)
    grande.save(os.path.join(DESTINO, "quartzo_1024.png"), optimize=True)
    desenhar(256).save(os.path.join(DESTINO, "quartzo.png"), optimize=True)
    tamanhos = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    imagens = [desenhar(t, simples=t <= 32) for t in tamanhos]
    imagens[-1].save(os.path.join(DESTINO, "quartzo.ico"), format="ICO",
                     sizes=[(t, t) for t in tamanhos], append_images=imagens[:-1])
    # folha de conferência, fora da pasta do programa
    if "--previa" in sys.argv:
        larg = sum(tamanhos) + 20 * len(tamanhos) + 20
        folha = Image.new("RGBA", (max(larg, 1064), 256 + 1024 + 60), (60, 60, 60, 255))
        x = 20
        for t, im in zip(tamanhos, imagens):
            folha.alpha_composite(im, (x, 20 + 256 - t))
            x += t + 20
        folha.alpha_composite(grande, (20, 256 + 40))
        destino = sys.argv[sys.argv.index("--previa") + 1]
        folha.save(destino)
        print("Prévia:", destino)
    print("Ícone gerado em", DESTINO)


if __name__ == "__main__":
    main()
