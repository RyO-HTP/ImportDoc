from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from itertools import combinations

from .consistency_groups import (
    BILL_KEYS, Evidence, Link, Source, company, confirmed_separate, container_numbers,
    folded, group_sources, identifier, identifiers, words,
)
from .consistency_matrix import (
    BILL, GOODS, HEADER_RULES, INVOICE, LINE_RULES, PACKING, PARTY_RULES, TRANSPORT, Rule,
)
from .normalize import normalize_currency, normalize_date, normalize_unit

MATCH, MISMATCH, MISSING, VERIFY = "MATCH", "MISMATCH", "MISSING", "VERIFY"
MASS_FACTORS = {
    "KG": Decimal(1), "KGM": Decimal(1), "T": Decimal(1000), "MT": Decimal(1000), "TNE": Decimal(1000),
    "G": Decimal("0.001"), "LB": Decimal("0.45359237"), "LBS": Decimal("0.45359237"),
}
PACKAGE_UNITS = {
    "CTN": "CARTONS", "CTNS": "CARTONS", "CARTON": "CARTONS", "CT": "CARTONS",
    "PLT": "PALLETS", "PALLET": "PALLETS", "PCE": "PCS", "PC": "PCS",
    "PK": "PACKAGES", "PKG": "PACKAGES", "PKGS": "PACKAGES", "PACKAGE": "PACKAGES",
    "SET": "SETS", "BOX": "BOXES", "BAG": "BAGS",
}
ORIGIN_NAMES = {
    "UNITED STATES": "US", "UNITED STATES OF AMERICA": "US", "USA": "US",
    "VIET NAM": "VN", "VIETNAM": "VN", "VIỆT NAM": "VN",
    "CHINA": "CN", "P R CHINA": "CN", "PR CHINA": "CN",
    "TAIWAN": "TW", "HONG KONG": "HK", "JAPAN": "JP", "INDIA": "IN",
    "UNITED KINGDOM": "GB", "UK": "GB", "CANADA": "CA",
}
INCOTERMS = re.compile(r"\b(EXW|FCA|CPT|CIP|DAP|DPU|DDP|FAS|FOB|CFR|CIF|DAT)\b")
AMBIGUOUS_DATE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-]\d{4}$")
SINGLE_GROUP_DECIMAL = re.compile(r"^[+-]?\d{1,3}[,.]\d{3}$")
NUMBER = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
NUMERIC_KINDS = {"number", "mass", "volume", "packages", "money", "line_quantity", "price", "price_basis"}


@dataclass(frozen=True)
class Fact:
    value: object
    evidence: tuple[Evidence, ...]
    ambiguous: bool = False

    @property
    def trusted(self) -> bool:
        return bool(self.evidence) and all(e.trusted for e in self.evidence)


@dataclass
class Check:
    rule: str
    title: str
    status: str
    severity: str
    reason: str
    action: str
    group_id: str | None
    sources: tuple[Source, ...]
    evidence: tuple[Evidence, ...] = ()
    calculated_value: str | None = None

    def metadata(self) -> dict:
        return {
            "field_key": self.rule, "label": self.title, "status": self.status,
            "recommendation": self.action, "group_id": self.group_id,
            "sources": [source.as_dict() for source in self.sources],
            "calculated_value": self.calculated_value,
        }


def _decimal(e: Evidence) -> Decimal | None:
    text = (e.normalized or e.value or "").strip()
    raw = re.sub(r"\s+", "", e.raw or "")
    # One three-digit group admits two readings. Trust a human correction or a
    # value whose raw and normalized forms already agree without that ambiguity.
    if e.review_status not in {"CONFIRMED", "EDITED"} and SINGLE_GROUP_DECIMAL.fullmatch(raw):
        return None
    if not NUMBER.fullmatch(text) or len(text) > 40:
        return None
    try:
        val = Decimal(text)
        return val if val.is_finite() else None
    except InvalidOperation:
        return None


def _date(e: Evidence) -> date | None:
    raw = e.value or ""
    match = AMBIGUOUS_DATE.fullmatch(raw)
    if match and int(match[1]) <= 12 and int(match[2]) <= 12 and match[1] != match[2] and e.review_status not in {"CONFIRMED", "EDITED"}:
        return None
    try:
        return date.fromisoformat(normalize_date(raw) or "")
    except ValueError:
        return None


def _unit(value: str) -> str:
    raw = folded(value)
    normalized = folded(normalize_unit(value) or value)
    # A bare 'ton' is ambiguous across jurisdictions and remains incomparable.
    if raw in {"TON", "TONS"}:
        return raw
    return PACKAGE_UNITS.get(normalized, normalized)


