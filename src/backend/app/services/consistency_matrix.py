from __future__ import annotations

from dataclasses import dataclass

from .fields import ALL_FIELD_SPECS

INVOICE = "COMMERCIAL_INVOICE"
PACKING = "PACKING_LIST"
BILL = "TRANSPORT_DOCUMENT"
NOTICE = "ARRIVAL_NOTICE"
CONTRACT = "SALES_CONTRACT_PO"
ORIGIN = "CERTIFICATE_OF_ORIGIN"
EXPORT = "EXPORT_DECLARATION"
IMPORT = "CUSTOMS_DECLARATION_SCAN"
INSURANCE = "INSURANCE_POLICY"
PHYTO = "PHYTOSANITARY_CERTIFICATE"
SERVICE = "SERVICE_CONTRACT"

TRANSPORT = frozenset({BILL, NOTICE})
CUSTOMS = frozenset({IMPORT, EXPORT, "CUSTOMS_DECLARATION"})
PHYSICAL = TRANSPORT | CUSTOMS | {PACKING, INVOICE, ORIGIN, PHYTO}
COMMERCIAL = CUSTOMS | {INVOICE, CONTRACT}
GOODS = PHYSICAL | {CONTRACT}
SUPPORTED = GOODS | {INSURANCE, SERVICE}

# Only explicit, semantically equivalent aliases. In particular VGM, CIF customs
# value, country of dispatch, delivery place and port of discharge stay distinct.
CUSTOMS_KEYS = {
    "declaration.invoice_no": "invoice.identity",
    "declaration.invoice_date": "invoice.date",
    "declaration.bill_of_lading_no": "transport.waybill_no",
    "declaration.loading_port_code": "transport.loading_port",
    "declaration.discharge_port_code": "transport.discharge_port",
    "declaration.transport_vessel": "transport.vessel",
    "declaration.package_count": "transport.packages",
    "declaration.package_unit": "transport.package_unit",
    "declaration.container_count": "transport.container_count",
    "declaration.gross_weight": "transport.gross_weight_kg",
    "declaration.gross_weight_unit": "transport.gross_weight_unit",
    "declaration.invoice_terms": "transport.invoice_terms",
    "declaration.invoice_total": "totals.total_amount",
    "declaration.importer_name": "party.importer",
    "declaration.importer_address": "party.importer.address",
    "declaration.importer_tax_code": "party.importer.tax_code",
    "declaration.importer_phone": "party.importer.phone",
    "declaration.exporter_name": "party.exporter",
    "declaration.exporter_address": "party.exporter.address",
    "declaration.exporter_tax_code": "party.exporter.tax_code",
    "declaration.exporter_country_code": "party.exporter.country_code",
    "goods.description": "line.description",
    "goods.quantity_1": "line.quantity",
    "goods.quantity_1_unit": "line.unit",
    "goods.unit_price": "line.unit_price",
    "goods.unit_price_currency": "line.currency",
    "goods.invoice_amount": "line.amount",
    "goods.hs_code": "line.hs_code",
    "goods.origin_country_code": "line.origin_country_code",
}


@dataclass(frozen=True)
class Rule:
    field: str
    documents: frozenset[str]
    basis: str
    kind: str = "text"
    severity: str = "WARNING"
    expected_for: frozenset[str] = frozenset()

    @property
    def label(self) -> str:
        labels = {"invoice.identity": "Số hóa đơn", "invoice.date": "Ngày hóa đơn", "origin.identity": "Số C/O",
                  "contract.identity": "Số hợp đồng", "transport.containers": "Số container"}
        spec = ALL_FIELD_SPECS.get(self.field)
        return spec.label_vi if spec else labels.get(self.field, self.field)

    def describe(self) -> dict:
        return {
            "field_key": self.field, "label": self.label, "document_types": sorted(self.documents),
            "basis": self.basis, "mismatch_risk": self.severity, "expected_on": sorted(self.expected_for),
        }


