#!/usr/bin/env python3
"""Importa contactos de un .vcf a clientes + tag de difusión.

Uso:
  python scripts/import_contacts_vcf.py /ruta/contacts.vcf \
    --tag "Base difusión VCF" --source "contacts.vcf"

Reglas:
- Normaliza celulares Colombia a +57XXXXXXXXXX.
- Inserta nuevos como etiqueta=prospecto.
- No pisa nombres existentes.
- No importa ni etiqueta contactos bloqueados, personales, equipo o internos.
- Asigna un tag para poder segmentar difusiones desde /admin/difusiones.
"""

from __future__ import annotations

import argparse
import os
import quopri
import re
import sys
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class VcfContact:
    nombre: str | None
    numero: str


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def _unfold(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if line.startswith((" ", "\t")) and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _decode_vcard_value(line: str) -> str:
    if ":" not in line:
        return ""
    meta, value = line.split(":", 1)
    meta_upper = meta.upper()
    if "QUOTED-PRINTABLE" in meta_upper:
        try:
            charset = "utf-8"
            match = re.search(r"CHARSET=([^;:]+)", meta, flags=re.I)
            if match:
                charset = match.group(1)
            return quopri.decodestring(value).decode(charset, errors="replace")
        except Exception:
            return value
    return value


def normalizar_numero_colombia(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 12 and digits.startswith("57") and digits[2] == "3":
        return "+" + digits
    if len(digits) == 10 and digits.startswith("3"):
        return "+57" + digits
    if len(digits) == 11 and digits.startswith("0") and digits[1] == "3":
        return "+57" + digits[1:]
    return None


def parse_vcf(path: Path) -> tuple[list[VcfContact], dict[str, int]]:
    lines = _unfold(path.read_text(errors="ignore").splitlines())
    cards: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] | None = None
    for line in lines:
        upper = line.upper()
        if upper == "BEGIN:VCARD":
            current = {}
        elif upper == "END:VCARD":
            if current is not None:
                cards.append(current)
            current = None
        elif current is not None and ":" in line:
            key = line.split(":", 1)[0].split(";", 1)[0].upper()
            current.setdefault(key, []).append(line)

    contacts: list[VcfContact] = []
    stats = {
        "cards": len(cards),
        "tel_lines": 0,
        "valid_numbers": 0,
        "invalid_numbers": 0,
        "duplicates_in_file": 0,
    }
    seen: set[str] = set()
    for card in cards:
        name_line = (card.get("FN") or card.get("N") or [""])[0]
        name = " ".join(_decode_vcard_value(name_line).replace(";", " ").split()) or None
        for tel_line in card.get("TEL", []):
            stats["tel_lines"] += 1
            numero = normalizar_numero_colombia(_decode_vcard_value(tel_line))
            if not numero:
                stats["invalid_numbers"] += 1
                continue
            if numero in seen:
                stats["duplicates_in_file"] += 1
                continue
            seen.add(numero)
            stats["valid_numbers"] += 1
            contacts.append(VcfContact(nombre=name[:255] if name else None, numero=numero))
    return contacts, stats


def import_contacts(
    contacts: list[VcfContact],
    *,
    database_url: str,
    tag_name: str,
    source: str,
    dry_run: bool,
) -> dict[str, int | str]:
    result: dict[str, int | str] = {
        "candidatos": len(contacts),
        "insertados": 0,
        "actualizados": 0,
        "saltados_sensibles": 0,
        "tag_asignado": 0,
        "tag": tag_name,
        "dry_run": int(dry_run),
    }
    if dry_run:
        return result

    import psycopg2
    from psycopg2.extras import DictCursor

    with psycopg2.connect(database_url) as conn:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(
                """
                INSERT INTO tags (nombre, color, descripcion, orden, created_by)
                VALUES (%s, '#F59E0B', %s, 100, 'vcf-import')
                ON CONFLICT (nombre) DO UPDATE
                    SET descripcion = EXCLUDED.descripcion
                RETURNING id
                """,
                (tag_name, f"Contactos importados desde {source} para difusiones."),
            )
            tag_id = cur.fetchone()["id"]

            for contact in contacts:
                cur.execute(
                    """
                    SELECT c.id, c.nombre, c.etiqueta, c.bloqueado,
                           ni.id AS interno_id, em.id AS equipo_id
                      FROM clientes c
                 LEFT JOIN numeros_internos ni
                        ON ni.numero_whatsapp = c.numero_whatsapp AND ni.activo=true
                 LEFT JOIN equipo_miembros em
                        ON em.numero_whatsapp = c.numero_whatsapp AND em.activo=true
                     WHERE c.numero_whatsapp=%s
                    """,
                    (contact.numero,),
                )
                row = cur.fetchone()
                if row and (
                    row["bloqueado"]
                    or row["etiqueta"] in ("personal", "equipo")
                    or row["interno_id"]
                    or row["equipo_id"]
                ):
                    result["saltados_sensibles"] = int(result["saltados_sensibles"]) + 1
                    continue

                if row:
                    cur.execute(
                        """
                        UPDATE clientes
                           SET nombre = CASE
                                 WHEN COALESCE(NULLIF(TRIM(nombre), ''), '') = ''
                                   THEN COALESCE(%s, nombre)
                                 ELSE nombre
                               END,
                               etiqueta = COALESCE(etiqueta, 'prospecto'),
                               metadata = metadata || jsonb_build_object('import_source', %s)
                         WHERE id=%s
                        RETURNING id
                        """,
                        (contact.nombre, source, row["id"]),
                    )
                    cliente_id = cur.fetchone()["id"]
                    result["actualizados"] = int(result["actualizados"]) + 1
                else:
                    cur.execute(
                        """
                        INSERT INTO clientes (
                            numero_whatsapp, nombre, etiqueta, metadata
                        )
                        VALUES (
                            %s, %s, 'prospecto', jsonb_build_object('import_source', %s)
                        )
                        RETURNING id
                        """,
                        (contact.numero, contact.nombre, source),
                    )
                    cliente_id = cur.fetchone()["id"]
                    result["insertados"] = int(result["insertados"]) + 1

                cur.execute(
                    """
                    INSERT INTO cliente_tags (cliente_id, tag_id, added_by)
                    VALUES (%s, %s, 'vcf-import')
                    ON CONFLICT (cliente_id, tag_id) DO NOTHING
                    """,
                    (cliente_id, tag_id),
                )
                if cur.rowcount:
                    result["tag_asignado"] = int(result["tag_asignado"]) + 1
        conn.commit()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("vcf_path", type=Path)
    parser.add_argument("--tag", default="Base difusión VCF")
    parser.add_argument("--source", default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    _load_env(Path(".env"))
    db_url = args.database_url or os.getenv("DATABASE_URL_SYNC")
    if not db_url:
        print("ERROR: falta DATABASE_URL_SYNC o --database-url", file=sys.stderr)
        return 2
    contacts, stats = parse_vcf(args.vcf_path)
    result = import_contacts(
        contacts,
        database_url=db_url,
        tag_name=args.tag.strip()[:50],
        source=args.source or args.vcf_path.name,
        dry_run=args.dry_run,
    )
    print("VCF_STATS", stats)
    print("IMPORT_RESULT", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
