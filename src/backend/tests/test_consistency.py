from __future__ import annotations

from types import SimpleNamespace

import pytest
import pymupdf

from app.models import Document, DocumentSection, ExtractedField, Shipment
from app.services.consistency import collect_sources, consistency_report, dismiss_finding, run_consistency_check
from app.services.consistency_groups import Evidence, Source, group_sources
from app.services.consistency_matrix import BILL, CONTRACT, INVOICE, NOTICE, ORIGIN, PACKING, public_matrix
from app.services.consistency_rules import MATCH, MISMATCH, MISSING, VERIFY, evaluate
from app.services.duplicates import detect_duplicates
from app.services.extract import ExtractionService
from app.services.fields import fields_for
from app.services.schemas import ExtractionOut


def _source(doc_id, doc_type, fields, *, untrusted=(), lines=()):
    source = Source(doc_id, f"file-{doc_id}.pdf", doc_type, first_page=1)
    entries = [(key, value, None) for key, value in fields.items()]
    entries += [(key, value, index) for index, line in enumerate(lines) for key, value in line.items()]
    for index, (key, value, line) in enumerate(entries, 1):
        source.add(Evidence(doc_id * 100 + index, key, value, value, page=2,
                            grounded=key not in untrusted, line=line))
    return source


def _transport(waybill, container, **more):
    return {"transport.waybill_no": waybill, "transport.container_no": container, **more}


def _checks(sources, rule):
    return [result for result in evaluate(sources)[1] if result.rule == rule]


def test_cross_check_applies_reference_roles_and_checks_weights_units_and_net():
    bl = _source(1, BILL, _transport("BL00012345", "MSCU1234567", **{
        "transport.gross_weight_kg": "1200", "transport.gross_weight_unit": "KG",
        "transport.net_weight_kg": "1300", "transport.net_weight_unit": "KG",
        "transport.loading_port": "VNSGN", "transport.discharge_port": "USLAX",
        "transport.package_unit": "CTN", "transport.packages": "10",
        "document.issue_date": "2026-03-16",
    }))
    notice = _source(2, NOTICE, _transport("BL-00012345", "MSCU 1234567", **{
        "transport.gross_weight_kg": "1.2", "transport.gross_weight_unit": "MT",
        "transport.loading_port": "VNSGN", "transport.discharge_port": "USLAX",
        "transport.package_unit": "PLT", "transport.packages": "2",
        "document.issue_date": "2026-03-23",
    }))
    assert _checks([bl, notice], "transport.gross_weight_kg")[0].status == MATCH
    assert _checks([bl, notice], "transport.packages")[0].status == VERIFY
    assert _checks([bl, notice], "gross_net")[0].severity == "ERROR"
    assert not _checks([bl, notice], "document.issue_date")


@pytest.mark.parametrize("value,trusted,expected", [(None, False, MISSING), ("1500", False, VERIFY), ("1500", True, MISMATCH)])
def test_unreadable_unverified_and_verified_values_have_different_outcomes(value, trusted, expected):
    a = _source(1, BILL, {"transport.waybill_no": "BL00012345", "transport.gross_weight_kg": "1200", "transport.gross_weight_unit": "KG"})
    b = _source(2, NOTICE, {"transport.waybill_no": "BL00012345", "transport.gross_weight_kg": value, "transport.gross_weight_unit": "KG"},
                untrusted=() if trusted else ("transport.gross_weight_kg",))
    check, = _checks([a, b], "transport.gross_weight_kg")
    assert check.status == expected
    assert (check.severity == "ERROR") == (expected == MISMATCH)


def test_distinct_bills_and_boxes_without_other_evidence_remain_uncertain():
    a = _source(1, NOTICE, _transport("BL00012345", "MSCU1234567"))
    b = _source(2, NOTICE, _transport("BL00067890", "TLLU7654321"))
    groups, results = evaluate([a, b])
    assert len(groups) == 2
    assert [result.status for result in results] == [VERIFY]
    assert results[0].severity == "INFO"