def _canonical(e: Evidence, kind: str):
    value = e.value or ""
    if kind in NUMERIC_KINDS:
        return _decimal(e)
    if kind in {"date", "scheduled", "instrument_date"}:
        return _date(e)
    if kind in {"id", "waybill", "seal"}:
        return identifiers(value)
    if kind == "containers":
        return container_numbers(value)
    if kind == "currency":
        return None if folded(value) in {"DOLLAR", "DOLLARS", "$"} else normalize_currency(value)
    if kind == "unit":
        return _unit(value)
    if kind == "incoterm":
        return set(INCOTERMS.findall(folded(value)))
    if kind == "hs":
        hs = re.sub(r"[.\s-]", "", value)
        return hs if re.fullmatch(r"\d{6,12}", hs) else None
    if kind == "origin":
        name = words(value)
        return ORIGIN_NAMES.get(name, name)
    if kind == "party":
        return company(value)
    return words(value)


def fact(rows: list[Evidence], kind: str) -> Fact:
    present = [e for e in rows if e.value]
    if not present:
        return Fact(None, tuple(rows[:1]))
    trusted = [e for e in present if e.trusted]
    # Container lists can be spread over pages and lines, so retain unverified
    # members as evidence instead of silently shrinking a list to its verified subset.
    selected = present if kind == "containers" else (trusted or present)
    values = [_canonical(e, kind) for e in selected]
    if kind == "containers":
        result = set().union(*(v for v in values if isinstance(v, set)))
        return Fact(frozenset(result) if result else None, tuple(selected), not result)
    if not all(v is not None and v != set() for v in values):
        return Fact(None, tuple(selected), True)
    first = values[0]
    if any(v != first for v in values[1:]):
        return Fact(None, tuple(selected), True)
    return Fact(frozenset(first) if isinstance(first, set) else first, tuple(selected[:1]))


def header(source: Source, key: str, kind: str = "text") -> Fact:
    return fact(source.evidence(key), kind)


def _measure(source: Source, value: Fact, unit_key: str) -> Fact:
    explicit = header(source, unit_key, "unit")
    # Legacy unit columns on *_kg were automatically written as kg even when
    # the raw label did not say so. A real printed unit is required.
    units = explicit.value
    if units is None:
        raw = value.evidence[0].raw or "" if value.evidence else ""
        match = re.search(r"\b(kgm|kgs?|mt|tne|lbs?|g)\b", raw, re.I)
        units = _unit(match[1]) if match else None
    factor = MASS_FACTORS.get(units)
    return Fact(value.value * factor if factor is not None else None, value.evidence + explicit.evidence,
                explicit.ambiguous or (units is not None and factor is None))


def _currency(source: Source, row: dict | None) -> Fact:
    value = fact(row.get("line.currency", []), "currency") if row is not None else Fact(None, ())
    return header(source, "totals.currency", "currency") if value.value is None and not value.ambiguous else value


def _unit_at(source: Source, row: dict | None, key: str) -> Fact:
    return fact(row.get("line.unit", []), "unit") if row is not None else header(source, key, "unit")


def _same(a: Fact, b: Fact) -> bool:
    return a.value is not None and a.value == b.value and not a.ambiguous and not b.ambiguous


def _same_container_scope(a: Source, b: Source) -> bool:
    return _same(header(a, "transport.containers", "containers"), header(b, "transport.containers", "containers"))


def _same_scope(a: Source, b: Source, link: Link | None, rule: Rule, row_left: dict | None) -> bool:
    if not link or link.scope in {"RELATED", "CONTRACT"}:
        return False
    containers_a = header(a, "transport.containers", "containers")
    containers_b = header(b, "transport.containers", "containers")
    if (
        isinstance(containers_a.value, frozenset) and isinstance(containers_b.value, frozenset)
        and containers_a.value != containers_b.value
        and rule.kind in {"mass", "volume", "packages", "line_quantity", "number", "money"}
    ):
        return False
    if rule.kind in {"money", "currency"} and row_left is None:
        return _same(header(a, "invoice.identity", "id"), header(b, "invoice.identity", "id"))
    return True


def _money_tolerance(currency: str | None) -> Decimal:
    if currency in {"VND", "JPY", "KRW", "CLP"}:
        return Decimal(1)
    if currency in {"BHD", "KWD", "OMR", "TND", "JOD"}:
        return Decimal("0.001")
    return Decimal("0.01")


