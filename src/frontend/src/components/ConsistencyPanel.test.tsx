import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ConsistencyReport } from "../api/types";
import ConsistencyPanel from "./ConsistencyPanel";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    getConsistency: vi.fn(),
    runConsistency: vi.fn(),
    dismissFinding: vi.fn(),
  },
}));

const getConsistency = vi.mocked(api.getConsistency);
const runConsistency = vi.mocked(api.runConsistency);
const dismissFinding = vi.mocked(api.dismissFinding);

const report: ConsistencyReport = {
  run: { id: 1, status: "DONE", error: null, finding_count: 1, created_at: "2026-11-23T08:00:00Z" },
  stale: false,
  counts: { error: 1, warning: 0 },
  findings: [
    {
      id: 7,
      severity: "ERROR",
      category: "weight",
      title: "Gross lệch",
      explanation: "Trọng lượng gross khác nhau giữa PL và B/L",
      dismissed: false,
      dismiss_note: null,
      values: [
        {
          field_id: 100, document_id: 10, filename: "packing-list.pdf", doc_type: "PACKING_LIST",
          page: 1, bbox: null, cell_ref: null, field_key: "transport.gross_weight_kg", value: "26400",
        },
        {
          field_id: 200, document_id: 20, filename: "bill.pdf", doc_type: "TRANSPORT_DOCUMENT",
          page: 2, bbox: null, cell_ref: null, field_key: "transport.gross_weight_kg", value: "26040",
        },
      ],
    },
  ],
};

function renderPanel(onValueClick = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ConsistencyPanel shipmentId={1} onValueClick={onValueClick} />
    </QueryClientProvider>,
  );
  return onValueClick;
}

describe("ConsistencyPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getConsistency.mockResolvedValue(report);
    runConsistency.mockResolvedValue(report);
  });

  it("lists findings with values per document and counts", async () => {
    renderPanel();
    expect(await screen.findByText("Gross lệch")).toBeTruthy();
    expect(screen.getByText("1 lỗi · 0 cảnh báo")).toBeTruthy();
    expect(screen.getByText(/packing-list\.pdf · tr\.1/)).toBeTruthy();
    expect(screen.getByText("26400")).toBeTruthy();
    expect(screen.getByText("26040")).toBeTruthy();
  });

  it("calls onValueClick when a value is clicked", async () => {
    const onValueClick = renderPanel();
    fireEvent.click(await screen.findByText("26040"));
    expect(onValueClick).toHaveBeenCalledWith(report.findings[0].values[1]);
  });

  it("re-runs the check", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Kiểm tra lại" }));
    await waitFor(() => expect(runConsistency).toHaveBeenCalledWith(1));
  });

  it("dismisses a finding", async () => {
    const dismissed: ConsistencyReport = {
      ...report,
      counts: { error: 0, warning: 0 },
      findings: [{ ...report.findings[0], dismissed: true }],
    };
    dismissFinding.mockResolvedValue(dismissed);
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Bỏ qua: Gross lệch" }));
    await waitFor(() => expect(dismissFinding).toHaveBeenCalledWith(7, undefined));
    expect(await screen.findByText("Đã bỏ qua")).toBeTruthy();
  });

  it("shows the failure notice when the last run failed", async () => {
    getConsistency.mockResolvedValue({ ...report, run: { ...report.run!, status: "FAILED", error: "timeout" } });
    renderPanel();
    expect(await screen.findByText(/Lần kiểm tra gần nhất thất bại/)).toBeTruthy();
  });

  it("shows an empty state when there are no findings", async () => {
    getConsistency.mockResolvedValue({ ...report, findings: [], counts: { error: 0, warning: 0 } });
    renderPanel();
    expect(await screen.findByText("Không phát hiện thông tin lệch giữa các chứng từ")).toBeTruthy();
  });

  it("tells the reviewer to re-run when the documents changed since the last check", async () => {
    getConsistency.mockResolvedValue({ ...report, stale: true });
    renderPanel();
    expect(await screen.findByText(/Chứng từ đã thay đổi sau lần kiểm tra gần nhất/)).toBeTruthy();
  });

  it("filters by outcome and keeps each A/B value linked to its PDF location", async () => {
    const mismatch = report.findings[0];
    const sources = mismatch.values.map((value) => ({
      id: `${value.document_id}:0`, document_id: value.document_id, filename: value.filename,
      doc_type: value.doc_type!, section_id: null, page: value.page, status: "EXTRACTED",
    }));
    const values = mismatch.values.map((value, index) => ({
      ...value, source_id: sources[index].id, bbox: [0.1, 0.2, 0.3, 0.4], label: "Khối lượng",
    }));
    getConsistency.mockResolvedValue({
      ...report,
      status_counts: { MATCH: 0, MISMATCH: 1, MISSING: 1, VERIFY: 0 },
      findings: [
        { ...mismatch, status: "MISMATCH", sources, values, recommendation: "Đọc lại chứng từ gốc." },
        { ...mismatch, id: 8, severity: "INFO", status: "MISSING", title: "Thiếu số seal", sources, values: [] },
      ],
    });
    const onValueClick = renderPanel();
    expect(await screen.findByText("Chứng từ A")).toBeTruthy();
    expect(screen.getByText("Chứng từ B")).toBeTruthy();
    expect(screen.getByText("Đọc lại chứng từ gốc.")).toBeTruthy();
    fireEvent.click(screen.getByText("26040"));
    expect(onValueClick).toHaveBeenCalledWith(values[1]);
    fireEvent.click(screen.getByRole("button", { name: "Thiếu dữ liệu · 1" }));
    expect(await screen.findByText("Thiếu số seal")).toBeTruthy();
    expect(screen.queryByText("Gross lệch")).toBeNull();
  });
});