def test_multiple_bills_or_containers_in_one_invoice_group_do_not_create_mismatch():
    shared = {"reference.invoice_no": "INV455", "party.consignee": "BUYER CO LTD"}
    a = _source(1, NOTICE, _transport("BL00012345", "MSCU1234567", **shared))
    b = _source(2, NOTICE, _transport("BL00067890", "TLLU7654321", **shared))
    c = _source(3, PACKING, {**shared, "transport.container_no": "MSCU1234567, TLLU7654321"})
    groups, results = evaluate([a, b, c])
    assert len(groups) == 1
    assert all(result.status != MISMATCH for result in results)
    assert all(result.rule != "shipment.identity" for result in results)
    assert any("tập con" in result.reason for result in results)


def test_split_consignment_aggregates_only_disjoint_complete_container_lists():
    invoice = _source(1, INVOICE, {
        "document.doc_number": "INV455", "transport.container_no": "MSCU1234567, TLLU7654321",
        "transport.package_unit": "CARTONS", "transport.packages": "30",
        "transport.gross_weight_kg": "2", "transport.gross_weight_unit": "MT",
    })
    a = _source(2, PACKING, {
        "reference.invoice_no": "INV455", "transport.container_no": "MSCU1234567",
        "transport.package_unit": "CTN", "transport.packages": "10",
        "transport.gross_weight_kg": "500", "transport.gross_weight_unit": "KG",
    })
    b = _source(3, PACKING, {
        "reference.invoice_no": "INV455", "transport.container_no": "TLLU7654321",
        "transport.package_unit": "CTN", "transport.packages": "20",
        "transport.gross_weight_kg": "1600", "transport.gross_weight_unit": "KG",
    })
    assert not _checks([invoice, a], "partition.packages")
    assert _checks([invoice, a, b], "partition.packages")[0].status == MATCH
    mismatch, = _checks([invoice, a, b], "partition.gross_weight")
    assert mismatch.status == MISMATCH and mismatch.calculated_value == "2100"
    assert {source.document_id for source in mismatch.sources} == {1, 2, 3}


def test_distinct_commercial_and_route_evidence_prevents_cross_lot_comparison():
    groups_data = [
        ("INV100", "BL00012345", "MSCU1234567", "VNSGN", "USLAX", "CONTRACT55"),
        ("INV200", "BL00067890", "TLLU7654321", "CNSHA", "VNHPH", "CONTRACT55"),
    ]
    sources = []
    for i, (invoice, bill, container, loading, discharge, contract) in enumerate(groups_data):
        key = i * 3
        routes = {"transport.loading_port": loading, "transport.discharge_port": discharge}
        sources += [
            _source(key + 1, INVOICE, {"document.doc_number": invoice, "reference.contract_no": contract}),
            _source(key + 2, BILL, _transport(bill, container, **routes, **{"reference.invoice_no": invoice})),
            _source(key + 3, NOTICE, _transport(bill, container, **routes)),
        ]
    sources.append(_source(7, CONTRACT, {"document.doc_number": "CONTRACT55"}))
    groups, results = evaluate(sources)
    assert [len(g["documents"]) for g in groups] == [3, 3, 1]
    assert not any(r.group_id and {s.document_id for s in r.sources} & {1, 2, 3}
                   and {s.document_id for s in r.sources} & {4, 5, 6} for r in results)
    assert any(r.rule == "shipment.identity" and r.status == MATCH for r in results)


def test_shared_master_container_does_not_merge_distinct_house_and_invoice_scopes():
    data = {"transport.master_waybill_no": "MASTER345", "transport.waybill_no": "MASTER345",
            "transport.container_no": "MSCU1234567"}
    a = _source(1, BILL, {**data, "transport.house_waybill_no": "HOUSE100", "reference.invoice_no": "INV100"})
    b = _source(2, BILL, {**data, "transport.house_waybill_no": "HOUSE200", "reference.invoice_no": "INV200"})
    assert len(group_sources([a, b])[0]) == 2


