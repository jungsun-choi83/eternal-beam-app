"use client";

/**
 * /exhibition/staff — 전시 현장 스태프 화면 (폰/태블릿).
 *
 *   [ Take / Upload Pet Photo ] + 이름(선택) → 번호 배정 → 처리(기존 워커) → READY
 *   NOW SHOWING · UP NEXT · READY · PREPARING · NEEDS ATTENTION
 *   [ SHOW NEXT ] — 수동. 외부 기기가 아직 DISPLAY_STARTED/FINISHED 를 보내지 않는다.
 *
 * 인증·권한 화면은 OpsLayout 이 맡는다 (서버: JWT + SHAKER_OPS_USER_IDS).
 * 상태는 서버가 정한다 — 이 화면은 폴링해서 그리고, SHOW NEXT 에 자기가 본 상태를
 * 함께 보내 두 번 누름·두 기기 동시 누름이 한 칸 더 넘기지 않게 한다.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import {
  STAFF_POLL_MS,
  canShowNext,
  currentExhibitionId,
  fetchStaffQueue,
  formatQueueNumber,
  queueLabel,
  showNext,
  submitPetPhoto,
  type QueueItem,
  type StaffQueue,
} from "@/lib/exhibition-queue";
import { OpsLayout, type OpsChildProps } from "./ops-layout";
import { Button, Card, CardTitle, EmptyState, ErrorNote, OPS, Pill, TextInput } from "./ops-ui";

export function ExhibitionStaffScreen() {
  const exhibitionId = currentExhibitionId();
  return (
    <OpsLayout
      active="exhibition"
      title="Exhibition"
      subtitle={exhibitionId ? `Exhibition: ${exhibitionId}` : "Default exhibition"}
    >
      {(p) => <StaffBody {...p} exhibitionId={exhibitionId} />}
    </OpsLayout>
  );
}

const AUTH_CODES = new Set(["UNAUTHENTICATED", "OPS_FORBIDDEN"]);

export function StaffBody({
  token,
  onAuthError,
  exhibitionId,
}: OpsChildProps & { exhibitionId: string | null }) {
  const [queue, setQueue] = useState<StaffQueue | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [advancing, setAdvancing] = useState(false);
  const mounted = useRef(true);

  const handleError = useCallback(
    (e: unknown, set: (m: string) => void) => {
      const code = (e as { code?: string })?.code ?? "";
      if (AUTH_CODES.has(code)) onAuthError(e);
      else set((e as Error)?.message || "Request failed.");
    },
    [onAuthError]
  );

  const refresh = useCallback(async () => {
    try {
      const q = await fetchStaffQueue(token, exhibitionId);
      if (!mounted.current) return;
      setQueue(q);
      setLoadError(null);
    } catch (e) {
      if (mounted.current) handleError(e, setLoadError);
    }
  }, [token, exhibitionId, handleError]);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    const id = window.setInterval(() => void refresh(), STAFF_POLL_MS);
    return () => {
      mounted.current = false;
      window.clearInterval(id);
    };
  }, [refresh]);

  const onShowNext = useCallback(async () => {
    if (!queue || advancing) return;
    setAdvancing(true);
    setActionError(null);
    try {
      setQueue(await showNext(token, queue));
    } catch (e) {
      const code = (e as { code?: string })?.code ?? "";
      if (code === "QUEUE_STATE_CHANGED") {
        setActionError("The queue changed on another device — refreshed. Check the screen and press again if needed.");
        void refresh();
      } else {
        handleError(e, setActionError);
      }
    } finally {
      setAdvancing(false);
    }
  }, [queue, advancing, token, refresh, handleError]);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <SubmitCard
        token={token}
        exhibitionId={exhibitionId}
        onSubmitted={() => void refresh()}
        onError={(e, set) => handleError(e, set)}
      />

      {loadError ? <ErrorNote>{loadError}</ErrorNote> : null}

      <Card>
        <CardTitle
          action={
            <Button tone="primary" onClick={onShowNext} busy={advancing} disabled={!canShowNext(queue)}>
              SHOW NEXT
            </Button>
          }
        >
          Display
        </CardTitle>
        {actionError ? <ErrorNote>{actionError}</ErrorNote> : null}
        <Slot label="NOW SHOWING" item={queue?.now_showing ?? null} big />
        <Slot label="UP NEXT" item={queue?.up_next ?? null} />
        <p className="mt-3 text-[12px]" style={{ color: OPS.textFaint }}>
          SHOW NEXT: now showing → complete, up next → now showing, next ready → up next.
        </p>
      </Card>

      <ListCard title="READY" items={queue?.ready} empty="No ready pets waiting." />
      <ListCard title="PREPARING" items={queue?.preparing} empty="Nothing processing." showProcessing />
      {queue && queue.needs_attention.length ? (
        <ListCard
          title="NEEDS ATTENTION"
          items={queue.needs_attention}
          empty=""
          showDetail
          note="These photos did not pass processing and will not be shown. Retake and submit again."
        />
      ) : null}
      {queue && queue.recently_complete.length ? (
        <ListCard title="RECENTLY COMPLETE" items={queue.recently_complete} empty="" muted />
      ) : null}
    </div>
  );
}

function SubmitCard({
  token,
  exhibitionId,
  onSubmitted,
  onError,
}: {
  token: string;
  exhibitionId: string | null;
  onSubmitted: () => void;
  onError: (e: unknown, set: (m: string) => void) => void;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [petName, setPetName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [assigned, setAssigned] = useState<string | null>(null);

  useEffect(() => {
    if (!file) {
      setPreview(null);
      return;
    }
    const url = typeof URL.createObjectURL === "function" ? URL.createObjectURL(file) : null;
    setPreview(url);
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [file]);

  const submit = useCallback(async () => {
    if (!file || busy) return;
    setBusy(true);
    setError(null);
    try {
      const s = await submitPetPhoto(token, { file, petName, exhibitionId });
      setAssigned(queueLabel(s));
      setFile(null);
      setPetName("");
      if (inputRef.current) inputRef.current.value = "";
      onSubmitted();
    } catch (e) {
      onError(e, setError);
    } finally {
      setBusy(false);
    }
  }, [file, busy, token, petName, exhibitionId, onSubmitted, onError]);

  return (
    <Card>
      <CardTitle>New pet</CardTitle>
      {/* capture 를 강제하지 않는다 — 폰은 카메라/앨범을 고르게 해 준다. */}
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        aria-label="Pet photo"
        className="hidden"
        onChange={(e) => {
          setAssigned(null);
          setFile(e.target.files?.[0] ?? null);
        }}
      />
      <Button full onClick={() => inputRef.current?.click()} disabled={busy}>
        {file ? "Change photo" : "Take / Upload Pet Photo"}
      </Button>

      {preview ? (
        <img
          src={preview}
          alt="Selected pet"
          className="mx-auto mt-3 max-h-48 rounded-lg object-contain"
          style={{ border: `1px solid ${OPS.border}` }}
        />
      ) : null}

      <label className="mt-3 block text-[12px]" style={{ color: OPS.textMuted }}>
        Pet name (optional)
        <div className="mt-1">
          <TextInput value={petName} onChange={setPetName} placeholder="e.g. Coco" onEnter={() => void submit()} />
        </div>
      </label>

      <div className="mt-3">
        <Button tone="primary" full onClick={() => void submit()} busy={busy} disabled={!file}>
          Add to queue
        </Button>
      </div>
      {error ? (
        <div className="mt-3">
          <ErrorNote>{error}</ErrorNote>
        </div>
      ) : null}
      {assigned ? (
        <p role="status" className="mt-3 text-center text-[15px] font-semibold" style={{ color: OPS.gold }}>
          Queued as {assigned}
        </p>
      ) : null}
    </Card>
  );
}