HEADER_RULES = (
    Rule("invoice.identity", SUPPORTED, "Chỉ so số hóa đơn với trường tham chiếu hóa đơn rõ ràng.", "id", "ERROR",
         frozenset({INVOICE})),
    Rule("invoice.date", SUPPORTED, "So ngày phát hành hóa đơn với ngày hóa đơn tham chiếu cùng số; không so ngày phát hành các chứng từ khác loại.", "date"),
    Rule("contract.identity", SUPPORTED, "So số hợp đồng gốc với tham chiếu hợp đồng; hợp đồng khung có thể bao nhiều đợt.", "id"),
    Rule("origin.identity", GOODS, "So số riêng của C/O với số C/O được tham chiếu rõ; có thể có nhiều C/O trong một lô.", "id"),
    Rule("document.issue_date", SUPPORTED, "Chỉ so ngày phát hành khi cùng loại và cùng số của chính chứng từ. Bản sửa đổi có thể đổi ngày.", "instrument_date"),
    Rule("reference.po_no", SUPPORTED, "So số PO cùng phạm vi; một PO có thể giao làm nhiều đợt.", "id"),
    Rule("transport.waybill_no", PHYSICAL | {INSURANCE}, "So vận đơn cùng cấp; số khác nhau có thể là MBL/HBL hoặc nhiều vận đơn.", "waybill"),
    Rule("transport.master_waybill_no", PHYSICAL | {INSURANCE}, "Đối chiếu MBL với MBL; kiểm tra trong cùng chặng hoặc booking.", "waybill"),
    Rule("transport.house_waybill_no", PHYSICAL | {INSURANCE}, "Đối chiếu HBL với HBL; một MBL có thể chứa nhiều HBL.", "waybill"),
    Rule("transport.booking_no", PHYSICAL | {INSURANCE}, "Booking so với booking; có thể tách booking hoặc đổi chặng.", "waybill"),
    Rule("transport.containers", PHYSICAL | {INSURANCE}, "So tập số container theo phạm vi. Danh sách con, nhiều container hoặc nhiều vận đơn cần xét chứng từ liên kết.", "containers"),
    Rule("transport.container_count", PHYSICAL | {INSURANCE}, "So số container khi hai danh sách đã xác định có cùng phạm vi.", "number"),
    Rule("transport.seal_no", PHYSICAL | {INSURANCE}, "So seal của cùng container; đổi seal hoặc nhiều seal cần xác minh.", "seal"),
    Rule("transport.loading_port", PHYSICAL | {INSURANCE}, "So cảng xếp cùng chặng, giữ riêng nơi nhận hàng và cảng chuyển tải.", "route"),
    Rule("transport.discharge_port", PHYSICAL | {INSURANCE}, "So cảng dỡ cùng chặng, giữ riêng nơi giao cuối.", "route"),
    Rule("transport.receipt_place", TRANSPORT | {INSURANCE}, "So nơi nhận của cùng chặng vận tải.", "route"),
    Rule("transport.delivery_place", TRANSPORT | {INSURANCE}, "So nơi giao của cùng chặng vận tải.", "route"),
    Rule("transport.vessel", PHYSICAL | {INSURANCE}, "Tàu feeder, tàu mẹ hoặc thay tàu có thể hợp lệ.", "route"),
    Rule("transport.voyage", PHYSICAL | {INSURANCE}, "So số chuyến của cùng chặng và tàu.", "route"),
    Rule("transport.on_board_date", TRANSPORT | {ORIGIN, INSURANCE}, "So ngày on-board của cùng chặng, tách ngày phát hành và ETD.", "scheduled"),
    Rule("transport.arrival_date", TRANSPORT, "ETA có thể cập nhật giữa các lần phát hành.", "scheduled"),
    Rule("transport.actual_arrival_date", TRANSPORT, "So ngày đến thực tế của cùng chặng, giữ riêng ETA.", "scheduled"),
    Rule("transport.gross_weight_kg", PHYSICAL, "So gross của hàng, chỉ sau khi đổi đơn vị ghi trên nguồn và xác nhận cùng phạm vi; không so với VGM.", "mass", "ERROR"),
    Rule("transport.net_weight_kg", PHYSICAL, "So net của hàng, giữ riêng gross.", "mass", "ERROR"),
    Rule("transport.volume_cbm", PHYSICAL, "So thể tích hàng cùng phạm vi, có dung sai làm tròn.", "volume"),
    Rule("transport.packages", PHYSICAL, "So số kiện cùng đơn vị và cấp đóng gói, không quy đổi pallet sang carton.", "packages", "ERROR"),
    Rule("transport.package_unit", PHYSICAL, "So cấp đóng gói in trên chứng từ, ví dụ carton và pallet có thể cùng xuất hiện.", "unit"),
    Rule("transport.invoice_terms", COMMERCIAL, "So mã Incoterms của giao dịch thương mại; không so điều khoản cước trên vận đơn.", "incoterm"),
    Rule("transport.incoterm_place", COMMERCIAL, "So địa điểm được nêu kèm cùng mã Incoterms.", "named_place"),
    Rule("transport.payment_method", frozenset({INVOICE, CONTRACT}), "So phương thức thanh toán của giao dịch; phụ lục có thể điều chỉnh.", "commercial"),
    Rule("totals.currency", COMMERCIAL, "So tiền tệ hóa đơn hoặc hợp đồng, giữ riêng đồng tiền khai trị giá hải quan.", "currency", "ERROR",
         frozenset({INVOICE})),
    Rule("totals.total_amount", CUSTOMS | {INVOICE}, "So trị giá hóa đơn cùng số hóa đơn và đồng tiền, không so tổng hợp đồng hoặc trị giá tính thuế.", "money", "ERROR",
         frozenset({INVOICE})),
    Rule("transport.origin_country_code", GOODS, "So xuất xứ của hàng cùng phạm vi, không suy từ cảng xếp hoặc nơi xuất hàng.", "origin", "ERROR"),
    *(
        Rule(f"party.{role}", kinds, basis, "party")
        for role, kinds, basis in (
            ("seller", frozenset({INVOICE, CONTRACT, PACKING, ORIGIN, EXPORT}), "So bên bán ghi trên chứng từ; có thể có hóa đơn bên thứ ba."),
            ("buyer", frozenset({INVOICE, CONTRACT, PACKING, ORIGIN, EXPORT}), "So bên mua; giữ riêng consignee và đơn vị nhập khẩu ủy thác."),
            ("shipper", PHYSICAL | {INSURANCE}, "So shipper cùng vai trò và cấp vận đơn; forwarder có thể đứng tên trên MBL."),
            ("consignee", PHYSICAL | {INSURANCE}, "So consignee cùng vai trò và cấp vận đơn; To Order và ngân hàng cần xác minh."),
            ("notify", TRANSPORT, "So notify party cùng vận đơn; có thể có nhiều bên nhận thông báo."),
            ("exporter", CUSTOMS | {ORIGIN, PHYTO, INVOICE, CONTRACT}, "So người xuất khẩu được ghi rõ, giữ riêng bên bán, shipper và nhà sản xuất."),
            ("importer", CUSTOMS | {ORIGIN, INVOICE, CONTRACT, NOTICE}, "So người nhập khẩu được ghi rõ, giữ riêng bên mua, consignee và bên ủy thác."),
            ("producer", frozenset({ORIGIN, PHYTO, INVOICE, CONTRACT}), "So cùng nhà sản xuất, giữ riêng người phát hành và người xuất khẩu."),
        )
    ),
)

