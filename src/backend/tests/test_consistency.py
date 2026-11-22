from __future__ import annotations

from types import SimpleNamespace

from app.models import Document, ExtractedField, LineItem, Shipment
from app.services.consistency import (
    CheckField,
    build_batches,
    consistency_report,
    run_consistency_check,
)
from app.services.llm import LLMError


class StubLLM:
    settings = SimpleNamespace(text_model="sonya", vision_model="vision_sonya", max_tokens=32000)

    def __init__(self, response: dict | None = None, error: Exception | None = None):
        self.response = response or {"findings": []}
        self.error = error
        self.calls: list[str] = []

    async def complete_json_validated(
        self, prompt, schema_name, schema, validator, image_png=None, max_attempts=3, max_tokens=None, model_override=None
    ):
        self.calls.append(prompt)
        if self.error is not None:
            raise self.error
        return validator(self.response)


async def _doc(session, shipment_id: int, doc_type: str, filename: str, status: str = "EXTRACTED") -> Document:
    doc = Document(
        shipment_id=shipment_id,
        filename=filename,
        file_ext=".pdf",
        storage_path=f"/tmp/{filename}",
        checksum=filename,
        doc_type=doc_type,
        status=status,
    )
    session.add(doc)
    await session.flush()
    return doc


async def _field(session, doc: Document, key: str, value: str, page: int = 1, line_item_id: int | None = None) -> ExtractedField:
    field = ExtractedField(
        document_id=doc.id, field_key=key, raw_value=value, normalized_value=value, page=page, line_item_id=line_item_id
    )
    session.add(field)
    await session.flush()
    return field


async def _seed(session, two_docs: bool = True):
    shipment = Shipment(code="LS1")
    session.add(shipment)
    await session.flush()
    pl = await _doc(session, shipment.id, "PACKING_LIST", "packing-list.pdf")
    f1 = await _field(session, pl, "transport.gross_weight_kg", "26400")
    f2 = None
    bl = None
    if two_docs:
        bl = await _doc(session, shipment.id, "TRANSPORT_DOCUMENT", "bill.pdf")
        f2 = await _field(session, bl, "transport.gross_weight_kg", "26040")
    await session.commit()
    return SimpleNamespace(sid=shipment.id, pl=pl, bl=bl, f1=f1, f2=f2)


def _finding(*ids: int, severity: str = "ERROR") -> dict:
    return {
        "severity": severity,
        "category": "weight",
        "title": "Gross lệch",
        "explanation": "Trọng lượng gross khác nhau",
        "field_ids": list(ids),
    }


async def test_single_document_skips_llm(db_session):
    s = await _seed(db_session, two_docs=False)
    llm = StubLLM()
    run = await run_consistency_check(db_session, llm, s.sid)
    assert run.status == "DONE"
    assert run.finding_count == 0
    assert llm.calls == []


async def test_grounded_finding_is_stored(db_session):
    s = await _seed(db_session)
    llm = StubLLM({"findings": [_finding(s.f2.id, s.f1.id)]})
    run = await run_consistency_check(db_session, llm, s.sid)
    report = await consistency_report(db_session, s.sid)
    assert run.finding_count == 1
    finding = report["findings"][0]
    assert [v["value"] for v in finding["values"]] == ["26400", "26040"]
    assert [v["filename"] for v in finding["values"]] == ["packing-list.pdf", "bill.pdf"]
    assert finding["values"][0]["page"] == 1
    assert report["counts"] == {"error": 1, "warning": 0}
    assert report["run"]["status"] == "DONE"


async def test_hallucinated_ids_and_single_document_findings_are_dropped(db_session):
    s = await _seed(db_session)
    llm = StubLLM({"findings": [_finding(s.f1.id, 999999), _finding(s.f1.id)]})
    run = await run_consistency_check(db_session, llm, s.sid)
    assert run.finding_count == 0
    assert (await consistency_report(db_session, s.sid))["findings"] == []