def _outcome(rule: Rule, left: Source, right: Source, a: Fact, b: Fact, group: str,
             status: str, reason: str, *, extra: tuple[Evidence, ...] = (), verify_risk: str = "WARNING") -> Check:
    severity = rule.severity if status == MISMATCH else (verify_risk if status == VERIFY else "INFO")
    return Check(rule.field, rule.label, status, severity, reason, {
        MISMATCH: "Đối chiếu giá trị trên hai bản gốc cùng phạm vi và yêu cầu bên phát hành sửa hoặc xác nhận.",
        VERIFY: "Mở chứng từ nguồn, xác nhận phạm vi, đơn vị và giá trị đọc được trước khi xử lý.",
        MISSING: "Kiểm tra trường trên bản gốc hoặc bổ sung chứng từ có thông tin tương ứng.",
        MATCH: "",
    }[status], group, (left, right), a.evidence + b.evidence + extra)


def _context(rule: Rule, a: Source, b: Source, left_row: dict | None, right_row: dict | None) -> tuple[str | None, tuple[Evidence, ...]]:
    kind = rule.kind
    if kind == "party_attribute":
        key = rule.field.rsplit(".", 1)[0]
        if not _same(header(a, key, "party"), header(b, key, "party")):
            return "Chưa xác định hai giá trị thuộc cùng pháp nhân và vai trò.", ()
    if rule.field == "invoice.date":
        if not _same(header(a, "invoice.identity", "id"), header(b, "invoice.identity", "id")):
            return "Chưa xác định ngày tham chiếu thuộc cùng số hóa đơn.", ()
    if kind == "named_place" and not _same(header(a, "transport.invoice_terms", "incoterm"), header(b, "transport.invoice_terms", "incoterm")):
        return "Cần xác định cùng điều kiện Incoterms trước khi so địa điểm đã nêu.", ()
    if kind == "seal":
        if left_row is None:
            left_ids, right_ids = header(a, "transport.containers", "containers"), header(b, "transport.containers", "containers")
        else:
            left_ids = fact(left_row.get("line.container_no", []), "containers")
            right_ids = fact((right_row or {}).get("line.container_no", []), "containers")
        if not _same(left_ids, right_ids) or len(left_ids.value) != 1:
            return "Chưa ghép được số seal với đúng một container chung.", left_ids.evidence + right_ids.evidence
    return None, ()