PARTY_RULES = tuple(
    Rule(f"{rule.field}.{attr}", rule.documents,
         "So thông tin của cùng pháp nhân và cùng vai trò. Địa chỉ, điện thoại và mã định danh có thể dùng theo chi nhánh.", "party_attribute")
    for rule in HEADER_RULES if rule.kind == "party"
    for attr in ("address", "tax_code", "phone", "country_code")
)

LINE_RULES = (
    Rule("line.sku", GOODS, "So mã hàng khi hai dòng đã được nhận diện bằng mô tả hoặc quy cách và không mơ hồ.", "id"),
    Rule("line.description", GOODS, "Ghép dòng bằng SKU hoặc mô tả/quy cách duy nhất. Mô tả rút gọn hay dịch thuật cần xác minh.", "description"),
    Rule("line.spec", GOODS, "So quy cách của cùng mặt hàng; ký hiệu, quy đổi và cách mô tả cần đọc nguồn.", "description"),
    Rule("line.components", GOODS, "So quy cách thành phần của cùng mặt hàng.", "description"),
    Rule("line.quantity", GOODS, "So lượng cùng mặt hàng, đơn vị và phạm vi. Đơn hàng, lô giao và dòng khai báo có thể tách khác nhau.", "line_quantity", "ERROR"),
    Rule("line.unit", GOODS, "So đơn vị trên cùng mặt hàng; quy đổi khối lượng chỉ khi có đơn vị xác định.", "unit"),
    Rule("line.unit_price", COMMERCIAL, "So đơn giá cùng tiền tệ, đơn vị và cơ sở tính giá.", "price", "ERROR"),
    Rule("line.price_base_quantity", COMMERCIAL, "So cơ sở đơn giá; 100 đơn vị và 1 đơn vị có thể biểu thị cùng giá quy đổi.", "price_basis"),
    Rule("line.amount", CUSTOMS | {INVOICE}, "So thành tiền của cùng hóa đơn, mặt hàng và lượng.", "money", "ERROR"),
    Rule("line.currency", COMMERCIAL, "Đồng tiền dòng hàng hoặc đồng tiền hóa đơn áp dụng cho dòng.", "currency", "ERROR"),
    Rule("line.hs_code", GOODS, "So 6 số HS quốc tế; phần sau có thể là phân nhóm quốc gia.", "hs", "ERROR"),
    Rule("line.origin_country_code", GOODS, "So xuất xứ in trên cùng mặt hàng; ưu tiên xuất xứ theo dòng khi tài liệu có nhiều nước.", "origin", "ERROR"),
    Rule("line.seal_no", PHYSICAL, "So seal trên dòng cùng container và cùng chặng.", "seal"),
)

