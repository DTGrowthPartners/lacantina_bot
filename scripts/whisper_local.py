"""Transcribe un audio con faster-whisper local (fallback de la API de OpenAI).

NO corre con el venv del bot: se invoca con el intérprete de `whisper-env`
(ver `whisper_local_python` en config), que es el único que tiene instalado
faster-whisper. Por eso es un script suelto y no un módulo importable.

Uso:  python whisper_local.py <ruta_audio> [idioma] [modelo]
Salida: el texto transcrito por stdout. Si no hay nada que transcribir, sale
vacío con código 0; cualquier error revienta con código != 0 y el caller
(app/integrations/whisper.py) lo trata como "no se pudo".
"""

import sys

from faster_whisper import WhisperModel

audio_path = sys.argv[1]
idioma = sys.argv[2] if len(sys.argv) > 2 else "es"
modelo = sys.argv[3] if len(sys.argv) > 3 else "small"

# int8 en CPU: el VPS no tiene GPU y una nota de voz de WhatsApp son segundos.
model = WhisperModel(modelo, device="cpu", compute_type="int8")
segments, _info = model.transcribe(audio_path, language=idioma, beam_size=5)

texto = " ".join(s.text.strip() for s in segments).strip()
sys.stdout.write(texto)
