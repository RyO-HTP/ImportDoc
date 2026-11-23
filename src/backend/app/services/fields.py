from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label_vi: str
    kind: str  # text | date | decimal | currency | unit | int
    critical: bool = False
    enum: tuple[str, ...] | None = None


PARTY_ROLES = ("SELLER", "BUYER", "SHIPPER", "CONSIGNEE", "NOTIFY", "EXPORTER", "IMPORTER", "PRODUCER", "ISSUER", "INSPECTOR")

COMMON_FIELDS = (
    FieldSpec("document.doc_number", "Số chứng từ", "text", critical=True),
    FieldSpec("document.issue_date", "Ngày phát hành", "date"),
    FieldSpec("document.issuer", "Bên phát hành", "text"),
)

REFERENCE_FIELDS = (
    FieldSpec("reference.invoice_no", "Số hóa đơn tham chiếu", "text"),
    FieldSpec("reference.invoice_date", "Ngày hóa đơn tham chiếu", "date"),
    FieldSpec("reference.contract_no", "Số hợp đồng tham chiếu", "text"),
    FieldSpec("reference.origin_no", "Số C/O tham chiếu", "text"),
    FieldSpec("reference.po_no", "Số đơn đặt hàng", "text"),
)

TRANSPORT_FIELDS = (
    FieldSpec("transport.vessel", "Tàu", "text", critical=True),
    FieldSpec("transport.voyage", "Chuyến", "text"),
    FieldSpec("transport.master_waybill_no", "Số vận đơn chủ (MBL)", "text"),
    FieldSpec("transport.house_waybill_no", "Số vận đơn thứ cấp (HBL)", "text"),
    FieldSpec("transport.booking_no", "Số booking", "text"),
    FieldSpec("transport.loading_port", "Cảng xếp hàng", "text"),
    FieldSpec("transport.discharge_port", "Cảng dỡ hàng", "text"),
    FieldSpec("transport.receipt_place", "Nơi nhận hàng", "text"),
    FieldSpec("transport.delivery_place", "Nơi giao hàng", "text"),
    FieldSpec("transport.on_board_date", "Ngày xếp hàng lên tàu", "date"),
    FieldSpec("transport.actual_arrival_date", "Ngày đến thực tế", "date"),
    FieldSpec("transport.net_weight_unit", "Đơn vị net weight", "text"),
    FieldSpec("transport.incoterm_place", "Địa điểm theo Incoterms", "text"),
    FieldSpec("transport.waybill_no", "Số vận đơn", "text", critical=True),
    FieldSpec("transport.container_no", "Số container", "text", critical=True),
    FieldSpec("transport.container_count", "Số lượng container", "int"),
    FieldSpec("transport.seal_no", "Số seal", "text"),
    FieldSpec("transport.packages", "Số kiện", "int"),
    FieldSpec("transport.package_unit", "Đơn vị số kiện", "text"),
    FieldSpec("transport.gross_weight_kg", "Gross weight (kg)", "decimal", critical=True),
    FieldSpec("transport.gross_weight_unit", "Đơn vị gross weight", "text"),
    FieldSpec("transport.net_weight_kg", "Net weight (kg)", "decimal", critical=True),
    FieldSpec("transport.volume_cbm", "Thể tích (m³)", "decimal", critical=True),
    FieldSpec("transport.origin_country_code", "Mã nước xuất xứ", "text"),
    FieldSpec("transport.arrival_date", "Ngày hàng đến (ETA)", "date"),
    FieldSpec("transport.payment_method", "Phương thức thanh toán", "text"),
    FieldSpec("transport.invoice_terms", "Điều kiện giao hàng", "text"),
)