def test_line_matching_follows_sku_not_row_position_and_compares_hs_origin_and_quantity():
    base = {"line.sku": "SKU-100", "line.description": "OAK WOOD PANEL", "line.unit": "PCS", "line.quantity": "10"}
    other = {"line.sku": "SKU-200", "line.description": "WOODEN CHAIR", "line.unit": "PCS", "line.quantity": "3"}
    invoice = _source(1, INVOICE, {"document.doc_number": "INV100"}, lines=[
        {**base, "line.hs_code": "4412.33.00", "line.origin_country_code": "VN"}, other,
    ])
    origin = _source(2, ORIGIN, {"reference.invoice_no": "INV100"}, lines=[
        other, {**base, "line.hs_code": "441233", "line.origin_country_code": "CN", "line.quantity": "11"},
    ])
    checks = evaluate([invoice, origin])[1]
    assert next(c for c in checks if c.rule == "line.hs_code").status == MATCH
    assert next(c for c in checks if c.rule == "line.origin_country_code").status == MISMATCH
    quantities = [c for c in checks if c.rule == "line.quantity"]
    assert {c.status for c in quantities} == {MATCH, MISMATCH}


def test_duplicate_skus_and_unrelated_descriptions_are_not_paired_by_row_number():
    a = _source(1, INVOICE, {"document.doc_number": "INV100"}, lines=[
        {"line.sku": "A100", "line.description": "OAK WOOD", "line.quantity": "10"},
        {"line.sku": "A100", "line.description": "OAK WOOD", "line.quantity": "20"},
    ])
    b = _source(2, PACKING, {"reference.invoice_no": "INV100"}, lines=[
        {"line.sku": "A100", "line.description": "OAK WOOD", "line.quantity": "30"},
    ])
    assert not _checks([a, b], "line.quantity")
    assert _checks([a, b], "line.identity")[0].status == VERIFY


def test_invoice_arithmetic_checks_quantity_price_base_discount_and_tax():
    invoice = _source(1, INVOICE, {
        "totals.currency": "USD", "totals.line_net_total": "22", "totals.allowance_amount": "2",
        "totals.charge_amount": "0", "totals.tax_exclusive_amount": "20",
        "totals.tax_amount": "2", "totals.tax_inclusive_amount": "24",
    }, lines=[
        {"line.quantity": "20", "line.unit_price": "120", "line.price_base_quantity": "100",
         "line.allowance_amount": "2", "line.charge_amount": "0", "line.amount": "22"},
    ])
    checks = {c.rule: c for c in evaluate([invoice])[1]}
    assert checks["invoice_line"].status == MATCH
    assert checks["invoice_subtotal"].status == MATCH
    assert checks["invoice_exclusive"].status == MATCH
    assert checks["invoice_inclusive"].status == MISMATCH
    assert checks["invoice_inclusive"].calculated_value == "22"


def test_unlisted_adjustments_make_price_arithmetic_a_check_for_review():
    invoice = _source(1, INVOICE, {"totals.currency": "USD"}, lines=[
        {"line.quantity": "20", "line.unit_price": "12", "line.amount": "22"},
    ])
    check, = _checks([invoice], "invoice_line")
    assert check.status == VERIFY
    assert check.calculated_value == "240"


def test_shipping_dates_and_seal_require_correct_context():
    a = _source(1, BILL, _transport("BL10000", "MSCU1234567", **{
        "transport.seal_no": "SEAL001", "transport.on_board_date": "2026-03-16",
        "transport.actual_arrival_date": "2026-03-14",
    }))
    b = _source(2, NOTICE, _transport("BL10000", "MSCU1234567", **{
        "transport.seal_no": "SEAL002", "transport.arrival_date": "2026-03-18",
    }))
    assert _checks([a, b], "transport_dates")[0].status == MISMATCH
    assert _checks([a, b], "transport.seal_no")[0].status == VERIFY
    assert _checks([a, b], "transport.arrival_date")[0].status == MISSING


