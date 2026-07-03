"""Validacion compartida de nombres para reservas.

El objetivo es evitar que instrucciones, preguntas o ubicaciones terminen como
nombre del cliente cuando el bot esta completando una reserva.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class NombreValidacion:
    es_nombre: bool
    nombre_limpio: str | None = None
    razon: str = ""
    confianza: str = "alta"


_LETRAS = r"A-Za-zÁÉÍÓÚÜÑáéíóúüñ"
_REEMPLAZOS = str.maketrans("áéíóúüñ", "aeiouun")

_NOMBRES_INVALIDOS_EXACTOS = {
    "si", "sí", "no", "ok", "okay", "dale", "listo", "gracias",
    "por favor", "porfa", "porfis", "porfi", "correcto", "confirmo",
    "confirmado", "perfecto", "bueno", "claro", "vale",
}

_CONECTORES_NOMBRE = {
    "de", "del", "la", "las", "los", "el", "y", "da", "do", "dos",
}

_PALABRAS_NO_NOMBRE = {
    "a", "al", "ahi", "alla", "alli", "antes", "asi", "cual", "cuando",
    "como", "con", "cerca", "despues", "donde", "en", "esa", "ese", "eso",
    "esta", "estan", "este", "esto", "me", "mi", "para",
    "pero", "por", "que", "qué", "quien", "quién", "se", "si", "sí",
    "sin", "su", "te", "toda", "todo", "tu", "una", "uno", "unos",
}
_NOMBRES_INVALIDOS_NORMALIZADOS = {
    normalizar.casefold().translate(_REEMPLAZOS)
    for normalizar in _NOMBRES_INVALIDOS_EXACTOS
}

_PATRONES_FRASE_NO_NOMBRE = (
    r"\b(?:mesa|mesas|puesto|puestos|silla|sillas|zona|cantina|vip|rumbero|"
    r"barra|tarima|pantalla|bano|banos|baño|baños|esquina|ubicacion|"
    r"ubicación|direccion|dirección|location|maps?|mapa|cover|evento|"
    r"reserva|reservar|reservada|reservado)\b",
    r"\b(?:quedariamos|quedaríamos|quedamos|quedar|queda|quedaria|"
    r"quedaría|quedarian|quedarían)\b",
    r"\b(?:dime|digame|dígame|me\s+dice|me\s+dices|me\s+indica|"
    r"indicame|indícame|confirmame|confírmame|confirmar?me|"
    r"me\s+confirmas?|me\s+confirma|puedes\s+confirmar?me|"
    r"puede\s+confirmar?me|puedes\s+mandarme|puede\s+mandarme|"
    r"mandame|mándame|enviame|envíame|pasame|pásame|revisa|"
    r"revísame|ayudame|ayúdame)\b",
    r"\b(?:quiero|queremos|quisiera|quisiéramos|puedo|podemos|podriamos|"
    r"podríamos|seria|sería|necesito|necesitamos|prefiero|preferimos)\b",
    r"^(?:y\s+)?(?:es\s+)?(?:en|al|a\s+la|a\s+el|para|por)\b",
    r"\b(?:cerca|junto|pegad[ao]s?|al\s+lado|frente|fondo|entrada|"
    r"primer[ao]?|segund[ao]?|tercer[ao]?|arriba|abajo)\b",
)


def normalizar_texto_nombre(valor: str | None) -> str:
    texto = (valor or "").casefold()
    return texto.translate(_REEMPLAZOS)


def limpiar_candidato_nombre(valor: str | None) -> str:
    nombre = re.sub(r"\s+", " ", (valor or "")).strip(" \t\r\n.,;:!?\"'")
    nombre = re.sub(
        r"^(?:por\s+favor|porfa|porfis|porfi)\s+",
        "",
        nombre,
        flags=re.IGNORECASE,
    ).strip(" \t\r\n.,;:!?\"'")
    nombre = re.sub(
        r"^(?:mi nombre(?: es)?|soy|me llamo|a nombre de)\s+",
        "",
        nombre,
        flags=re.IGNORECASE,
    ).strip(" \t\r\n.,;:!?\"'")
    nombre = re.sub(
        r"\s+(?:por favor|gracias|porfa|porfis|porfi|por favor gracias|"
        r"est[aá] bien|est[aá] correcto|as[ií] est[aá] bien|correcto)$",
        "",
        nombre,
        flags=re.IGNORECASE,
    ).strip(" \t\r\n.,;:!?\"'")
    return nombre


def parece_frase_no_nombre(valor: str | None) -> bool:
    original = valor or ""
    texto = normalizar_texto_nombre(original)
    if not texto:
        return True
    if re.search(r"[¿?]", original):
        return True
    return any(re.search(patron, texto, flags=re.IGNORECASE) for patron in _PATRONES_FRASE_NO_NOMBRE)


def validar_nombre_reserva(valor: str | None) -> NombreValidacion:
    nombre = limpiar_candidato_nombre(valor)
    if not nombre:
        return NombreValidacion(False, None, "vacio")
    if not (2 <= len(nombre) <= 80):
        return NombreValidacion(False, None, "longitud")
    if len(nombre.split()) > 8:
        return NombreValidacion(False, None, "demasiadas_palabras")
    if re.search(r"https?://|www\.|@\w|#[0-9A-Za-z_]+", nombre, flags=re.IGNORECASE):
        return NombreValidacion(False, None, "link_o_usuario")
    if re.search(r"\d", nombre):
        return NombreValidacion(False, None, "contiene_numeros")
    if not re.search(fr"[{_LETRAS}]", nombre):
        return NombreValidacion(False, None, "sin_letras")

    normalizado = normalizar_texto_nombre(nombre)
    if normalizado in _NOMBRES_INVALIDOS_NORMALIZADOS:
        return NombreValidacion(False, None, "respuesta_generica")
    if parece_frase_no_nombre(nombre):
        return NombreValidacion(False, None, "parece_frase")

    tokens = re.findall(fr"[{_LETRAS}]+", nombre)
    texto_solo_tokens = " ".join(tokens)
    if normalizar_texto_nombre(texto_solo_tokens) != normalizar_texto_nombre(nombre):
        return NombreValidacion(False, None, "caracteres_no_nombre")

    tokens_norm = [normalizar_texto_nombre(t) for t in tokens]
    tokens_nombre = [t for t in tokens_norm if t not in _CONECTORES_NOMBRE]
    if not tokens_nombre:
        return NombreValidacion(False, None, "sin_tokens_nombre")
    if any(t in _PALABRAS_NO_NOMBRE for t in tokens_norm if t not in _CONECTORES_NOMBRE):
        return NombreValidacion(False, None, "palabras_funcionales")
    if any(len(t) < 2 for t in tokens_nombre):
        return NombreValidacion(False, None, "token_muy_corto")

    confianza = "alta"
    if len(tokens_nombre) == 1:
        confianza = "media"
    return NombreValidacion(True, nombre, "ok", confianza)


def limpiar_nombre_reserva(valor: str | None) -> str | None:
    validacion = validar_nombre_reserva(valor)
    return validacion.nombre_limpio if validacion.es_nombre else None
