from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ConsistencyFinding, ConsistencyRun, Document, DocumentSection, ExtractedField, LineItem, Shipment
from .consistency_groups import Evidence, Source
from .consistency_matrix import SUPPORTED, TRANSPORT, public_matrix
from .consistency_rules import MATCH, MISMATCH, MISSING, VERIFY, Check, evaluate
from .duplicates import DUPLICATE_KEYS, distinct_transport_scope
from .fields import ALL_FIELD_SPECS

RULES_VERSION = 3
RISK_ORDER = {"ERROR": 0, "WARNING": 1, "INFO": 2}
STATUS_ORDER = {MISMATCH: 0, VERIFY: 1, MISSING: 2, MATCH: 3}
EXCLUDED_STATUSES = {"SUPERSEDED_BY", "IS_DUPLICATE_OF"}


async def collect_sources(session: AsyncSession, shipment_id: int) -> list[Source]:
    docs = (await session.execute(
        select(Document).where(Document.shipment_id == shipment_id).order_by(Document.id)
    )).scalars().all()
    active = {doc.id: doc for doc in docs if doc.status not in EXCLUDED_STATUSES}
    historical_duplicates = [
        doc for doc in docs if doc.doc_type in TRANSPORT and doc.status == "IS_DUPLICATE_OF"
        and doc.duplicate_of_id in active and active[doc.duplicate_of_id].doc_type == doc.doc_type
    ]
    if historical_duplicates:
        candidates = {doc.id for doc in historical_duplicates} | {doc.duplicate_of_id for doc in historical_duplicates}
        identity_rows = (await session.execute(
            select(ExtractedField.document_id, ExtractedField.field_key, ExtractedField.raw_value, ExtractedField.normalized_value)
            .where(ExtractedField.document_id.in_(candidates), ExtractedField.field_key.in_(DUPLICATE_KEYS))
        )).all()
        identities: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for doc_id, key, raw, normalized in identity_rows:
            if value := normalized or raw:
                identities[doc_id][key].add(value)
        for doc in historical_duplicates:
            if distinct_transport_scope(identities[doc.id], identities[doc.duplicate_of_id]):
                active[doc.id] = doc
    if not active:
        return []
    sections = (await session.execute(
        select(DocumentSection).where(DocumentSection.document_id.in_(active)).order_by(DocumentSection.page_start)
    )).scalars().all()
    sources_by_doc: dict[int, list[Source]] = defaultdict(list)
    by_section: dict[int, Source] = {}
    def status_of(doc: Document) -> str:
        return "EXTRACTED" if doc.status == "IS_DUPLICATE_OF" else doc.status

    for section in sections:
        if section.section_type not in SUPPORTED:
            continue
        doc = active[section.document_id]
        source = Source(doc.id, doc.filename, section.section_type, section.id, section.page_start + 1, status_of(doc))
        sources_by_doc[doc.id].append(source)
        by_section[section.id] = source
    for doc in active.values():
        if not sources_by_doc[doc.id] and (doc.doc_type in SUPPORTED or doc.status != "EXTRACTED"):
            sources_by_doc[doc.id].append(
                Source(doc.id, doc.filename, doc.doc_type or "OTHER", first_page=1 if doc.page_count else None, status=status_of(doc))
            )

    rows = (await session.execute(
        select(ExtractedField, LineItem.seq)
        .outerjoin(LineItem, ExtractedField.line_item_id == LineItem.id)
        .where(ExtractedField.document_id.in_(active))
        .order_by(ExtractedField.id)
    )).all()
    for field, seq in rows:
        candidates = sources_by_doc[field.document_id]
        source = by_section.get(field.section_id)
        if source is None and len(candidates) == 1:
            source = candidates[0]
        if source is None:
            matching = [s for s in candidates if s.doc_type == field.section_type]
            if len(matching) == 1:
                source = matching[0]
        if source is not None and source.status == "EXTRACTED":
            source.add(Evidence(
                id=field.id, key=field.field_key, raw=field.raw_value, normalized=field.normalized_value,
                page=field.page, bbox=field.bbox, cell_ref=field.cell_ref,
                grounded=field.grounding_verified, review_status=field.review_status,
                provenance=field.provenance_type, unit=field.unit,
                line=seq if field.line_item_id is not None else None,
            ))
    return [source for doc_id in sorted(active) for source in sources_by_doc[doc_id]]


def all_evidence(source: Source):
    for rows in source.fields.values():
        yield from rows
    for line in source.lines.values():
        for rows in line.values():
            yield from rows


