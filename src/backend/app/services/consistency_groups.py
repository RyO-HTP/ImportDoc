from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from .consistency_matrix import CONTRACT, CUSTOMS, CUSTOMS_KEYS, INVOICE, ORIGIN, PACKING, TRANSPORT

BILL_KEYS = ("transport.waybill_no", "transport.master_waybill_no", "transport.house_waybill_no")
PARTY_KEYS = ("party.seller", "party.buyer", "party.shipper", "party.consignee", "party.exporter", "party.importer")
ROUTE_KEYS = ("transport.loading_port", "transport.discharge_port")
CONTAINER_KEYS = ("transport.container_no", "line.container_no")
CONTAINER_PATTERN = re.compile(r"(?<![A-Z0-9])([A-Z]{4})[\s-]*([0-9]{7})(?![A-Z0-9])")
PLACEHOLDERS = {"", "-", "—", "N/A", "NA", "NONE", "NULL", "UNKNOWN", "KHÔNG RÕ", "KHÔNG ĐỌC ĐƯỢC"}
PORT_COUNTRIES = set("AE AU BE CA CN DE ES FR GB HK ID IN IT JP KR MY NL NZ SA SG TH TR TW US VN ZA".split())


def folded(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().upper()


def words(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", folded(value)))


def identifier(value: str) -> str:
    return re.sub(r"[\s./_-]+", "", folded(value))


def identifiers(value: str) -> set[str]:
    return {key for part in re.split(r"[,;\n|]+", value) if len(key := identifier(part)) >= 3 and key.isalnum()}


def company(value: str) -> str:
    name = words(value)
    name = re.sub(r"\b(?:COMPANY LIMITED|CO LIMITED|COMPANY LTD)\b", "CO LTD", name)
    return re.sub(r"\bINCORPORATED\b", "INC", name)


def container_numbers(value: str) -> set[str]:
    return {owner + serial for owner, serial in CONTAINER_PATTERN.findall(folded(value))}


def port_code(value: str) -> str | None:
    code = identifier(value)
    if code[:2] in PORT_COUNTRIES and re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}", code) and folded(value) == code:
        return code
    return None


@dataclass(frozen=True)
class Evidence:
    id: int
    key: str
    raw: str | None
    normalized: str | None
    page: int | None = None
    bbox: list[float] | None = None
    cell_ref: str | None = None
    grounded: bool = False
    review_status: str = "UNREVIEWED"
    provenance: str = "TEXT_LAYER"
    unit: str | None = None
    line: int | None = None

    @property
    def value(self) -> str | None:
        value = self.normalized if self.review_status in {"EDITED", "CONFIRMED"} else (self.raw or self.normalized)
        if value and folded(value) not in PLACEHOLDERS:
            return value.strip()
        return None

    @property
    def trusted(self) -> bool:
        return self.grounded or self.review_status in {"EDITED", "CONFIRMED"}


@dataclass
class Source:
    document_id: int
    filename: str
    doc_type: str
    section_id: int | None = None
    first_page: int | None = None
    status: str = "EXTRACTED"
    fields: dict[str, list[Evidence]] = field(default_factory=lambda: defaultdict(list))
    lines: dict[int, dict[str, list[Evidence]]] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.document_id}:{self.section_id or 0}"

    def add(self, evidence: Evidence) -> None:
        key = CUSTOMS_KEYS.get(evidence.key, evidence.key) if self.doc_type in CUSTOMS else evidence.key
        if self.doc_type == INVOICE:
            key = {"document.doc_number": "invoice.identity", "document.issue_date": "invoice.date"}.get(key, key)
        if self.doc_type != INVOICE:
            key = {"reference.invoice_no": "invoice.identity", "reference.invoice_date": "invoice.date"}.get(key, key)
        if self.doc_type == CONTRACT and key == "document.doc_number":
            key = "contract.identity"
        if key == "reference.contract_no":
            key = "contract.identity"
        if key == "reference.origin_no" or (self.doc_type == ORIGIN and key == "document.doc_number"):
            key = "origin.identity"
        if key.startswith("line.") and evidence.line is not None:
            self.lines.setdefault(evidence.line, defaultdict(list))[key].append(evidence)
        else:
            self.fields[key].append(evidence)

    def evidence(self, key: str) -> list[Evidence]:
        if key == "transport.containers":
            return [
                *self.fields.get("transport.container_no", ()),
                *(e for row in self.lines.values() for e in row.get("line.container_no", ())),
            ]
        return self.fields.get(key, [])

    def values(self, key: str, canonical=words, *, trusted=False) -> set:
        values = set()
        observed = [e for e in self.evidence(key) if e.value is not None]
        verified = [e for e in observed if e.trusted]
        selected = verified if trusted or (verified and key != "transport.containers") else observed
        for e in selected:
            converted = canonical(e.value)
            values.update(converted if isinstance(converted, set) else {converted} if converted else ())
        return values

    def as_dict(self) -> dict:
        return {"id": self.key, "document_id": self.document_id, "section_id": self.section_id,
                "filename": self.filename, "doc_type": self.doc_type, "page": self.first_page, "status": self.status}