def compare(rule: Rule, left: Source, right: Source, link: Link | None, group: str,
            left_row: dict | None = None, right_row: dict | None = None) -> Check | None:
    if rule.kind == "instrument_date" and (
        left.doc_type != right.doc_type or not _same(header(left, "document.doc_number", "id"),
                                                   header(right, "document.doc_number", "id"))
    ):
        return None
    if left_row is None:
        a, b = header(left, rule.field, rule.kind), header(right, rule.field, rule.kind)
    else:
        a = fact(left_row.get(rule.field, []), rule.kind)
        b = fact((right_row or {}).get(rule.field, []), rule.kind)
    if a.ambiguous or b.ambiguous:
        return _outcome(rule, left, right, a, b, group, VERIFY,
                        "Giá trị chưa đọc chắc chắn, định dạng mơ hồ hoặc có nhiều kết quả trích xuất cho cùng trường.")
    if a.value is None or b.value is None:
        expected = (a.value is None and left.doc_type in rule.expected_for or
                    b.value is None and right.doc_type in rule.expected_for)
        if a.value is None and b.value is None and not expected:
            return None
        return _outcome(rule, left, right, a, b, group, MISSING,
                        "Thiếu giá trị có thể đối chiếu trên một hoặc cả hai chứng từ.")
    context, evidence = _context(rule, left, right, left_row, right_row)
    if context:
        return _outcome(rule, left, right, a, b, group, VERIFY, context, extra=evidence, verify_risk="INFO")

    original_a, original_b = a, b
    kind = rule.kind
    compatible_scope = _same_scope(left, right, link, rule, left_row)
    if left_row is not None and right_row is not None:
        compatible_scope = compatible_scope and _trusted_line_identity(left_row, right_row)
    tolerance = Decimal(0)
    if kind == "mass":
        prefix = rule.field.removesuffix("_kg")
        a, b = _measure(left, a, f"{prefix}_unit"), _measure(right, b, f"{prefix}_unit")
        if a.value is None or b.value is None:
            return _outcome(rule, left, right, a, b, group, VERIFY if a.ambiguous or b.ambiguous else MISSING,
                            "Thiếu đơn vị khối lượng in trên nguồn hoặc đơn vị chưa xác định.", verify_risk="INFO")
        tolerance = Decimal(1)
    if kind in {"price", "line_quantity", "packages"}:
        unit_a = _unit_at(left, left_row, "transport.package_unit")
        unit_b = _unit_at(right, right_row, "transport.package_unit")
        evidence += unit_a.evidence + unit_b.evidence
        if kind == "line_quantity" and unit_a.value in MASS_FACTORS and unit_b.value in MASS_FACTORS:
            a = Fact(a.value * MASS_FACTORS[unit_a.value], a.evidence)
            b = Fact(b.value * MASS_FACTORS[unit_b.value], b.evidence)
        elif not _same(unit_a, unit_b):
            return _outcome(rule, left, right, a, b, group, VERIFY,
                            "Đơn vị hoặc cấp đóng gói khác nhau hoặc chưa xác định.", extra=evidence, verify_risk="INFO")
    if kind in {"price", "money"}:
        curr_a, curr_b = _currency(left, left_row), _currency(right, right_row)
        evidence += curr_a.evidence + curr_b.evidence
        if not _same(curr_a, curr_b):
            return _outcome(rule, left, right, a, b, group, VERIFY,
                            "Đồng tiền khác nhau hoặc thiếu, chưa có cơ sở so sánh số tiền.", extra=evidence)
        tolerance = _money_tolerance(curr_a.value) if kind == "money" else Decimal("0.00001")
    if kind == "price":
        base_a = fact((left_row or {}).get("line.price_base_quantity", []), "number")
        base_b = fact((right_row or {}).get("line.price_base_quantity", []), "number")
        evidence += base_a.evidence + base_b.evidence
        if base_a.value is not None and base_b.value is not None and base_a.value > 0 and base_b.value > 0:
            a = Fact(a.value / base_a.value, a.evidence)
            b = Fact(b.value / base_b.value, b.evidence)
        elif base_a.value is not None or base_b.value is not None or base_a.ambiguous or base_b.ambiguous:
            return _outcome(rule, left, right, a, b, group, VERIFY,
                            "Thiếu cơ sở quy đổi đơn giá hoặc cơ sở đơn giá không hợp lệ.", extra=evidence)
    if kind == "volume":
        tolerance = Decimal("0.01")

    equal = _same(a, b)
    if isinstance(a.value, Decimal) and isinstance(b.value, Decimal):
        equal = abs(a.value - b.value) <= tolerance
    if kind == "hs" and a.value[:6] == b.value[:6] and not equal:
        return _outcome(rule, left, right, a, b, group, MATCH,
                        "Khớp sáu số HS quốc tế. Phần mở rộng theo biểu thuế cần xem riêng theo nước khai báo.")
    if kind == "containers" and isinstance(a.value, frozenset) and isinstance(b.value, frozenset):
        if (a.value < b.value or b.value < a.value) and a.value & b.value:
            return _outcome(rule, left, right, a, b, group, VERIFY,
                            "Có container trùng và một danh sách là tập con. Có thể là chứng từ theo từng phần lô.",
                            verify_risk="INFO")
    if kind == "waybill" and a.value & b.value and not equal:
        return _outcome(rule, left, right, a, b, group, VERIFY,
                        "Có vận đơn chung và có số bổ sung. Kiểm tra phạm vi MBL, HBL hoặc vận đơn tách.",
                        verify_risk="INFO")
    if equal and (compatible_scope or kind not in {"mass", "volume", "packages", "money", "line_quantity", "number"}):
        return _outcome(rule, left, right, a, b, group, MATCH, rule.basis, extra=evidence)
    if equal:
        return _outcome(rule, left, right, a, b, group, VERIFY,
                        "Giá trị bằng nhau nhưng chưa xác nhận cùng phạm vi chứng từ.", extra=evidence, verify_risk="INFO")
    verify_risk = "WARNING"
    if not original_a.trusted or not original_b.trusted or any(not e.trusted for e in evidence if e.value):
        reason = "Có khác biệt trong kết quả đọc; ít nhất một giá trị OCR hoặc dữ liệu trích xuất chưa được xác thực."
    elif not compatible_scope:
        reason = "Chứng từ có liên kết nhưng chưa xác định cùng phạm vi lô, chặng hoặc dòng hàng."
        verify_risk = "INFO"
    elif kind in {"waybill", "containers", "seal", "route", "scheduled", "instrument_date", "party", "party_attribute",
                  "description", "unit", "price_basis", "named_place", "commercial"}:
        reason = rule.basis
        if kind in {"waybill", "containers", "seal", "scheduled", "unit", "price_basis"}:
            verify_risk = "INFO"
    elif kind == "origin" and (len(a.value) != 2 or len(b.value) != 2):
        reason = "Chưa quy được cả hai cách ghi về mã nước xuất xứ có thể đối chiếu."
    else:
        return _outcome(rule, left, right, a, b, group, MISMATCH,
                        "Giá trị trên hai nguồn đã xác thực khác nhau trong cùng phạm vi. " + rule.basis, extra=evidence)
    return _outcome(rule, left, right, a, b, group, VERIFY, reason, extra=evidence, verify_risk=verify_risk)