LINE_FIELDS = (
    FieldSpec("line.sku", "Mã mặt hàng", "text"),
    FieldSpec("line.seal_no", "Số seal của container ghi trên dòng", "text"),
    FieldSpec("line.price_base_quantity", "Số đơn vị tính cho đơn giá", "decimal"),
    FieldSpec("line.allowance_amount", "Giảm trừ dòng hàng", "decimal"),
    FieldSpec("line.charge_amount", "Phụ phí dòng hàng", "decimal"),
    FieldSpec("line.no", "STT", "int"),
    FieldSpec("line.description", "Mô tả hàng", "text"),
    FieldSpec("line.spec", "Quy cách", "text"),
    FieldSpec("line.components", "Quy cách thành phần", "text"),
    FieldSpec("line.hs_code", "Mã HS trên chứng từ", "text"),
    FieldSpec("line.origin_country_code", "Mã nước xuất xứ dòng hàng", "text"),
    FieldSpec("line.container_no", "Số container dòng hàng", "text"),
    FieldSpec("line.quantity", "Số lượng", "decimal", critical=True),
    FieldSpec("line.unit", "Đơn vị", "unit"),
    FieldSpec("line.unit_price", "Đơn giá", "decimal", critical=True),
    FieldSpec("line.amount", "Thành tiền", "decimal", critical=True),
    FieldSpec("line.currency", "Đồng tiền dòng hàng", "currency"),
)

PARTY_FIELDS = (
    FieldSpec("party.seller", "Bên bán", "text"),
    FieldSpec("party.buyer", "Bên mua", "text"),
    FieldSpec("party.shipper", "Người gửi hàng", "text"),
    FieldSpec("party.consignee", "Người nhận hàng", "text"),
    FieldSpec("party.notify", "Bên thông báo", "text"),
    FieldSpec("party.exporter", "Người xuất khẩu", "text"),
    FieldSpec("party.importer", "Người nhập khẩu", "text"),
    FieldSpec("party.producer", "Nhà sản xuất", "text"),
)

# Per-role party attributes needed by a new customs draft: a company name alone is
# not enough for the importer/exporter blocks of the TKN header.
PARTY_ATTRIBUTE_FIELDS = tuple(
    FieldSpec(f"party.{role.lower()}.{attr}", label, kind)
    for role, label in (
        ("SELLER", "Bên bán"),
        ("BUYER", "Bên mua"),
        ("SHIPPER", "Người gửi hàng"),
        ("CONSIGNEE", "Người nhận hàng"),
        ("NOTIFY", "Bên thông báo"),
        ("EXPORTER", "Người xuất khẩu"),
        ("IMPORTER", "Người nhập khẩu"),
        ("PRODUCER", "Nhà sản xuất"),
    )
    for attr, label, kind in (
        ("address", "Địa chỉ", "text"),
        ("tax_code", "Mã số thuế", "text"),
        ("phone", "Điện thoại", "text"),
        ("country_code", "Mã nước", "text"),
    )
)

TOTAL_FIELDS = (
    FieldSpec("totals.line_net_total", "Tổng thành tiền các dòng hàng", "decimal"),
    FieldSpec("totals.tax_exclusive_amount", "Tổng trước thuế", "decimal"),
    FieldSpec("totals.tax_inclusive_amount", "Tổng gồm thuế", "decimal"),
    FieldSpec("totals.tax_amount", "Tiền thuế trên chứng từ", "decimal"),
    FieldSpec("totals.allowance_amount", "Tổng giảm trừ cấp chứng từ", "decimal"),
    FieldSpec("totals.charge_amount", "Tổng phụ phí cấp chứng từ", "decimal"),
    FieldSpec("totals.total_amount", "Tổng trị giá", "decimal", critical=True),
    FieldSpec("totals.currency", "Đồng tiền", "currency", critical=True),
)

GOODS_IDENTITY_FIELDS = tuple(
    spec for spec in LINE_FIELDS
    if spec.key not in {"line.unit_price", "line.amount", "line.currency", "line.price_base_quantity",
                        "line.allowance_amount", "line.charge_amount"}
)
SHIPPING_FIELDS = COMMON_FIELDS + REFERENCE_FIELDS + TRANSPORT_FIELDS + PARTY_FIELDS + PARTY_ATTRIBUTE_FIELDS