@dataclass(frozen=True)
class Link:
    left: str
    right: str
    strength: int
    signals: tuple[str, ...]
    scope: str


def _share(a: Source, b: Source, key: str, canonical=words) -> bool:
    return bool(a.values(key, canonical) & b.values(key, canonical))


def _disjoint(a: Source, b: Source, key: str, canonical=words, *, trusted=False) -> bool:
    left = a.values(key, canonical, trusted=trusted)
    right = b.values(key, canonical, trusted=trusted)
    return bool(left and right and left.isdisjoint(right))


def _shipper_or_consignee_agrees(a: Source, b: Source) -> bool:
    return any(_share(a, b, key, company) for key in ("party.shipper", "party.consignee"))


def confirmed_separate(a: Source, b: Source) -> bool:
    """Disjoint transport identifiers alone establish neither a split nor a conflict."""
    bill_a = set().union(*(a.values(k, identifiers) for k in BILL_KEYS))
    bill_b = set().union(*(b.values(k, identifiers) for k in BILL_KEYS))
    if not bill_a or not bill_b or not bill_a.isdisjoint(bill_b):
        return False
    if not _disjoint(a, b, "transport.containers", container_numbers):
        return False
    route_diff = all(_disjoint(a, b, key, port_code, trusted=True) for key in ROUTE_KEYS)
    parties_differ = all(_disjoint(a, b, key, company, trusted=True) for key in ("party.shipper", "party.consignee"))
    invoice_diff = _disjoint(a, b, "invoice.identity", identifiers, trusted=True)
    return (route_diff and not _shipper_or_consignee_agrees(a, b)) or (invoice_diff and parties_differ)


def _shared_bill(a: Source, b: Source) -> bool:
    # Generic B/L can be the typed HBL in another document. Explicitly
    # different levels alone do not identify the same consignment.
    left = a.values(BILL_KEYS[0], identifiers)
    right = b.values(BILL_KEYS[0], identifiers)
    if left & right:
        return True
    return bool((left & b.values(BILL_KEYS[2], identifiers)) or (right & a.values(BILL_KEYS[2], identifiers)))


def _line_codes(source: Source) -> set[str]:
    return {
        identifier(e.value) for line in source.lines.values() for e in line.get("line.sku", ())
        if e.value and e.trusted
    }


