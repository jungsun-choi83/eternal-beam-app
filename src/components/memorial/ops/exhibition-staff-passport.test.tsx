import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./ops-layout", () => ({ OpsLayout: () => null }));

import { StaffBody } from "./exhibition-staff-screen";

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

function emptyQueue() {
  return {
    exhibition_id: "expo-1",
    now_showing: null,
    up_next: null,
    ready: [],
    preparing: [],
    needs_attention: [],
    recently_complete: [],
    handoff_required: true,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Passport queue number guard in Exhibition Ops", () => {
  it("shows validation feedback for a non-positive-integer Passport number", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(emptyQueue())));
    render(<StaffBody token="t" onAuthError={vi.fn()} exhibitionId="expo-1" />);

    await screen.findByText("No ready pets waiting.");
    const input = screen.getByPlaceholderText("Leave blank for legacy staff mode");
    fireEvent.change(input, { target: { value: "12x" } });

    expect(screen.getByRole("alert").textContent).toContain("positive whole number");
    expect((screen.getByRole("button", { name: "Add to queue" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("sends a Passport number through the explicit association payload", async () => {
    const fetchFn = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) =>
      String(input).endsWith("/submissions")
        ? json({
            run_id: "run-42",
            exhibition_id: "expo-1",
            queue_number: 42,
            pet_name: "Coco",
            created_at: null,
            display_status: "WAITING",
            processing_status: "QUEUED",
          })
        : json(emptyQueue())
    );
    vi.stubGlobal("fetch", fetchFn);
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:preview");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    render(<StaffBody token="t" onAuthError={vi.fn()} exhibitionId="expo-1" />);

    await screen.findByText("No ready pets waiting.");
    fireEvent.change(screen.getByLabelText("Pet photo"), {
      target: { files: [new File(["photo"], "coco.jpg", { type: "image/jpeg" })] },
    });
    fireEvent.change(screen.getByPlaceholderText("Leave blank for legacy staff mode"), {
      target: { value: "42" },
    });

    expect(screen.getByText(/server will verify this Passport number/i)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Add to queue" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Add to queue" }));

    expect(await screen.findByRole("status")).toHaveProperty("textContent", "Queued as #042 Coco");
    const post = fetchFn.mock.calls.find(([url]) => String(url).endsWith("/submissions"))!;
    const form = post[1]?.body as FormData;
    expect(form.get("queue_number")).toBe("42");
    expect(form.get("exhibition_id")).toBe("expo-1");
  });
});