def _line_key(row: dict, key: str, canonical) -> str | None:
    values = {canonical(e.value) for e in row.get(key, ()) if e.value}
    return next(iter(values)) if len(values) == 1 and all(values) else None


def match_lines(left: Source, right: Source) -> tuple[list[tuple[dict, dict]], bool]:
    """Only unique identities may pair. Identical sequence numbers prove nothing."""
    unmatched_left, unmatched_right = dict(left.lines), dict(right.lines)
    pairs = []
    for key, normalizer in (("line.sku", identifier), ("line.spec", words), ("line.description", words)):
        a: dict[str, list[int]] = defaultdict(list)
        b: dict[str, list[int]] = defaultdict(list)
        for index, row in unmatched_left.items():
            if token := _line_key(row, key, normalizer):
                a[token].append(index)
        for index, row in unmatched_right.items():
            if token := _line_key(row, key, normalizer):
                b[token].append(index)
        for token in sorted(a.keys() & b.keys()):
            if len(a[token]) != 1 or len(b[token]) != 1:
                continue
            if key == "line.description" and len(token.split()) < 2:
                continue
            i, j = a[token][0], b[token][0]
            first, second = unmatched_left[i], unmatched_right[j]
            if key != "line.sku":
                sku_a = _line_key(first, "line.sku", identifier)
                sku_b = _line_key(second, "line.sku", identifier)
                if sku_a and sku_b and sku_a != sku_b:
                    continue
            pairs.append((unmatched_left.pop(i), unmatched_right.pop(j)))
    return pairs, bool(unmatched_left or unmatched_right)


def _trusted_line_identity(a: dict, b: dict) -> bool:
    for key, canonical in (("line.sku", identifier), ("line.spec", words), ("line.description", words)):
        left = _line_key(a, key, canonical)
        right = _line_key(b, key, canonical)
        if left and left == right and all(
            any(e.value and canonical(e.value) == left and e.trusted for e in row.get(key, ())) for row in (a, b)
        ):
            return True
    return False


def _internal(source: Source, group: str, rule: str, title: str, facts: tuple[Fact, ...],
              result: bool | None, reason: str, *, calculated: Decimal | None = None,
              missing: bool = False, ambiguous_only: bool = False) -> Check:
    if result is True:
        status, severity = MATCH, "INFO"
    elif result is None and missing and not any(f.ambiguous for f in facts):
        status, severity = MISSING, "INFO"
    elif result is False and not ambiguous_only and all(f.trusted for f in facts if f.value is not None):
        status, severity = MISMATCH, "ERROR"
    else:
        status, severity = VERIFY, "WARNING"
        if result is False and any(not f.trusted for f in facts if f.value is not None):
            reason += " Có nguồn OCR hoặc dữ liệu trích xuất chưa xác thực."
    return Check(rule, title, status, severity, reason,
                 "" if status == MATCH else "Mở nguồn và đối chiếu từng thành phần trước khi xác nhận.", group,
                 (source,), tuple(e for f in facts for e in f.evidence), format(calculated, "f") if calculated is not None else None)


def _has_value(value: Fact) -> bool:
    return value.value is not None or value.ambiguous