def input_hash(sources: list[Source]) -> str:
    payload = [
        (source.as_dict(), [asdict(e) for e in sorted(all_evidence(source), key=lambda row: row.id)])
        for source in sources
    ]
    return hashlib.sha256(json.dumps([RULES_VERSION, payload], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


async def _latest_run(session: AsyncSession, shipment_id: int) -> ConsistencyRun | None:
    return (await session.execute(
        select(ConsistencyRun).where(ConsistencyRun.shipment_id == shipment_id)
        .order_by(ConsistencyRun.id.desc()).limit(1)
    )).scalar_one_or_none()


def _signature(check: Check, source_by_field: dict[int, Source]) -> list:
    locators = sorted({
        (source_by_field[e.id].key, e.key, e.line if e.line is not None else -1, e.value or "")
        for e in check.evidence
    })
    return [check.rule, check.status, sorted(s.key for s in check.sources), locators, check.calculated_value]


def _signature_key(signature) -> str:
    return json.dumps(signature, ensure_ascii=False, sort_keys=True)


async def run_consistency_check(session: AsyncSession, _llm, shipment_id: int, force: bool = False) -> ConsistencyRun:
    # The rule engine does no network I/O. Serialize replace/dismiss writes
    # across API and worker while taking the same evidence snapshot.
    await session.commit()
    if session.get_bind().dialect.name == "sqlite":
        await session.execute(text("BEGIN IMMEDIATE"))
    else:
        await session.execute(select(Shipment.id).where(Shipment.id == shipment_id).with_for_update())
    sources = await collect_sources(session, shipment_id)
    digest = input_hash(sources)
    latest = await _latest_run(session, shipment_id)
    if not force and latest is not None and latest.status == "DONE" and latest.input_hash == digest:
        await session.commit()
        return latest

    groups, checks = evaluate(sources)
    run = ConsistencyRun(
        shipment_id=shipment_id, status="DONE", input_hash=digest,
        details={"rules_version": RULES_VERSION, "groups": groups},
        finding_count=sum(check.severity != "INFO" for check in checks),
    )
    session.add(run)
    await session.flush()
    previous = (await session.execute(
        select(ConsistencyFinding).where(ConsistencyFinding.shipment_id == shipment_id, ConsistencyFinding.dismissed.is_(True))
    )).scalars().all()
    dismissed = {_signature_key(row.signature): row for row in previous if row.signature}
    await session.execute(delete(ConsistencyFinding).where(ConsistencyFinding.shipment_id == shipment_id))
    source_by_field = {e.id: source for source in sources for e in all_evidence(source)}
    for check in checks:
        signature = _signature(check, source_by_field)
        prior = dismissed.get(_signature_key(signature))
        session.add(ConsistencyFinding(
            run_id=run.id, shipment_id=shipment_id, severity=check.severity, category=check.rule,
            title=check.title, explanation=check.reason, field_ids=list(dict.fromkeys(e.id for e in check.evidence)),
            signature=signature, details=check.metadata(), dismissed=prior is not None,
            dismiss_note=prior.dismiss_note if prior else None, dismissed_at=prior.dismissed_at if prior else None,
        ))
    await session.commit()
    return run


def _run_dict(run: ConsistencyRun | None) -> dict | None:
    if run is None:
        return None
    return {
        "id": run.id, "status": run.status, "error": run.error, "finding_count": run.finding_count,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


def _value_payload(source: Source, evidence: Evidence) -> dict:
    spec = ALL_FIELD_SPECS.get(evidence.key)
    return {
        "field_id": evidence.id, "source_id": source.key, "document_id": source.document_id,
        "filename": source.filename, "doc_type": source.doc_type, "page": evidence.page, "bbox": evidence.bbox,
        "cell_ref": evidence.cell_ref, "field_key": evidence.key, "value": evidence.value,
        "label": spec.label_vi if spec else evidence.key,
        "trusted": evidence.trusted, "provenance": evidence.provenance, "line": evidence.line,
    }


async def consistency_report(session: AsyncSession, shipment_id: int) -> dict:
    run = await _latest_run(session, shipment_id)
    counts = {"error": 0, "warning": 0}
    status_counts = {key: 0 for key in STATUS_ORDER}
    groups, findings = [], []
    stale = False
    if run is not None:
        sources = await collect_sources(session, shipment_id)
        stale = run.input_hash != input_hash(sources)
        if not stale and run.details:
            groups = run.details["groups"]
            evidence = {e.id: (source, e) for source in sources for e in all_evidence(source)}
            rows = (await session.execute(
                select(ConsistencyFinding).where(ConsistencyFinding.run_id == run.id)
            )).scalars().all()
            for row in sorted(rows, key=lambda item: (
                RISK_ORDER[item.severity], STATUS_ORDER[item.details["status"]], item.id,
            )):
                values = [_value_payload(*evidence[fid]) for fid in row.field_ids if fid in evidence]
                record = {
                    "id": row.id, "severity": row.severity, "category": row.category, "title": row.title,
                    "explanation": row.explanation, "dismissed": row.dismissed, "dismiss_note": row.dismiss_note,
                    "values": values, **row.details,
                }
                if not row.dismissed:
                    status_counts[record["status"]] += 1
                    if row.severity in ("ERROR", "WARNING"):
                        counts[row.severity.lower()] += 1
                findings.append(record)
    return {"run": _run_dict(run), "stale": stale, "findings": findings, "counts": counts,
            "status_counts": status_counts, "groups": groups, "matrix": public_matrix()}


async def dismiss_finding(session: AsyncSession, finding: ConsistencyFinding, note: str | None) -> None:
    finding.dismissed = True
    finding.dismiss_note = note
    finding.dismissed_at = datetime.now(timezone.utc)
