/**
 * 전시 스태프 화면 · 공개 대기열 화면.
 *
 * 서버 응답은 fetch 스텁으로 흉내 낸다. 상태 규칙(READY 만 · SHOW NEXT 전이)은 서버
 * (backend/tests/test_exhibition_queue.py)가 지키고, 여기서는 화면이 그것을 그대로
 * 그리고 올바른 요청을 보내는지만 본다.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./ops-layout", () => ({ OpsLayout: () => null }));

import { StaffBody } from "./exhibition-staff-screen";
import { ExhibitionQueueScreen } from "@/components/exhibition/exhibition-queue-screen";
import type { QueueItem, StaffQueue } from "@/lib/exhibition-queue";

function item(n: number, name: string | null, extra: Partial<QueueItem> = {}): QueueItem {
  return {
    run_id: `run-${n}`,
    queue_number: n,
    pet_name: name,
    created_at: null,
    display_status: "WAITING",
    processing_status: "READY",
    handoff_status: "HANDOFF_CONFIRMED",
    detail: null,
    ...extra,
  };
}

function staffQueue(partial: Partial<StaffQueue> = {}): StaffQueue {
  return {
    exhibition_id: "expo-1",
    now_showing: item(18, "Coco", { display_status: "NOW_SHOWING" }),
    up_next: item(19, "Bori", { display_status: "UP_NEXT" }),
    ready: [item(20, "Momo"), item(21, "Leo")],
    preparing: [item(22, "Ruby", { processing_status: "PROCESSING", handoff_status: null })],
    needs_attention: [],
    recently_complete: [],
    handoff_required: true,
    ...partial,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Route = (url: string, init?: RequestInit) => Response | Promise<Response>;

function stubFetch(route: Route) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => route(String(input), init));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ExhibitionStaffScreen", () => {
  it("shows NOW SHOWING / UP NEXT / READY / PREPARING from the server", async () => {
    const fetchFn = stubFetch(() => json(staffQueue()));
    render(<StaffBody token="t" onAuthError={vi.fn()} exhibitionId="expo-1" />);

    expect(await screen.findByText("#018 Coco")).toBeTruthy();
    expect(screen.getByText("#019 Bori")).toBeTruthy();
    expect(screen.getByText("#020")).toBeTruthy();
    expect(screen.getByText("#022")).toBeTruthy();
    expect(screen.getByText("PROCESSING")).toBeTruthy();
    const [url, init] = fetchFn.mock.calls[0];
    expect(String(url)).toBe("/api/exhibition/staff/queue?exhibition_id=expo-1");
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer t");
  });

  it("SHOW NEXT sends the state the screen saw and renders the advanced queue", async () => {
    const advanced = staffQueue({
      now_showing: item(19, "Bori", { display_status: "NOW_SHOWING" }),
      up_next: item(20, "Momo", { display_status: "UP_NEXT" }),
      ready: [item(21, "Leo")],
      recently_complete: [item(18, "Coco", { display_status: "COMPLETE" })],
    });
    const fetchFn = stubFetch((url) => (url.endsWith("/show-next") ? json(advanced) : json(staffQueue())));
    render(<StaffBody token="t" onAuthError={vi.fn()} exhibitionId="expo-1" />);
    await screen.findByText("#018 Coco");

    fireEvent.click(screen.getByRole("button", { name: "SHOW NEXT" }));

    await waitFor(() => expect(screen.getByText("RECENTLY COMPLETE")).toBeTruthy());
    const post = fetchFn.mock.calls.find(([u]) => String(u).endsWith("/show-next"))!;
    expect(post[1]?.method).toBe("POST");
    expect(JSON.parse(String(post[1]?.body))).toEqual({
      exhibition_id: "expo-1",
      expected_now_showing_run_id: "run-18",
      expected_up_next_run_id: "run-19",
    });
    expect(screen.getByText("#019 Bori")).toBeTruthy();
    expect(screen.getByText("#020 Momo")).toBeTruthy();
  });

  it("SHOW NEXT is disabled when nothing is showing or up next", async () => {
    stubFetch(() => json(staffQueue({ now_showing: null, up_next: null, ready: [] })));
    render(<StaffBody token="t" onAuthError={vi.fn()} exhibitionId={null} />);
    await screen.findByText("No ready pets waiting.");
    expect((screen.getByRole("button", { name: "SHOW NEXT" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("a stale double tap is reported and the queue is refreshed", async () => {
    let gets = 0;
    stubFetch((url) => {
      if (url.endsWith("/show-next")) {
        return json({ detail: { code: "QUEUE_STATE_CHANGED", message: "changed" } }, 409);
      }
      gets += 1;
      return json(staffQueue());
    });
    render(<StaffBody token="t" onAuthError={vi.fn()} exhibitionId="expo-1" />);
    await screen.findByText("#018 Coco");
    fireEvent.click(screen.getByRole("button", { name: "SHOW NEXT" }));
    expect(await screen.findByText(/queue changed on another device/i)).toBeTruthy();
    await waitFor(() => expect(gets).toBeGreaterThanOrEqual(2));
  });

  it("failed processing is listed under NEEDS ATTENTION, not READY", async () => {
    stubFetch(() =>
      json(
        staffQueue({
          ready: [],
          needs_attention: [item(23, "Toto", { processing_status: "FAILED", detail: "SUBJECT_NOT_DETECTED" })],
        })
      )
    );
    render(<StaffBody token="t" onAuthError={vi.fn()} exhibitionId="expo-1" />);
    const heading = await screen.findByText("NEEDS ATTENTION");
    const card = heading.closest("section")!;
    expect(within(card).getByText("SUBJECT_NOT_DETECTED")).toBeTruthy();
    expect(screen.getByText("No ready pets waiting.")).toBeTruthy();
  });

  it("submits the photo with the optional pet name and shows the assigned number", async () => {
    // jsdom 의 createObjectURL 은 jsdom File 을 받지 못한다 — 미리보기 URL 만 흉내 낸다.
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:preview");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const fetchFn = stubFetch((url) =>
      url.endsWith("/submissions")
        ? json({ run_id: "run-24", exhibition_id: "expo-1", queue_number: 24, pet_name: "Nabi",
                 created_at: null, display_status: "WAITING", processing_status: "QUEUED" })
        : json(staffQueue())
    );
    render(<StaffBody token="t" onAuthError={vi.fn()} exhibitionId="expo-1" />);
    await screen.findByText("#018 Coco");

    const file = new File([new Uint8Array([1, 2, 3])], "nabi.jpg", { type: "image/jpeg" });
    fireEvent.change(screen.getByLabelText("Pet photo"), { target: { files: [file] } });
    fireEvent.change(screen.getByPlaceholderText("e.g. Coco"), { target: { value: " Nabi " } });
    fireEvent.click(screen.getByRole("button", { name: "Add to queue" }));

    expect(await screen.findByRole("status")).toHaveProperty("textContent", "Queued as #024 Nabi");
    const post = fetchFn.mock.calls.find(([u]) => String(u).endsWith("/submissions"))!;
    const form = post[1]?.body as FormData;
    expect(form.get("pet_name")).toBe("Nabi");
    expect(form.get("exhibition_id")).toBe("expo-1");
    expect((form.get("file") as File).name).toBe("nabi.jpg");
  });

  it("an auth failure goes back to the ops shell", async () => {
    stubFetch(() => json({ detail: "no" }, 401));
    const onAuthError = vi.fn();
    render(<StaffBody token="t" onAuthError={onAuthError} exhibitionId="expo-1" />);
    await waitFor(() => expect(onAuthError).toHaveBeenCalled());
    expect(onAuthError.mock.calls[0][0]).toMatchObject({ code: "UNAUTHENTICATED" });
  });
});

describe("ExhibitionQueueScreen (public)", () => {
  it("shows only NOW SHOWING and UP NEXT, without controls or auth", async () => {
    const fetchFn = stubFetch(() =>
      json({
        now_showing: { queue_number: 21, pet_name: "Coco" },
        up_next: [{ queue_number: 22, pet_name: "Bori" }, { queue_number: 23, pet_name: "Momo" }],
      })
    );
    render(<ExhibitionQueueScreen exhibitionId="expo-1" />);
    expect(await screen.findByText("#021 Coco")).toBeTruthy();
    expect(screen.getByText("#022 Bori")).toBeTruthy();
    expect(screen.getByText("#023 Momo")).toBeTruthy();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByText(/SHOW NEXT|READY|PREPARING/)).toBeNull();
    const [url, init] = fetchFn.mock.calls[0];
    expect(String(url)).toBe("/api/exhibition/queue?exhibition_id=expo-1");
    expect(init?.headers).toBeUndefined();
  });

  it("keeps the last queue on screen when a poll fails", async () => {
    let calls = 0;
    stubFetch(() => {
      calls += 1;
      if (calls > 1) throw new TypeError("network");
      return json({ now_showing: { queue_number: 5, pet_name: null }, up_next: [] });
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<ExhibitionQueueScreen exhibitionId={null} />);
      expect(await screen.findByText("#005")).toBeTruthy();
      await vi.advanceTimersByTimeAsync(6_000);
      expect(await screen.findByText("Reconnecting…")).toBeTruthy();
      expect(screen.getByText("#005")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });
});