def _invoice_line(source: Source, group: str, row: dict) -> Check | None:
    qty, price, amount, base, allowance, charge = (
        fact(row.get(f"line.{key}", []), "number")
        for key in ("quantity", "unit_price", "amount", "price_base_quantity", "allowance_amount", "charge_amount")
    )
    if not _has_value(price) and not _has_value(amount):
        return None
    currency = _currency(source, row)
    facts = (qty, price, amount, base, allowance, charge, currency)
    expected = None
    if not any(f.ambiguous for f in facts) and all(f.value is not None for f in (qty, price, amount, currency)):
        divisor = base.value if base.value is not None else Decimal(1)
        if divisor > 0:
            expected = qty.value * price.value / divisor - (allowance.value or 0) + (charge.value or 0)
    reason = ("Số lượng × đơn giá ÷ cơ sở giá − giảm trừ + phụ phí cấp dòng."
              if expected is not None else "Cần đủ số lượng, đơn giá, thành tiền, đồng tiền và cơ sở tính giá hợp lệ.")
    # An unlisted adjustment is not assumed to be zero for declaring a mismatch.
    missing_adjustment = allowance.value is None or charge.value is None
    return _internal(source, group, "invoice_line", "Tính thành tiền dòng hàng", facts,
                     abs(expected - amount.value) <= _money_tolerance(currency.value) if expected is not None else None,
                     reason, calculated=expected, missing=any(f.value is None for f in (qty, price, amount, currency)),
                     ambiguous_only=missing_adjustment)


def _totals(source: Source, group: str) -> list[Check]:
    line_net, exclusive, inclusive, tax, allowance, charge = (
        header(source, f"totals.{key}", "number") for key in (
            "line_net_total", "tax_exclusive_amount", "tax_inclusive_amount", "tax_amount", "allowance_amount", "charge_amount",
        )
    )
    currency = _currency(source, None)
    tolerance = _money_tolerance(currency.value)
    checks = []
    document_total = header(source, "totals.total_amount", "number")
    subtotal_target = line_net if _has_value(line_net) else document_total
    if _has_value(subtotal_target) and source.lines:
        amounts = [fact(row.get("line.amount", []), "number") for row in source.lines.values()]
        currencies = [_currency(source, row) for row in source.lines.values()]
        complete = (subtotal_target.value is not None and currency.value is not None
                    and all(a.value is not None for a in amounts) and all(_same(c, currency) for c in currencies))
        calculated = sum((a.value for a in amounts), Decimal(0)) if complete else None
        # Extracted line coverage is not independently guaranteed on scans or
        # truncated source regions, so a subtotal difference needs a human check.
        checks.append(_internal(
            source, group, "invoice_subtotal", "Tổng thành tiền các dòng hàng", (subtotal_target, *amounts, *currencies, currency),
            abs(calculated - subtotal_target.value) <= tolerance if complete else None,
            "Cộng các dòng cùng tiền tệ và đối chiếu với tổng dòng ghi riêng trên hóa đơn." if _has_value(line_net)
            else "So tổng số tiền đọc được ở các dòng với tổng ghi trên hóa đơn; kiểm tra thuế, phụ phí hoặc giảm trừ nếu chênh.",
            calculated=calculated, missing=not complete, ambiguous_only=True,
        ))
    for key, title, target, operands, signs in (
        ("invoice_exclusive", "Tổng trước thuế", exclusive, (line_net, allowance, charge), (1, -1, 1)),
        ("invoice_inclusive", "Tổng gồm thuế", inclusive, (exclusive, tax), (1, 1)),
    ):
        if not _has_value(target):
            continue
        complete = currency.value is not None and all(f.value is not None for f in (target, *operands))
        calculated = sum((f.value * sign for f, sign in zip(operands, signs)), Decimal(0)) if complete else None
        checks.append(_internal(
            source, group, key, title, (target, *operands, currency),
            abs(calculated - target.value) <= tolerance if complete else None,
            "Đối chiếu tổng với các thành phần ghi riêng trên hóa đơn và dung sai làm tròn theo tiền tệ.",
            calculated=calculated, missing=not complete,
        ))
    return checks


