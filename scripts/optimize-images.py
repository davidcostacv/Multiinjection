#!/usr/bin/env python3
"""Optimizacion de imagenes para el sitio de Multi Injection La Paz.

NO es un paso de build: el sitio se sirve tal cual desde GitHub Pages.
Esto se corre a mano cuando se agrega o reemplaza una imagen en assets/,
y los archivos que genera se commitean al repo.

    python scripts/optimize-images.py            # genera todo
    python scripts/optimize-images.py --palette  # solo reporta colores

Requiere unicamente Pillow:  pip install Pillow
"""

from __future__ import annotations

import argparse
import colorsys
import sys
from collections import defaultdict
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Falta Pillow. Instalalo con:  pip install Pillow")

RAIZ = Path(__file__).resolve().parent.parent
ASSETS = RAIZ / "assets"
# Los originales viven aparte y NO se sirven: el script solo lee de aqui, para
# no re-comprimir su propia salida en cada corrida.
SRC = ASSETS / "_src"

# Umbrales del brief: sobre esto, la imagen se optimiza.
MAX_KB = 300
MAX_ANCHO = 1600
ANCHO_MOVIL = 800

# Fondo de pagina, para previsualizar el logo recortado sobre el color real.
INK_850 = (16, 19, 21)


# --------------------------------------------------------------------------
# utilidades
# --------------------------------------------------------------------------

def kb(ruta: Path) -> float:
    return ruta.stat().st_size / 1024


def hex_of(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb[:3])