def test_extraction_accepts_shipping_references_and_validates_ocr_boxes():
    result = ExtractionOut.model_validate({
        "loading_port": {"value": "VNSGN", "bbox": [0.1, 0.2, 0.3, 0.4]},
        "discharge_port": {"value": "USLAX", "bbox": [0.8, 0, 0.1, 1]},
        "invoice_ref_no": "INV455",
        "parties": [{"role": "notify", "name": {"value": "Notify Corp", "bbox": [0, 0, 1, 1]}}],
        "line_items": [{"sku": "SKU100", "hs_code": "441233"}],
    })
    assert result.loading_port.bbox == [0.1, 0.2, 0.3, 0.4]
    assert result.discharge_port.bbox is None
    assert result.invoice_ref_no.value == "INV455"
    assert result.parties[0].bbox == [0, 0, 1, 1]
    assert result.line_items[0].sku.value == "SKU100"
    assert len({r["field_key"] for r in public_matrix()}) == len(public_matrix())


def test_text_evidence_uses_the_page_that_contains_the_value_not_only_a_matching_quote(tmp_path):
    path = tmp_path / "two-pages.pdf"
    page_text = ["Cargo description", "Cargo description\nInvoice reference INV426"]
    with pymupdf.open() as pdf:
        for text in page_text:
            pdf.new_page().insert_text((40, 50), text)
        pdf.save(path)
    result = ExtractionOut.model_validate({
        "invoice_ref_no": {"value": "INV426", "source_quote": "Cargo description"},
        "loading_port": {"value": "NOTPRINTED", "source_quote": "Invoice reference INV426"},
    })
    rows = ExtractionService(None)._rows_from_result(
        fields_for(BILL), result, "TEXT_LAYER", page_text, None, path, BILL, text_indexes=[0, 1],
    )
    invoice = next(row for row in rows if row.field_key == "reference.invoice_no")
    port = next(row for row in rows if row.field_key == "transport.loading_port")
    assert invoice.page == 2 and invoice.bbox and invoice.grounding_verified
    assert port.page == 2 and not port.grounding_verified


async def _doc(session, shipment_id, doc_type, filename, **kwargs):
    doc = Document(shipment_id=shipment_id, filename=filename, file_ext=".pdf", storage_path=f"/tmp/{filename}",
                   checksum=filename, doc_type=doc_type, **{"status": "EXTRACTED", **kwargs})
    session.add(doc)
    await session.flush()
    return doc


async def _field(session, doc, key, value, **kwargs):
    field = ExtractedField(document_id=doc.id, field_key=key, raw_value=value, normalized_value=value,
                           **{"page": 2, "grounding_verified": True, **kwargs})
    session.add(field)
    await session.flush()
    return field


async def _seed(session):
    shipment = Shipment(code="LS1")
    session.add(shipment)
    await session.flush()
    pl = await _doc(session, shipment.id, PACKING, "packing-list.pdf")
    bill = await _doc(session, shipment.id, BILL, "bill.pdf")
    for doc in (pl, bill):
        await _field(session, doc, "transport.waybill_no", "BILL00001")
        await _field(session, doc, "transport.gross_weight_unit", "KG")
    a = await _field(session, pl, "transport.gross_weight_kg", "26400", bbox=[0.1, 0.2, 0.3, 0.4])
    b = await _field(session, bill, "transport.gross_weight_kg", "26040")
    await session.commit()
    return SimpleNamespace(sid=shipment.id, pl=pl, bill=bill, a=a, b=b)