def internal_checks(source: Source, group: str) -> list[Check]:
    checks = []
    gross = header(source, "transport.gross_weight_kg", "number")
    net = header(source, "transport.net_weight_kg", "number")
    if _has_value(gross) and _has_value(net):
        gross_kg = _measure(source, gross, "transport.gross_weight_unit") if gross.value is not None else gross
        net_kg = _measure(source, net, "transport.net_weight_unit") if net.value is not None else net
        complete = gross_kg.value is not None and net_kg.value is not None
        checks.append(_internal(
            source, group, "gross_net", "Đối chiếu gross và net", (gross_kg, net_kg),
            gross_kg.value >= net_kg.value >= 0 if complete else None,
            "Khối lượng gross của hàng phải lớn hơn hoặc bằng net khi dùng cùng phạm vi và đơn vị.",
            missing=not complete,
        ))
    count = header(source, "transport.container_count", "number")
    ids = header(source, "transport.containers", "containers")
    if _has_value(count) and _has_value(ids):
        unique = Decimal(len(ids.value)) if ids.value is not None else None
        valid = count.value == unique if count.value is not None and unique is not None else None
        checks.append(_internal(
            source, group, "container_count", "Đối chiếu số lượng container", (count, ids), valid,
            "So số khai báo với số mã container đọc được. Chênh lệch có thể do danh sách nhiều trang hoặc OCR bỏ sót.",
            calculated=unique, ambiguous_only=unique is None or count.value is None or unique < count.value,
        ))
    on_board = header(source, "transport.on_board_date", "date")
    actual = header(source, "transport.actual_arrival_date", "date")
    if _has_value(on_board) and _has_value(actual):
        complete = on_board.value is not None and actual.value is not None
        checks.append(_internal(
            source, group, "transport_dates", "Trình tự ngày vận chuyển", (on_board, actual),
            on_board.value <= actual.value if complete else None,
            "Ngày đến thực tế phải bằng hoặc sau ngày on-board trong cùng chặng.",
        ))
    if source.doc_type == INVOICE:
        checks.extend(check for row in source.lines.values() if (check := _invoice_line(source, group, row)))
        checks.extend(_totals(source, group))
    return checks


def _line_evidence(source: Source) -> tuple[Evidence, ...]:
    return tuple(
        e for row in source.lines.values() for key in ("line.sku", "line.description", "line.spec")
        for e in row.get(key, ()) if e.value
    )


def _link_evidence(source: Source) -> tuple[Evidence, ...]:
    return tuple(
        e for key in (*BILL_KEYS, "transport.containers", "invoice.identity", "party.shipper", "party.consignee",
                      "transport.loading_port", "transport.discharge_port")
        for e in source.evidence(key) if e.value
    )


def _partition_checks(members: list[Source], group: str) -> list[Check]:
    checks = []
    for anchor in (source for source in members if source.doc_type == INVOICE):
        total_containers = header(anchor, "transport.containers", "containers")
        invoice = header(anchor, "invoice.identity", "id")
        if not isinstance(total_containers.value, frozenset) or len(total_containers.value) < 2 or not invoice.value:
            continue
        for doc_type in (PACKING, BILL):
            parts = []
            for source in members:
                if source.doc_type != doc_type or not _same(invoice, header(source, "invoice.identity", "id")):
                    continue
                boxes = header(source, "transport.containers", "containers")
                if isinstance(boxes.value, frozenset) and boxes.value < total_containers.value:
                    parts.append((source, boxes))
            represented = set().union(*(boxes.value for _, boxes in parts))
            if represented != total_containers.value or sum(len(boxes.value) for _, boxes in parts) != len(represented):
                continue
            sources = (anchor, *(source for source, _ in parts))
            coverage = (invoice, total_containers, *(boxes for _, boxes in parts))
            for category, title, key, kind, tolerance in (
                ("partition.packages", "Tổng số kiện theo các phần của lô", "transport.packages", "packages", Decimal(0)),
                ("partition.gross_weight", "Tổng gross theo các phần của lô", "transport.gross_weight_kg", "mass", Decimal(1)),
            ):
                facts = tuple(header(source, key, "number") for source in sources)
                if any(f.value is None for f in facts):
                    continue
                if kind == "mass":
                    facts = tuple(_measure(source, value, "transport.gross_weight_unit")
                                  for source, value in zip(sources, facts))
                    units: tuple[Fact, ...] = ()
                else:
                    units = tuple(header(source, "transport.package_unit", "unit") for source in sources)
                    if not all(_same(units[0], unit) for unit in units):
                        continue
                if any(f.value is None for f in facts):
                    continue
                sum_parts = sum((f.value for f in facts[1:]), Decimal(0))
                matches = abs(sum_parts - facts[0].value) <= tolerance
                result = _internal(
                    anchor, group, category, title, (*facts, *units, *coverage), matches,
                    "Tổng các phần có container rời nhau và phủ đủ danh sách ghi trên hóa đơn.",
                    calculated=sum_parts,
                )
                result.sources = sources
                checks.append(result)
    return checks