function Slot({ label, item, big = false }: { label: string; item: QueueItem | null; big?: boolean }) {
  return (
    <div className="mb-3 rounded-lg border px-4 py-3" style={{ borderColor: OPS.border }}>
      <p className="text-[11px] font-semibold tracking-widest" style={{ color: OPS.textFaint }}>
        {label}
      </p>
      <p
        className="mt-1 font-semibold"
        style={{ color: item ? OPS.text : OPS.textFaint, fontSize: big ? "26px" : "19px", lineHeight: 1.25 }}
      >
        {item ? queueLabel(item) : "—"}
      </p>
    </div>
  );
}

const PROCESSING_TONE = { QUEUED: "neutral", PROCESSING: "gold", READY: "good", FAILED: "warn" } as const;

function ListCard({
  title,
  items,
  empty,
  showProcessing = false,
  showDetail = false,
  muted = false,
  note,
}: {
  title: string;
  items: QueueItem[] | undefined;
  empty: string;
  showProcessing?: boolean;
  showDetail?: boolean;
  muted?: boolean;
  note?: ReactNode;
}) {
  return (
    <Card>
      <CardTitle>{title}</CardTitle>
      {note ? (
        <p className="-mt-2 mb-3 text-[12px]" style={{ color: OPS.textMuted }}>
          {note}
        </p>
      ) : null}
      {!items ? (
        <EmptyState>Loading…</EmptyState>
      ) : items.length === 0 ? (
        <EmptyState>{empty}</EmptyState>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {items.map((i) => (
            <li
              key={i.run_id}
              className="flex items-center justify-between gap-3 text-[15px]"
              style={{ color: muted ? OPS.textFaint : OPS.text }}
            >
              <span className="min-w-0 truncate">
                <span className="font-semibold">{formatQueueNumber(i.queue_number)}</span>
                {i.pet_name ? ` ${i.pet_name}` : ""}
              </span>
              {showDetail && i.detail ? (
                <span className="shrink-0 text-[12px]" style={{ color: OPS.textMuted }}>
                  {i.detail}
                </span>
              ) : null}
              {showProcessing ? (
                <Pill tone={PROCESSING_TONE[i.processing_status] ?? "neutral"}>
                  {i.processing_status === "READY" ? "SENDING TO DISPLAY" : i.processing_status}
                </Pill>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
