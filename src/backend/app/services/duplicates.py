from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record_audit
from ..models import Document, ExtractedField
from .constants import EXCEL_EXT, IMAGE_EXT, PDF_EXT
from .consistency_groups import container_numbers, identifiers
from .consistency_matrix import TRANSPORT

DUPLICATE_KEYS = ("transport.waybill_no", "transport.master_waybill_no", "transport.house_waybill_no",
                  "transport.container_no", "document.doc_number")
NON_UNIQUE_TYPES = {"CERTIFICATE_OF_ORIGIN", "PHYTOSANITARY_CERTIFICATE", "CUSTOMS_DECLARATION_SCAN", "OTHER"}


async def _document_keys(session: AsyncSession, doc: Document) -> dict[str, set[str]]:
    rows = (
        await session.execute(
            select(ExtractedField).where(
                ExtractedField.document_id == doc.id,
                ExtractedField.line_item_id.is_(None),
                ExtractedField.field_key.in_(DUPLICATE_KEYS),
            )
        )
    ).scalars().all()
    keys: dict[str, set[str]] = {}
    for row in rows:
        if row.normalized_value:
            keys.setdefault(row.field_key, set()).add(row.normalized_value.strip().upper())
    return keys


def _same_physical_kind(a: Document, b: Document) -> bool:
    ext_a, ext_b = a.file_ext.lower(), b.file_ext.lower()
    groups = [
        ext_a in PDF_EXT | IMAGE_EXT and ext_b in PDF_EXT | IMAGE_EXT,
        ext_a in EXCEL_EXT and ext_b in EXCEL_EXT,
    ]
    return any(groups)


def _similar_type(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return a == b or a in NON_UNIQUE_TYPES or b in NON_UNIQUE_TYPES


def distinct_transport_scope(a: dict[str, set[str]], b: dict[str, set[str]]) -> bool:
    for key, parser in (
        ("transport.waybill_no", identifiers), ("transport.house_waybill_no", identifiers),
        ("transport.master_waybill_no", identifiers), ("transport.container_no", container_numbers),
    ):
        left = set().union(*(parser(v) for v in a.get(key, ())))
        right = set().union(*(parser(v) for v in b.get(key, ())))
        if left and right and left != right:
            return True
    return False


async def detect_duplicates(session: AsyncSession, doc: Document) -> None:
    """Flag documents that plausibly describe the same instrument.

    Conservative on purpose: the same type plus the same waybill/container/document number is
    required, so an invoice and a packing list sharing an invoice number are never merged.
    A shared document number across unrelated contracts is not enough either.
    """
    others = (
        await session.execute(
            select(Document).where(
                Document.shipment_id == doc.shipment_id,
                Document.id != doc.id,
                Document.status.in_(["EXTRACTED"]),
            )
        )
    ).scalars().all()

    doc_keys = await _document_keys(session, doc)
    if not doc_keys:
        return

    for other in others:
        if not _same_physical_kind(doc, other):
            continue
        other_keys = await _document_keys(session, other)
        if not other_keys:
            continue
        if not _similar_type(doc.doc_type, other.doc_type):
            continue
        if doc.doc_type in TRANSPORT and other.doc_type == doc.doc_type and distinct_transport_scope(doc_keys, other_keys):
            continue

        transport_match = bool(
            doc_keys.get("transport.waybill_no")
            and doc_keys.get("transport.waybill_no") & other_keys.get("transport.waybill_no", set())
            and doc.doc_type == other.doc_type
        )
        container_match = bool(
            doc_keys.get("transport.container_no")
            and doc_keys.get("transport.container_no") & other_keys.get("transport.container_no", set())
            and doc.doc_type == other.doc_type
        )
        doc_number_match = bool(
            doc_keys.get("document.doc_number")
            and doc_keys.get("document.doc_number") & other_keys.get("document.doc_number", set())
            and doc.doc_type == other.doc_type
            and doc.doc_type not in NON_UNIQUE_TYPES
        )
        strong = transport_match or container_match
        weak = doc_number_match

        if not (strong or weak):
            continue

        from .ocr_diff import first_difference

        try:
            difference = await first_difference(doc, other)
        except Exception:
            difference = None

        newer, older = (doc, other) if doc.id > other.id else (other, doc)
        confidence = "HIGH" if (strong and not difference) else ("MEDIUM" if strong else "LOW")
        older.status = "IS_DUPLICATE_OF"
        older.duplicate_of_id = newer.id
        older.version_note = (
            f"Trùng số {', '.join(f'{k}=' + ','.join(v) for k, v in doc_keys.items())} với {newer.filename}"
            f"; khác biệt đầu tiên: {difference or 'không thấy khác biệt rõ'} (tin cậy {confidence})"
        )
        record_audit(
            session,
            "document",
            older.id,
            "POTENTIAL_DUPLICATE",
            payload={"duplicate_of": newer.id, "confidence": confidence, "difference": difference},
        )
        return