EXTRACTABLE_FIELDS: dict[str, tuple[FieldSpec, ...]] = {
    "COMMERCIAL_INVOICE": SHIPPING_FIELDS + LINE_FIELDS + TOTAL_FIELDS,
    "PACKING_LIST": SHIPPING_FIELDS + LINE_FIELDS,
    "TRANSPORT_DOCUMENT": SHIPPING_FIELDS + GOODS_IDENTITY_FIELDS,
    "SALES_CONTRACT_PO": SHIPPING_FIELDS + LINE_FIELDS + TOTAL_FIELDS,
    "ARRIVAL_NOTICE": SHIPPING_FIELDS + GOODS_IDENTITY_FIELDS,
    "SERVICE_CONTRACT": COMMON_FIELDS + REFERENCE_FIELDS + PARTY_FIELDS + PARTY_ATTRIBUTE_FIELDS + TOTAL_FIELDS,
    "EXPORT_DECLARATION": SHIPPING_FIELDS + LINE_FIELDS + TOTAL_FIELDS,
    "CERTIFICATE_OF_ORIGIN": SHIPPING_FIELDS + GOODS_IDENTITY_FIELDS,
    "INSURANCE_POLICY": SHIPPING_FIELDS + TOTAL_FIELDS,
    "PHYTOSANITARY_CERTIFICATE": SHIPPING_FIELDS + GOODS_IDENTITY_FIELDS,
    "CUSTOMS_DECLARATION_SCAN": COMMON_FIELDS + PARTY_FIELDS + TOTAL_FIELDS,
    "OTHER": COMMON_FIELDS,
}

DOCUMENT_LEVEL_ONLY = {
    "CUSTOMS_DECLARATION_SCAN",
    "OTHER",
}

ALL_FIELD_SPECS: dict[str, FieldSpec] = {}
for specs in EXTRACTABLE_FIELDS.values():
    for spec in specs:
        ALL_FIELD_SPECS[spec.key] = spec

# Shipment-level aliases used by the template mapping. They resolve to a party field from a
# single preferred document and are critical: a company name must be reviewed before FINAL.
SHIPMENT_ALIASES: dict[str, str] = {
    "contract.seller": "party.seller",
    "contract.buyer": "party.buyer",
    "contract.shipper": "party.shipper",
    "contract.consignee": "party.consignee",
}

for alias, target in SHIPMENT_ALIASES.items():
    base = ALL_FIELD_SPECS[target]
    ALL_FIELD_SPECS[alias] = FieldSpec(alias, base.label_vi, base.kind, critical=True)


def resolve_alias(field_key: str) -> str:
    return SHIPMENT_ALIASES.get(field_key, field_key)


def _register_output_families() -> None:
    """Register every extra output family once, deterministically.

    Imported here (not by callers) so `fields_for()` and `is_critical()` are correct
    in the API, the worker and tests regardless of import order.
    """
    try:
        from . import tokhai_fields  # noqa: F401  (registers on import)
    except Exception:  # pragma: no cover - registration must never break the app
        import logging

        logging.getLogger("importdoc.fields").exception("cannot register Tờ khai fields")


def fields_for(doc_type: str) -> tuple[FieldSpec, ...]:
    return EXTRACTABLE_FIELDS.get(doc_type, COMMON_FIELDS)


def register_extra_fields(specs, doc_types: tuple[str, ...]) -> None:
    """Register field specs from another output family (e.g. Tờ khai) into the registry.

    Idempotent: re-registering the same key replaces the spec in place.
    """
    for spec in specs:
        ALL_FIELD_SPECS[spec.key] = spec
    for doc_type in doc_types:
        current = EXTRACTABLE_FIELDS.setdefault(doc_type, COMMON_FIELDS)
        known = {s.key for s in current}
        EXTRACTABLE_FIELDS[doc_type] = current + tuple(s for s in specs if s.key not in known)


def is_critical(field_key: str) -> bool:
    spec = ALL_FIELD_SPECS.get(field_key)
    return bool(spec and spec.critical)


_register_output_families()