INTERNAL_RULES = (
    ("gross_net", "Gross ≥ net", "Cùng phạm vi trong một chứng từ, sau khi đổi đơn vị."),
    ("container_count", "Số container khai báo", "Chỉ xác nhận khớp khi số khai báo bằng số mã đọc được; danh sách thiếu cần xác minh."),
    ("transport_dates", "Trình tự vận tải", "Ngày on-board không sau ngày đến thực tế cùng chuyến; ETA có thể thay đổi."),
    ("invoice_line", "Số lượng × đơn giá", "Tính theo cơ sở đơn giá và các khoản giảm trừ/phụ phí ghi rõ."),
    ("invoice_subtotal", "Tổng thành tiền dòng hàng", "Cộng thành tiền các dòng đọc được, cùng tiền tệ; loại phần tổng và dòng bị lặp."),
    ("invoice_exclusive", "Tổng trước thuế", "Tổng dòng trừ giảm trừ cộng phụ phí cấp chứng từ khi đủ thành phần."),
    ("invoice_inclusive", "Tổng gồm thuế", "Tổng trước thuế cộng thuế được ghi rõ trên hóa đơn."),
    ("partition.packages", "Tổng số kiện theo các phần của lô", "Chỉ cộng các chứng từ cùng loại, cùng hóa đơn và cùng đơn vị khi danh sách container không trùng và phủ đủ lô."),
    ("partition.gross_weight", "Tổng gross theo các phần của lô", "Cộng gross theo các nhóm container đã xác định, sau khi đổi đơn vị in trên nguồn."),
)


def public_matrix() -> list[dict]:
    return [rule.describe() for rule in (*HEADER_RULES, *PARTY_RULES, *LINE_RULES)] + [
        {"field_key": key, "label": label, "basis": basis, "document_types": [],
         "mismatch_risk": "ERROR", "expected_on": []}
        for key, label, basis in INTERNAL_RULES
    ]
