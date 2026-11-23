import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { ConsistencyFinding, ConsistencyReport, ConsistencyStatus, ConsistencyValue } from "../api/types";
import { Button, Callout, EmptyState, LocalizedStatus, Spinner } from "./ui";

const statusLabels = { MATCH: "Khớp", MISMATCH: "Không khớp", MISSING: "Thiếu dữ liệu", VERIFY: "Cần xác minh" } as const;
const statusTones = { MATCH: "green", MISMATCH: "red", MISSING: "gray", VERIFY: "amber" } as const;
const riskLabels = { ERROR: "Nghiêm trọng", WARNING: "Cảnh báo", INFO: "Tham khảo" } as const;
const docLabels: Record<string, string> = {
  COMMERCIAL_INVOICE: "Invoice", PACKING_LIST: "Packing List", TRANSPORT_DOCUMENT: "B/L",
  ARRIVAL_NOTICE: "Arrival Notice", SALES_CONTRACT_PO: "Contract / PO", CERTIFICATE_OF_ORIGIN: "C/O",
  CUSTOMS_DECLARATION_SCAN: "Tờ khai nhập khẩu", EXPORT_DECLARATION: "Tờ khai xuất khẩu",
  CUSTOMS_DECLARATION: "Tờ khai nhập khẩu",
  INSURANCE_POLICY: "Bảo hiểm", PHYTOSANITARY_CERTIFICATE: "Kiểm dịch", SERVICE_CONTRACT: "Hợp đồng dịch vụ",
};

function findingStatus(finding: ConsistencyFinding): ConsistencyStatus {
  return finding.status ?? (finding.severity === "ERROR" ? "MISMATCH" : "VERIFY");
}