def _cross_transport_dates(left: Source, right: Source, link: Link | None, group: str) -> list[Check]:
    if not link or link.scope != "TRANSPORT" or not {left.doc_type, right.doc_type} <= TRANSPORT:
        return []
    for key, kind in (("transport.vessel", "text"), ("transport.voyage", "text"), ("transport.containers", "containers")):
        a, b = header(left, key, kind), header(right, key, kind)
        if a.value is not None and b.value is not None and a.value != b.value:
            return []
    checks = []
    for departure, arrival in ((left, right), (right, left)):
        loaded = header(departure, "transport.on_board_date", "date")
        actual = header(arrival, "transport.actual_arrival_date", "date")
        if loaded.value is None or actual.value is None:
            continue
        result = _internal(
            departure, group, "transport_dates", "Trình tự ngày vận chuyển", (loaded, actual),
            loaded.value <= actual.value, "So ngày on-board với ngày đến thực tế trên hai chứng từ cùng chặng.",
        )
        result.sources = (departure, arrival)
        checks.append(result)
    return checks


def evaluate(sources: list[Source]) -> tuple[list[dict], list[Check]]:
    groups, links = group_sources(sources)
    pair_links = {frozenset((link.left, link.right)): link for link in links}
    group_data, checks = [], []
    for members in groups:
        group = f"lot-{members[0].key}"
        keys = {source.key for source in members}
        group_data.append({
            "id": group,
            "status": "LINKED" if len(members) > 1 else "UNLINKED",
            "documents": [source.as_dict() for source in members],
            "links": [{"left": link.left, "right": link.right, "signals": list(link.signals)}
                      for link in links if link.left in keys and link.right in keys],
        })
        for source in members:
            has_values = any(e.value for rows in source.fields.values() for e in rows) or any(
                e.value for line in source.lines.values() for rows in line.values() for e in rows
            )
            if source.status == "EXTRACTED" and has_values:
                checks.extend(internal_checks(source, group))
            else:
                checks.append(Check(
                    "document.extraction", "Chứng từ chưa có kết quả trích xuất", MISSING, "INFO",
                    "Chưa có giá trị để đối chiếu từ chứng từ này.", "Trích xuất lại hoặc bổ sung dữ liệu từ nguồn.",
                    group, (source,),
                ))
        for left, right in combinations(members, 2):
            link = pair_links.get(frozenset((left.key, right.key)))
            checks.extend(_cross_transport_dates(left, right, link, group))
            for rule in (*HEADER_RULES, *PARTY_RULES):
                if {left.doc_type, right.doc_type} <= rule.documents:
                    result = compare(rule, left, right, link, group)
                    if result:
                        checks.append(result)
            if not {left.doc_type, right.doc_type} <= GOODS or not (left.lines or right.lines):
                continue
            pairs, ambiguous = match_lines(left, right)
            if ambiguous:
                checks.append(Check(
                    "line.identity", "Nhận diện dòng hàng", VERIFY if left.lines and right.lines else MISSING, "INFO",
                    "Có dòng chưa ghép duy nhất bằng SKU, quy cách hoặc mô tả. Có thể tách hoặc gộp dòng theo chứng từ.",
                    "Đối chiếu mã hàng và phạm vi trước khi so số lượng hoặc trị giá.", group, (left, right),
                    (_line_evidence(left) + _line_evidence(right))[:24],
                ))
            for a, b in pairs:
                for rule in LINE_RULES:
                    if {left.doc_type, right.doc_type} <= rule.documents:
                        result = compare(rule, left, right, link, group, a, b)
                        if result:
                            checks.append(result)
        checks.extend(_partition_checks(members, group))

    # When two transport sets remain separate, explain the ambiguity once per
    # pair of groups instead of treating every document in one group as a mismatch.
    for group_left, group_right in combinations(groups, 2):
        candidates = [
            (left, right) for left in group_left for right in group_right
            if left.doc_type in TRANSPORT and right.doc_type in TRANSPORT
            and left.status == right.status == "EXTRACTED"
            and _link_evidence(left) and _link_evidence(right)
        ]
        if not candidates:
            continue
        independent = next(((a, b) for a, b in candidates if confirmed_separate(a, b)), None)
        left, right = independent or candidates[0]
        checks.append(Check(
            "shipment.identity",
            "Hai nhóm có dấu hiệu thuộc các lô riêng" if independent else "Chưa đủ căn cứ để ghép hai nhóm vận tải",
            MATCH if independent else VERIFY, "INFO",
            "Vận đơn và container rời nhau, đồng thời có bằng chứng độc lập về tuyến hoặc giao dịch."
            if independent else
            "Vận đơn và container có thể tách theo HBL, MBL, đợt giao hoặc nhiều container trong cùng lô.",
            "" if independent else "Bổ sung booking, hóa đơn tham chiếu hoặc quan hệ MBL/HBL để xác nhận.",
            None, (left, right), _link_evidence(left) + _link_evidence(right),
        ))
    return group_data, checks