def formato_real(ruta: Path) -> str:
    """El formato segun los bytes, no segun la extension."""
    cabecera = ruta.open("rb").read(16)
    if cabecera[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if cabecera[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if cabecera[4:8] == b"ftyp":
        marca = cabecera[8:12].decode("ascii", "replace")
        return f"HEIF/{marca}"          # heic, avif, mif1...
    if cabecera[:4] == b"RIFF" and cabecera[8:12] == b"WEBP":
        return "WEBP"
    return "desconocido"


# --------------------------------------------------------------------------
# 1. paleta
# --------------------------------------------------------------------------

def extraer_paleta(ruta: Path, n_out=5, n_quant=48, merge=42):
    """Colores dominantes ignorando blancos y negros puros."""
    im = Image.open(ruta).convert("RGB")
    work = im.copy()
    work.thumbnail((400, 400), Image.Resampling.LANCZOS)

    q = work.quantize(colors=n_quant, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    cuentas = defaultdict(int)
    for cuenta, idx in q.convert("P").getcolors(n_quant * 4) or []:
        rgb = tuple(pal[idx * 3: idx * 3 + 3])
        if all(c >= 242 for c in rgb) or all(c <= 18 for c in rgb):
            continue
        cuentas[rgb] += cuenta

    fusion = []
    for rgb, cuenta in sorted(cuentas.items(), key=lambda kv: -kv[1]):
        for e in fusion:
            if sum((a - b) ** 2 for a, b in zip(rgb, e["rgb"])) < merge ** 2:
                e["cuenta"] += cuenta
                break
        else:
            fusion.append({"rgb": rgb, "cuenta": cuenta})

    total = sum(e["cuenta"] for e in fusion) or 1
    return [
        {"hex": hex_of(e["rgb"]), "rgb": e["rgb"], "pct": 100 * e["cuenta"] / total}
        for e in fusion[:n_out]
    ]


def ajustar_luz(hex_base: str, delta: float) -> str:
    """Variante del acento moviendo SOLO la luminosidad HSL. delta en [-1, 1]."""
    r, g, b = (int(hex_base[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = max(0.0, min(1.0, l + delta))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return hex_of((round(r * 255), round(g * 255), round(b * 255)))


def luminancia(hex_c: str) -> float:
    canales = []
    for i in (1, 3, 5):
        c = int(hex_c[i:i + 2], 16) / 255
        canales.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = canales
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contraste(a: str, b: str) -> float:
    la, lb = luminancia(a), luminancia(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


# --------------------------------------------------------------------------
# 2. logo: fondo transparente
# --------------------------------------------------------------------------

def _es_grafito(r, g, b, techo=78, spread=34):
    """Fondo del logo: oscuro y casi sin saturacion.

    El arte es amarillo (max 245), azul (max 140) y rojo (max 217), todos muy
    por encima del techo, asi que la separacion es limpia y no hay riesgo de
    comerse trazos.
    """
    return max(r, g, b) < techo and (max(r, g, b) - min(r, g, b)) < spread


def construir_logo(origen: Path, tolerancia=60):
    """Quita el fondo grafito solido del logo y lo deja transparente.

    Dos pasadas: flood-fill desde las esquinas para la region conectada, y
    luego un barrido por color para las zonas grafito que quedan ENCERRADAS
    dentro del rombo, adonde el flood-fill no puede llegar.
    """
    im = Image.open(origen).convert("RGBA")
    w, h = im.size

    for esquina in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if im.getpixel(esquina)[3] == 0:
            continue                     # ya borrada por un fill anterior
        ImageDraw.floodfill(im, esquina, (0, 0, 0, 0), thresh=tolerancia)

    pixeles = im.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixeles[x, y]
            if a and _es_grafito(r, g, b):
                pixeles[x, y] = (r, g, b, 0)

    caja = im.getbbox()
    if caja:
        im = im.crop(caja)

    salidas = []
    for ancho, nombre in ((512, "logo.png"), (128, "logo-128.png")):
        v = im.copy()
        v.thumbnail((ancho, ancho), Image.Resampling.LANCZOS)
        # El logo es de colores planos: cuantizarlo con FASTOCTREE (el unico
        # metodo de Pillow que conserva alfa) baja el peso un orden de magnitud
        # sin diferencia visible.
        v = v.quantize(colors=64, method=Image.Quantize.FASTOCTREE)
        destino = ASSETS / nombre
        v.save(destino, "PNG", optimize=True)
        salidas.append((destino, v.size))

    # Previsualizacion sobre el fondo real de la pagina, para revisar halos.
    prev = Image.new("RGB", (im.width + 120, im.height + 120), INK_850)
    prev.paste(im, (60, 60), im)
    prev.thumbnail((520, 520), Image.Resampling.LANCZOS)
    ruta_prev = RAIZ / "scripts" / "_preview-logo-sobre-fondo.png"
    prev.save(ruta_prev, "PNG", optimize=True)

    return salidas, im.size, ruta_prev


# --------------------------------------------------------------------------
# 3. fotos: WebP + fallback JPG, escritorio + movil
# --------------------------------------------------------------------------

def construir_fotos(origen: Path, base: str):
    im = Image.open(origen).convert("RGB")
    ancho_full = min(im.width, MAX_ANCHO)
    salidas = []

    for ancho, sufijo in ((ancho_full, ""), (ANCHO_MOVIL, "-800")):
        if ancho >= im.width and sufijo == "":
            v = im.copy()
        else:
            alto = round(im.height * ancho / im.width)
            v = im.resize((ancho, alto), Image.Resampling.LANCZOS)

        webp = ASSETS / f"{base}{sufijo}.webp"
        v.save(webp, "WEBP", quality=80, method=6)
        salidas.append((webp, v.size))

        jpg = ASSETS / f"{base}{sufijo}.jpg"
        v.save(jpg, "JPEG", quality=82, optimize=True, progressive=True)
        salidas.append((jpg, v.size))

    return salidas


def construir_hero(origen: Path):
    """Foto de fondo del hero, servida desde el propio dominio.

    Va debajo de una capa oscura al 74-80%, así que admite bastante más
    compresión que una foto que se mira de frente. Aun así se queda en
    calidad 70 y no menos: el degradado oscuro es justo donde aparecería el
    banding, y ahorrar 3 KB no compensa una franja visible en el titular.
    """
    im = Image.open(origen).convert("RGB")
    salidas = []

    for ancho in (ANCHO_MOVIL, 1600):
        alto = round(im.height * ancho / im.width)
        v = im.resize((ancho, alto), Image.Resampling.LANCZOS)

        webp = ASSETS / f"hero-{ancho}.webp"
        v.save(webp, "WEBP", quality=70, method=6)
        salidas.append((webp, v.size))

        jpg = ASSETS / f"hero-{ancho}.jpg"
        v.save(jpg, "JPEG", quality=70, optimize=True, progressive=True)
        salidas.append((jpg, v.size))

    return salidas


# --------------------------------------------------------------------------
# 4. imagen para compartir por WhatsApp (Open Graph 1200x630)
# --------------------------------------------------------------------------

def _fuente(tam: int, negrita=True):
    candidatas = ["arialbd.ttf", "arial.ttf"] if negrita else ["arial.ttf"]
    for nombre in candidatas:
        for carpeta in ("C:/Windows/Fonts", "/usr/share/fonts/truetype/dejavu"):
            ruta = Path(carpeta) / nombre
            if ruta.exists():
                try:
                    return ImageFont.truetype(str(ruta), tam)
                except OSError:
                    pass
    return ImageFont.load_default()


def construir_icono_ios(logo: Path):
    """Icono de pantalla de inicio de iOS.

    Safari no respeta la transparencia: la compone sobre negro. Se entrega
    ya compuesto sobre el grafito del propio logo.
    """
    lado = 180
    lienzo = Image.new("RGB", (lado, lado), (34, 40, 42))   # --ink-700
    marca = Image.open(logo).convert("RGBA")
    marca.thumbnail((lado - 26, lado - 26), Image.Resampling.LANCZOS)
    lienzo.paste(marca, ((lado - marca.width) // 2, (lado - marca.height) // 2), marca)

    destino = ASSETS / "apple-touch-icon.png"
    lienzo.save(destino, "PNG", optimize=True)
    return destino


def construir_og(logo: Path, acento: str):
    W, H = 1200, 630
    lienzo = Image.new("RGB", (W, H), INK_850)
    d = ImageDraw.Draw(lienzo)

    acc = tuple(int(acento[i:i + 2], 16) for i in (1, 3, 5))

    # Reglas finas de fondo: textura de instrumento, no de gradiente.
    for x in range(0, W, 40):
        d.line([(x, 0), (x, H)], fill=(24, 28, 30), width=1)
    d.rectangle([0, 0, W, 8], fill=acc)

    marca = Image.open(logo).convert("RGBA")
    marca.thumbnail((300, 300), Image.Resampling.LANCZOS)
    lienzo.paste(marca, (86, (H - marca.height) // 2 - 40), marca)

    x = 86 + marca.width + 60
    d.text((x, 214), "MULTI INJECTION", font=_fuente(66), fill=(232, 234, 235))
    d.text((x, 288), "LA PAZ", font=_fuente(66), fill=acc)
    d.text((x, 380), "Especialistas en inyección automotriz",
           font=_fuente(30, False), fill=(154, 163, 166))
    d.text((x, 424), "Más de 30 años  ·  Caracas, Venezuela",
           font=_fuente(30, False), fill=(154, 163, 166))
    d.line([(x, 356), (x + 470, 356)], fill=acc, width=3)

    destino = ASSETS / "og-image.jpg"
    lienzo.save(destino, "JPEG", quality=86, optimize=True, progressive=True)
    return destino


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--palette", action="store_true",
                    help="solo reportar la paleta del logo")
    args = ap.parse_args()

    origen_logo = SRC / "logo-original.png"
    if not origen_logo.exists():
        sys.exit(f"No encuentro el logo original en {origen_logo}")

    print("\n" + "=" * 66)
    print("  PALETA DEL LOGO")
    print("=" * 66)
    paleta = extraer_paleta(origen_logo)
    print(f"  {'HEX':<9} {'RGB':<15} {'COBERTURA':>10}")
    for c in paleta:
        print(f"  {c['hex']:<9} {'{},{},{}'.format(*c['rgb']):<15} {c['pct']:>9.1f}%")

    # El acento es el color de marca mas frecuente que pasa AA sobre el fondo.
    fondo = hex_of(INK_850)
    acento = next(
        (c["hex"] for c in paleta if contraste(c["hex"], fondo) >= 4.5),
        paleta[0]["hex"],
    )
    print(f"\n  Acento -> {acento}   ({contraste(acento, fondo):.2f}:1 sobre {fondo})")
    print("  Variantes por luminosidad HSL:")
    for etiqueta, delta in (("hover ", +0.10), ("press ", -0.10), ("suave ", +0.34)):
        v = ajustar_luz(acento, delta)
        print(f"    --accent-{etiqueta.strip():<6} {v}  ({contraste(v, fondo):5.2f}:1)")

    if args.palette:
        return

    print("\n" + "=" * 66)
    print("  INVENTARIO DE ORIGEN")
    print("=" * 66)
    print(f"  {'ARCHIVO':<30} {'FORMATO':<10} {'DIMENSIONES':<14} {'PESO':>10}")
    for p in sorted(SRC.iterdir()):
        if not p.is_file():
            continue
        fmt = formato_real(p)
        try:
            dim = "{}x{}".format(*Image.open(p).size)
        except Exception:
            dim = "no legible"
        marca = ""
        if kb(p) > MAX_KB:
            marca = "  <- supera 300KB"
        if fmt.startswith("HEIF"):
            marca = "  <- NO se muestra en Chrome/Android"
        print(f"  {p.name:<30} {fmt:<10} {dim:<14} {kb(p):>8,.1f} KB{marca}")

    print("\n" + "=" * 66)
    print("  SALIDA")
    print("=" * 66)

    antes_logo = kb(origen_logo)
    salidas, tam, prev = construir_logo(origen_logo)
    print(f"  logo recortado a {tam[0]}x{tam[1]} px (fondo grafito eliminado)")
    for ruta, dim in salidas:
        ahorro = 100 * (1 - kb(ruta) / antes_logo)
        print(f"    {ruta.name:<24} {'{}x{}'.format(*dim):<12} "
              f"{kb(ruta):>8,.1f} KB   -{ahorro:.1f}%")
    print(f"    revision visual: {prev.relative_to(RAIZ)}")

    foto = SRC / "inyectores.jpg"
    if foto.exists() and formato_real(foto) == "JPEG":
        print(f"\n  inyectores  (origen {kb(foto):,.1f} KB)")
        for ruta, dim in construir_fotos(foto, "inyectores"):
            print(f"    {ruta.name:<24} {'{}x{}'.format(*dim):<12} {kb(ruta):>8,.1f} KB")

    hero = SRC / "hero-original.jpg"
    if hero.exists():
        print(f"\n  hero  (origen {kb(hero):,.1f} KB, descargado de Unsplash)")
        for ruta, dim in construir_hero(hero):
            print(f"    {ruta.name:<24} {'{}x{}'.format(*dim):<12} {kb(ruta):>8,.1f} KB")

    icono = construir_icono_ios(ASSETS / "logo.png")
    print(f"\n  {icono.name:<26} {'180x180':<12} {kb(icono):>8,.1f} KB   (iOS)")

    og = construir_og(ASSETS / "logo.png", acento)
    print(f"\n  {og.name:<26} {'1200x630':<12} {kb(og):>8,.1f} KB   (Open Graph)")
    print()


if __name__ == "__main__":
    main()
