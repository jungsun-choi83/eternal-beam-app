"use client";

/**
 * /exhibition/queue — 공개 대기열 화면 (현장 모니터·방문자 폰).
 *
 * NOW SHOWING 한 건과 UP NEXT 목록만 그린다. 인증 없음, 관리 기능 없음 —
 * 서버도 번호와 이름만 돌려준다. App.tsx 가 EternalBeamApp **바깥**에서 분기하므로
 * 고객 앱의 인증 부팅·폴링·기기 동기화는 마운트되지 않는다.
 */

import { useEffect, useRef, useState } from "react";

import {
  PUBLIC_POLL_MS,
  currentExhibitionId,
  fetchPublicQueue,
  queueLabel,
  type PublicQueue,
} from "@/lib/exhibition-queue";

const C = { bg: "#141210", text: "#F4EFE6", faint: "#8E867C", gold: "#D8B65A", line: "#2C2824" } as const;

export function ExhibitionQueueScreen({ exhibitionId = currentExhibitionId() }: { exhibitionId?: string | null }) {
  const [queue, setQueue] = useState<PublicQueue | null>(null);
  const [offline, setOffline] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    const load = async () => {
      try {
        const q = await fetchPublicQueue(exhibitionId);
        if (!mounted.current) return;
        setQueue(q);
        setOffline(false);
      } catch {
        // 마지막으로 받은 화면을 그대로 둔다 — 일시적 오류에 빈 화면을 띄우지 않는다.
        if (mounted.current) setOffline(true);
      }
    };
    void load();
    const id = window.setInterval(() => void load(), PUBLIC_POLL_MS);
    return () => {
      mounted.current = false;
      window.clearInterval(id);
    };
  }, [exhibitionId]);

  return (
    <div
      className="flex min-h-[100dvh] w-full flex-col items-center justify-center px-6 py-10"
      style={{ background: C.bg, color: C.text }}
    >
      <section className="w-full max-w-xl text-center">
        <h1 className="text-[13px] font-semibold tracking-[0.3em]" style={{ color: C.faint }}>
          NOW SHOWING
        </h1>
        <p className="mt-3 font-semibold" style={{ fontSize: "clamp(36px, 9vw, 72px)", lineHeight: 1.1 }}>
          {queue?.now_showing ? queueLabel(queue.now_showing) : "—"}
        </p>
      </section>

      <div className="my-10 h-px w-full max-w-xl" style={{ background: C.line }} />

      <section className="w-full max-w-xl text-center">
        <h2 className="text-[13px] font-semibold tracking-[0.3em]" style={{ color: C.faint }}>
          UP NEXT
        </h2>
        {queue && queue.up_next.length ? (
          <ol className="mt-4 flex flex-col gap-2">
            {queue.up_next.map((item, i) => (
              <li
                key={item.queue_number}
                className="font-semibold"
                style={{ color: i === 0 ? C.gold : C.text, fontSize: i === 0 ? "30px" : "22px" }}
              >
                {queueLabel(item)}
              </li>
            ))}
          </ol>
        ) : (
          <p className="mt-4 text-[20px]" style={{ color: C.faint }}>
            {queue ? "—" : "Loading…"}
          </p>
        )}
      </section>

      {offline ? (
        <p className="mt-10 text-[12px]" style={{ color: C.faint }}>
          Reconnecting…
        </p>
      ) : null}
    </div>
  );
}