def pair_link(a: Source, b: Source) -> Link | None:
    if a.status != "EXTRACTED" or b.status != "EXTRACTED":
        return None
    same_invoice = _share(a, b, "invoice.identity", identifiers)
    same_house = _share(a, b, BILL_KEYS[2], identifiers)
    same_bill = _shared_bill(a, b)
    same_master = _share(a, b, BILL_KEYS[1], identifiers)
    same_container = _share(a, b, "transport.containers", container_numbers)
    same_booking = _share(a, b, "transport.booking_no", identifiers)
    same_party = any(_share(a, b, key, company) for key in PARTY_KEYS)
    same_route = all(_share(a, b, key) for key in ROUTE_KEYS)
    same_po = _share(a, b, "reference.po_no", identifiers)
    same_contract = _share(a, b, "contract.identity", identifiers)
    same_certificate = _share(a, b, "origin.identity", identifiers) and ORIGIN in (a.doc_type, b.doc_type)
    conflicting_invoices = _disjoint(a, b, "invoice.identity", identifiers, trusted=True)
    conflicting_houses = _disjoint(a, b, BILL_KEYS[2], identifiers, trusted=True)
    if a.doc_type == b.doc_type == INVOICE and _disjoint(a, b, "party.seller", company, trusted=True):
        return None
    if conflicting_houses and not same_invoice:
        return None
    if conflicting_invoices and not (same_house or (same_bill and same_party)):
        return None

    signals = tuple(
        label for present, label in (
            (same_invoice, "Số hóa đơn tham chiếu"), (same_house, "HBL"), (same_bill, "B/L"),
            (same_master, "MBL"), (same_container, "Container"), (same_booking, "Booking"),
            (same_party, "Cùng pháp nhân và vai trò"), (same_route, "Cùng cặp cảng"),
            (same_po, "PO"), (same_contract, "Hợp đồng"), (same_certificate, "C/O tham chiếu"),
        ) if present
    )
    if CONTRACT in (a.doc_type, b.doc_type):
        if same_invoice or same_contract or (same_po and same_party):
            return Link(a.key, b.key, 2, signals, "CONTRACT")
        return None
    if same_house or same_bill:
        # A consolidated box can contain many HBLs. Matching at the same level
        # establishes scope only when there is no conflicting invoice or HBL.
        scope = "TRANSPORT" if not (conflicting_invoices or conflicting_houses) else "RELATED"
        return Link(a.key, b.key, 5, signals, scope)
    if same_invoice:
        return Link(a.key, b.key, 5, signals, "INVOICE")
    if same_certificate and (same_party or same_container):
        return Link(a.key, b.key, 4, signals, "RELATED")
    if same_container and (same_booking or same_master or same_party or same_route):
        return Link(a.key, b.key, 4, signals, "RELATED")
    if same_booking and (same_party or same_route):
        return Link(a.key, b.key, 3, signals, "RELATED")
    if same_po and same_party and (_line_codes(a) & _line_codes(b)):
        return Link(a.key, b.key, 3, signals + ("Mã mặt hàng",), "RELATED")
    if {a.doc_type, b.doc_type} == {INVOICE, PACKING} and same_party:
        invoice, packing = (a, b) if a.doc_type == INVOICE else (b, a)
        if invoice.values("invoice.identity", identifiers) & packing.values("document.doc_number", identifiers):
            return Link(a.key, b.key, 3, signals + ("Số chứng từ trùng hóa đơn cần xác minh vai trò",), "RELATED")
    return None


def group_sources(sources: list[Source]) -> tuple[list[list[Source]], list[Link]]:
    members = {source.key: [source] for source in sources}
    owner = {source.key: source.key for source in sources}
    links = [link for a, b in combinations(sources, 2) if (link := pair_link(a, b))]
    forbidden = set()
    for a, b in combinations(sources, 2):
        commercial_split = (_disjoint(a, b, BILL_KEYS[2], identifiers, trusted=True)
                            and _disjoint(a, b, "invoice.identity", identifiers, trusted=True)
                            and not _shipper_or_consignee_agrees(a, b))
        common_anchor = any(_share(a, b, key, identifiers) for key in ("invoice.identity", "transport.booking_no"))
        if commercial_split or (confirmed_separate(a, b) and not common_anchor):
            forbidden.add(frozenset((a.key, b.key)))
    accepted: list[Link] = []

    def join(link: Link) -> None:
        left, right = owner[link.left], owner[link.right]
        if left == right:
            accepted.append(link)
            return
        if any(frozenset((a.key, b.key)) in forbidden for a in members[left] for b in members[right]):
            return
        members[left].extend(members.pop(right))
        for source in members[left]:
            owner[source.key] = left
        accepted.append(link)

    for link in sorted(links, key=lambda value: (-value.strength, value.left, value.right)):
        if link.scope != "CONTRACT":
            join(link)

    contract_links: dict[str, list[Link]] = defaultdict(list)
    contracts = {source.key for source in sources if source.doc_type == CONTRACT}
    for link in links:
        if link.scope == "CONTRACT":
            contract_links[link.left if link.left in contracts else link.right].append(link)
    for key, candidates in sorted(contract_links.items()):
        targets = {owner[link.right if link.left == key else link.left] for link in candidates}
        if len(targets) == 1:
            for link in candidates:
                join(link)

    groups = [sorted(components, key=lambda source: source.key) for components in members.values()]
    return sorted(groups, key=lambda components: (min(s.document_id for s in components), components[0].key)), accepted