async def test_results_store_provenance_and_rerun_when_grounding_or_values_change(db_session):
    seed = await _seed(db_session)
    run = await run_consistency_check(db_session, None, seed.sid)
    report = await consistency_report(db_session, seed.sid)
    assert report["counts"]["error"] == 1
    assert {r["status"] for r in report["findings"]} >= {MATCH, MISMATCH}
    error = next(r for r in report["findings"] if r["status"] == MISMATCH)
    assert [s["filename"] for s in error["sources"]] == ["packing-list.pdf", "bill.pdf"]
    assert error["values"][0]["page"] == 2
    assert error["values"][0]["bbox"] == [0.1, 0.2, 0.3, 0.4]
    assert (await run_consistency_check(db_session, None, seed.sid)).id == run.id
    seed.b.grounding_verified = False
    await db_session.commit()
    assert (await consistency_report(db_session, seed.sid))["stale"]
    assert (await run_consistency_check(db_session, None, seed.sid)).id != run.id
    assert (await consistency_report(db_session, seed.sid))["counts"] == {"error": 0, "warning": 1}


async def test_dismissal_persists_after_identical_reextraction_but_reopens_on_changed_value(db_session):
    from app.models import ConsistencyFinding
    from sqlalchemy import select

    seed = await _seed(db_session)
    await run_consistency_check(db_session, None, seed.sid)
    finding = (await db_session.execute(select(ConsistencyFinding).where(ConsistencyFinding.severity == "ERROR"))).scalar_one()
    await dismiss_finding(db_session, finding, "đã đối chiếu")
    await db_session.delete(seed.a)
    await db_session.flush()
    a = await _field(db_session, seed.pl, "transport.gross_weight_kg", "26400")
    await db_session.commit()
    await run_consistency_check(db_session, None, seed.sid)
    report = await consistency_report(db_session, seed.sid)
    assert next(f for f in report["findings"] if f["severity"] == "ERROR")["dismissed"]
    a.raw_value = a.normalized_value = "27000"
    await db_session.commit()
    await run_consistency_check(db_session, None, seed.sid)
    assert (await consistency_report(db_session, seed.sid))["counts"]["error"] == 1


async def test_older_duplicate_marker_cannot_hide_distinct_container_scopes(db_session):
    seed = await _seed(db_session)
    a = await _doc(db_session, seed.sid, NOTICE, "notice-a.pdf")
    b = await _doc(db_session, seed.sid, NOTICE, "notice-b.pdf")
    for doc, container in ((a, "MSCU1234567"), (b, "TLLU7654321")):
        await _field(db_session, doc, "transport.waybill_no", "BILL00001")
        await _field(db_session, doc, "transport.container_no", container)
    await detect_duplicates(db_session, b)
    assert a.status == b.status == "EXTRACTED"
    a.duplicate_of_id, a.status = b.id, "IS_DUPLICATE_OF"
    await db_session.commit()
    sources = await collect_sources(db_session, seed.sid)
    assert {a.id, b.id} <= {source.document_id for source in sources}


async def test_bundle_uses_section_types_and_pages_without_mixing_their_fields(db_session):
    shipment = Shipment(code="BUNDLE")
    db_session.add(shipment)
    await db_session.flush()
    doc = await _doc(db_session, shipment.id, "OTHER", "bundle.pdf")
    invoice = DocumentSection(document_id=doc.id, section_type=INVOICE, page_start=0, page_end=0)
    origin = DocumentSection(document_id=doc.id, section_type=ORIGIN, page_start=1, page_end=1)
    db_session.add_all([invoice, origin])
    await db_session.flush()
    await _field(db_session, doc, "document.doc_number", "INV99", section_id=invoice.id, page=1)
    await _field(db_session, doc, "reference.invoice_no", "INV99", section_id=origin.id, page=2)
    await db_session.commit()
    await run_consistency_check(db_session, None, shipment.id)
    report = await consistency_report(db_session, shipment.id)
    finding = next(f for f in report["findings"] if f["field_key"] == "invoice.identity")
    assert finding["status"] == MATCH
    assert [v["page"] for v in finding["values"]] == [1, 2]
    assert {v["source_id"] for v in finding["values"]} == {f"{doc.id}:{invoice.id}", f"{doc.id}:{origin.id}"}
