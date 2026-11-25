"""Immagini caricate in Configurazione (logo dell'azienda).

Il file caricato non viene mai salvato così com'è: si apre con Pillow (che rifiuta i file che non sono
immagini), si converte in PNG RGBA e si riduce a 256 px; nel database resta solo questo PNG in base64.
Così niente SVG/HTML con script, niente metadati EXIF, dimensione limitata.
"""

from __future__ import annotations

import base64
import io
from functools import lru_cache

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

MASSIMO_BYTE = 2 * 1024 * 1024
LATO_MASSIMO = 256


def normalizza(dati: bytes, etichetta: str = "Immagine") -> str:
    if len(dati) > MASSIMO_BYTE:
        raise ValidationError(f"{etichetta}: file troppo grande (massimo 2 MB).")
    try:
        with Image.open(io.BytesIO(dati)) as img:
            if img.format not in {"PNG", "JPEG", "GIF", "WEBP", "BMP", "ICO"}:
                raise ValidationError(f"{etichetta}: formato non supportato (usa PNG o JPEG).")
            img.load()
            rgba = img.convert("RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValidationError(f"{etichetta}: il file non è un'immagine valida.") from exc
    rgba.thumbnail((LATO_MASSIMO, LATO_MASSIMO))
    uscita = io.BytesIO()
    rgba.save(uscita, format="PNG", optimize=True)
    return base64.b64encode(uscita.getvalue()).decode()


@lru_cache(maxsize=32)
def png_quadrato(base64_png: str, lato: int) -> bytes:
    """Il logo dentro un quadrato lato×lato (proporzioni mantenute, bordi trasparenti), per le icone dei menu."""
    with Image.open(io.BytesIO(base64.b64decode(base64_png))) as img:
        rgba = img.convert("RGBA")
    rgba.thumbnail((lato, lato), Image.Resampling.LANCZOS)
    quadro = Image.new("RGBA", (lato, lato), (0, 0, 0, 0))
    quadro.paste(rgba, ((lato - rgba.width) // 2, (lato - rgba.height) // 2), rgba)
    uscita = io.BytesIO()
    quadro.save(uscita, format="PNG")
    return uscita.getvalue()