export default function ConsistencyPanel({
  shipmentId,
  onValueClick,
}: {
  shipmentId: number;
  onValueClick: (value: ConsistencyValue) => void;
}) {
  const [status, setStatus] = useState<ConsistencyStatus | "attention">("attention");
  const [groupId, setGroupId] = useState("");
  const queryClient = useQueryClient();
  const queryKey = ["consistency", shipmentId];
  const query = useQuery({ queryKey, queryFn: () => api.getConsistency(shipmentId) });

  const store = (report: ConsistencyReport) => queryClient.setQueryData(queryKey, report);
  const runMutation = useMutation({ mutationFn: () => api.runConsistency(shipmentId), onSuccess: store });
  const dismissMutation = useMutation({
    mutationFn: (findingId: number) => api.dismissFinding(findingId, undefined),
    onSuccess: store,
  });

  const report = query.data;
  const counts = report?.counts ?? { error: 0, warning: 0 };
  const groups = report?.groups ?? [];
  const activeGroup = groups.find((group) => group.id === groupId);
  const findings = (report?.findings ?? []).filter((finding) => {
    if (activeGroup && finding.group_id !== activeGroup.id) return false;
    const value = findingStatus(finding);
    return status === "attention" ? value === "MISMATCH" || value === "VERIFY" : value === status;
  });

  return (
    <section
      aria-label="Kiểm tra chéo"
      className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border border-hairline bg-canvas"
    >
      <header className="flex shrink-0 items-center justify-between gap-2 border-b border-divider px-3 py-2">
        <div className="flex min-w-0 items-baseline gap-2">
          <h2 className="type-caption-strong shrink-0 text-ink">Kiểm tra chéo</h2>
          <p className="type-fine-print truncate text-ink-48">
            {counts.error} lỗi · {counts.warning} cảnh báo
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={() => runMutation.mutate()} disabled={runMutation.isPending}>
          {runMutation.isPending ? "Đang kiểm tra…" : "Kiểm tra lại"}
        </Button>
      </header>

      <div className="min-h-0 flex-1 space-y-3 overflow-auto px-3 py-3">
        {query.isPending ? <Spinner /> : null}
        {query.isError ? (
          <Callout tone="critical" title="Không tải được kết quả kiểm tra chéo">{query.error.message}</Callout>
        ) : null}
        {runMutation.isError ? (
          <Callout tone="critical" title="Không chạy được kiểm tra chéo">{runMutation.error.message}</Callout>
        ) : null}
        {report?.stale && report.run?.status !== "FAILED" ? (
          <Callout tone="info" title="Chứng từ đã thay đổi sau lần kiểm tra gần nhất">
            Bấm “Kiểm tra lại” để đối chiếu với dữ liệu và quy tắc hiện tại.
          </Callout>
        ) : null}
        {report?.run?.status === "FAILED" ? (
          <Callout tone="warning" title="Lần kiểm tra gần nhất thất bại">
            {report.run.error ?? "Không rõ lỗi."}
          </Callout>
        ) : null}
        {report?.status_counts && report.run && !report.stale ? (
          <div className="space-y-2 border-b border-divider pb-2">
            <div role="group" aria-label="Lọc theo kết quả" className="flex flex-wrap gap-1.5">
              <button type="button" aria-pressed={status === "attention"} onClick={() => setStatus("attention")}
                className="type-fine-print rounded-pill border border-hairline px-2 py-1 aria-pressed:bg-pearl">
                Cần xem lại
              </button>
              {(["MISMATCH", "VERIFY", "MISSING", "MATCH"] as const).map((key) => (
                <button key={key} type="button" aria-pressed={status === key} onClick={() => setStatus(key)}
                  className="type-fine-print rounded-pill border border-hairline px-2 py-1 aria-pressed:bg-pearl">
                  {statusLabels[key]} · {report.status_counts?.[key] ?? 0}
                </button>
              ))}
            </div>
            {groups.length > 0 ? (
              <details className="type-caption">
                <summary className="cursor-pointer">Nhóm chứng từ · {groups.length}</summary>
                <label className="mt-2 flex items-center gap-2">
                  <span>Hiển thị</span>
                  <select value={activeGroup?.id ?? ""} onChange={(event) => setGroupId(event.target.value)}
                    className="min-w-0 rounded-md border border-hairline bg-canvas px-1.5 py-1">
                    <option value="">Tất cả nhóm</option>
                    {groups.map((group, index) => (
                      <option key={group.id} value={group.id}>
                        Nhóm {index + 1} · {group.status === "LINKED" ? `${group.documents.length} chứng từ` : "Chưa có liên kết"}
                      </option>
                    ))}
                  </select>
                </label>
                <ul className="mt-2 space-y-1 text-ink-80">
                  {(activeGroup ? [activeGroup] : groups).map((group) => (
                    <li key={group.id}>
                      <strong>Nhóm {groups.indexOf(group) + 1}</strong>{" "}
                      {group.documents.map((doc) => doc.filename + (doc.section_id && doc.page ? ` (tr.${doc.page})` : "")).join(" · ")}
                      {group.links.length > 0 ? (
                        <p className="type-fine-print text-ink-48">{[...new Set(group.links.flatMap((link) => link.signals))].join(" · ")}</p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
            {report.matrix?.length ? (
              <details className="type-caption">
                <summary className="cursor-pointer">Ma trận đối chiếu</summary>
                <dl className="mt-2 max-h-64 divide-y divide-divider overflow-auto">
                  {report.matrix.map((rule) => (
                    <div key={rule.field_key} className="py-1.5">
                      <dt className="font-semibold">{rule.label}</dt>
                      <dd className="type-fine-print">{rule.document_types.map((type) => docLabels[type] ?? type).join(" · ") || "Trong cùng chứng từ"}</dd>
                      <dd>{rule.basis}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            ) : null}
          </div>
        ) : null}
        {report && findings.length === 0 && report.run?.status !== "FAILED" && !report.stale ? (
          <EmptyState
            title={report.status_counts ? "Không có kết quả phù hợp" : "Không phát hiện thông tin lệch giữa các chứng từ"}
            description={report.run ? "Chọn một trạng thái khác để xem kết quả đối chiếu."
              : "Chưa kiểm tra. Bấm “Kiểm tra lại” để đối chiếu các chứng từ."}
          />
        ) : null}
        {findings.map((finding) => (
          <FindingCard
            key={finding.id}
            finding={finding}
            onValueClick={onValueClick}
            onDismiss={() => dismissMutation.mutate(finding.id)}
            dismissing={dismissMutation.isPending && dismissMutation.variables === finding.id}
          />
        ))}
      </div>
    </section>
  );
}

function FindingCard({
  finding,
  onValueClick,
  onDismiss,
  dismissing,
}: {
  finding: ConsistencyFinding;
  onValueClick: (value: ConsistencyValue) => void;
  onDismiss: () => void;
  dismissing: boolean;
}) {
  const status = findingStatus(finding);
  const isError = finding.severity === "ERROR";
  const isWarning = finding.severity === "WARNING";
  const sources = finding.sources ?? [];
  const valueRow = (value: ConsistencyValue) => (
    <li key={value.field_id}>
      <button
        type="button"
        onClick={() => onValueClick(value)}
        title="Bấm để xem nguồn"
        className="type-caption flex w-full min-w-0 items-baseline justify-between gap-2 rounded-sm px-2 py-1 text-left hover:bg-pearl focus:outline-none focus-visible:outline-2 focus-visible:outline-action"
      >
        <span className="min-w-0 flex-1 text-ink-48">
          {sources.length ? value.label ?? value.field_key : value.filename}
          {value.page ? ` · tr.${value.page}` : ""}
          {value.cell_ref ? ` · ${value.cell_ref}` : ""}
          {value.trusted === false && value.value ? " · Chưa xác thực" : ""}
        </span>
        <span className="type-caption-strong max-w-[60%] break-words text-right text-ink">{value.value ?? "Chưa có dữ liệu"}</span>
      </button>
    </li>
  );

  return (
    <article className={`rounded-md border px-3 py-2 ${
      finding.dismissed ? "border-hairline opacity-60" : isError ? "border-critical-line" : isWarning ? "border-warning-line" : "border-hairline"
    }`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="type-caption-strong text-ink">{finding.title}</p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <LocalizedStatus tone={statusTones[status]}>{statusLabels[status]}</LocalizedStatus>
            <span className="type-fine-print text-ink-48">{riskLabels[finding.severity]}</span>
          </div>
          {finding.explanation ? <p className="type-caption mt-0.5 text-ink-80">{finding.explanation}</p> : null}
          {finding.recommendation ? <p className="type-caption mt-1 text-ink-80"><strong>Xử lý đề xuất</strong> {finding.recommendation}</p> : null}
        </div>
        {finding.dismissed ? (
          <span className="type-fine-print shrink-0 text-ink-48">Đã bỏ qua</span>
        ) : finding.severity !== "INFO" ? (
          <button
            type="button"
            onClick={onDismiss}
            disabled={dismissing}
            aria-label={`Bỏ qua: ${finding.title}`}
            className="type-fine-print shrink-0 rounded-full border border-hairline px-2 py-0.5 text-ink-48 hover:bg-pearl focus:outline-none focus-visible:outline-2 focus-visible:outline-action disabled:opacity-40"
          >
            Bỏ qua
          </button>
        ) : null}
      </div>
      {sources.length ? sources.map((source, index) => {
        const values = finding.values.filter((value) => value.source_id === source.id);
        return (
          <div key={source.id} className="mt-2 border-t border-divider pt-1">
            <p className="type-fine-print break-words text-ink-80">
              <strong>Chứng từ {String.fromCharCode(65 + index)}</strong> · {source.filename} · {docLabels[source.doc_type] ?? source.doc_type}
            </p>
            {values.length ? <ul>{values.map(valueRow)}</ul> : <p className="type-fine-print text-ink-48">Chưa có dữ liệu nguồn cho trường này.</p>}
          </div>
        );
      }) : <ul className="mt-2 space-y-1">{finding.values.map(valueRow)}</ul>}
      {finding.calculated_value != null ? <p className="type-caption mt-1"><strong>Giá trị tính từ nguồn</strong> {finding.calculated_value}</p> : null}
      {finding.dismissed && finding.dismiss_note ? (
        <p className="type-fine-print mt-1 text-ink-48">Ghi chú: {finding.dismiss_note}</p>
      ) : null}
    </article>
  );
}