async def test_superseded_document_fields_are_excluded(db_session):
    s = await _seed(db_session)
    s.bl.status = "SUPERSEDED_BY"
    await db_session.commit()
    llm = StubLLM({"findings": [_finding(s.f1.id, s.f2.id)]})
    run = await run_consistency_check(db_session, llm, s.sid)
    assert llm.calls == []
    assert run.finding_count == 0


async def test_superseding_removes_previous_findings(db_session):
    s = await _seed(db_session)
    llm = StubLLM({"findings": [_finding(s.f1.id, s.f2.id)]})
    await run_consistency_check(db_session, llm, s.sid)
    s.bl.status = "SUPERSEDED_BY"
    await db_session.commit()
    await run_consistency_check(db_session, llm, s.sid)
    assert (await consistency_report(db_session, s.sid))["findings"] == []


async def test_unchanged_input_skips_llm_and_force_reruns(db_session):
    s = await _seed(db_session)
    llm = StubLLM()
    await run_consistency_check(db_session, llm, s.sid)
    await run_consistency_check(db_session, llm, s.sid)
    assert len(llm.calls) == 1
    await run_consistency_check(db_session, llm, s.sid, force=True)
    assert len(llm.calls) == 2


async def test_dismissal_survives_rerun_for_same_fields(db_session):
    from app.models import ConsistencyFinding
    from sqlalchemy import select

    s = await _seed(db_session)
    llm = StubLLM({"findings": [_finding(s.f1.id, s.f2.id)]})
    await run_consistency_check(db_session, llm, s.sid)
    row = (await db_session.execute(select(ConsistencyFinding))).scalar_one()
    row.dismissed = True
    row.dismiss_note = "ok"
    s.f1.normalized_value = "26401"
    await db_session.commit()
    await run_consistency_check(db_session, llm, s.sid)
    assert len(llm.calls) == 2
    report = await consistency_report(db_session, s.sid)
    assert report["findings"][0]["dismissed"] is True
    assert report["findings"][0]["dismiss_note"] == "ok"
    assert report["counts"] == {"error": 0, "warning": 0}


async def test_llm_failure_records_failed_run_and_keeps_previous_findings(db_session):
    s = await _seed(db_session)
    llm = StubLLM({"findings": [_finding(s.f1.id, s.f2.id)]})
    await run_consistency_check(db_session, llm, s.sid)
    s.f1.normalized_value = "26401"
    await db_session.commit()
    llm.error = LLMError("boom")
    run = await run_consistency_check(db_session, llm, s.sid)
    assert run.status == "FAILED"
    assert "boom" in run.error
    report = await consistency_report(db_session, s.sid)
    assert report["run"]["status"] == "FAILED"
    assert len(report["findings"]) == 1


async def test_line_item_fields_carry_line_seq(db_session):
    from app.services.consistency import collect_fields

    s = await _seed(db_session)
    item = LineItem(document_id=s.pl.id, seq=3)
    db_session.add(item)
    await db_session.flush()
    await _field(db_session, s.pl, "line.quantity", "10", line_item_id=item.id)
    await db_session.commit()
    fields = await collect_fields(db_session, s.sid)
    assert {f.field_key: f.line_seq for f in fields}["line.quantity"] == 3


def _cf(i: int, doc: int, seq: int | None) -> CheckField:
    return CheckField(id=i, document_id=doc, filename="f", doc_type="PACKING_LIST", page=1, field_key=f"k{i}", value="v", line_seq=seq)


def test_build_batches_keeps_header_in_every_batch():
    header = [_cf(1, 1, None), _cf(2, 2, None)]
    lines = [_cf(10 + n * 2 + k, 1, n) for n in range(3) for k in range(2)]
    batches = build_batches(header + lines, max_fields=4)
    assert len(batches) == 3
    for batch in batches:
        assert {f.id for f in header} <= {f.id for f in batch}
        assert len(batch) <= 4
    assert sorted(f.id for b in batches for f in b if f.line_seq is not None) == sorted(f.id for f in lines)
