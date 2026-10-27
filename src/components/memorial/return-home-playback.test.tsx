import { createRef } from "react";
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { PetRuntimeTrigger } from "@/lib/pet-runtime-events";
import { IdleLoopVideo } from "./idle-loop-video";

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  // These tests drive media events; canvas drawing is independent of chaining.
  vi.spyOn(window, "requestAnimationFrame").mockReturnValue(1);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function setup(returnHome: string | null) {
  const trigger = createRef<PetRuntimeTrigger>();
  const changed = vi.fn();
  const view = (url: string | null) => <IdleLoopVideo
    src="/breathing.mp4" deliveryFormat="packed_alpha"
    eventSources={{ COME_CLOSER: "/closer.mp4", RETURN_HOME: url }}
    actionTriggerRef={trigger} onRuntimeEventChange={changed}
  />;
  const result = render(view(returnHome));
  const video = (id: string) => result.container.querySelector<HTMLVideoElement>(`video[data-action="${id}"]`);
  return { ...result, trigger, changed, video, view };
}

it("mounts and preloads a late RETURN_HOME URL without autoplay", () => {
  const h = setup(null);
  expect(h.video("RETURN_HOME")).toBeNull();
  h.rerender(h.view("/home.mp4"));
  const home = h.video("RETURN_HOME")!;
  expect(home.preload).toBe("auto");
  expect(home.autoplay).toBe(false);
  expect(home.paused).toBe(true);
  expect(vi.mocked(home.play).mock.contexts).not.toContain(home);
});

it("plays COME_CLOSER → buffered RETURN_HOME → BREATHING", async () => {
  const h = setup("/home.mp4");
  Object.defineProperty(h.video("RETURN_HOME"), "readyState", { value: 4 });
  await act(async () => { h.trigger.current?.("COME_CLOSER"); });
  fireEvent.ended(h.video("COME_CLOSER")!);
  expect(h.changed.mock.calls.map(([id]) => id)).toEqual(["COME_CLOSER", "RETURN_HOME"]);
  fireEvent.ended(h.video("RETURN_HOME")!);
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  expect(h.changed).toHaveBeenLastCalledWith(null);
});

it.each(["missing", "failed", "unbuffered"])("keeps hold-and-dissolve when RETURN_HOME is %s", async (state) => {
  const h = setup(state === "missing" ? null : "/home.mp4");
  if (state === "failed") {
    Object.defineProperty(h.video("RETURN_HOME"), "readyState", { value: 4 });
    Object.defineProperty(h.video("RETURN_HOME"), "error", { value: { code: 3 } });
  }
  await act(async () => { h.trigger.current?.("COME_CLOSER"); });
  const closer = h.video("COME_CLOSER")!;
  closer.currentTime = 4;
  fireEvent.ended(closer);
  expect(h.changed.mock.calls.map(([id]) => id)).toEqual(["COME_CLOSER"]);
  expect(closer.currentTime).toBe(4); // Last frame remains held, not rewound.
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(h.changed).toHaveBeenLastCalledWith("COME_CLOSER");
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  expect(h.changed).toHaveBeenLastCalledWith(null);
});
