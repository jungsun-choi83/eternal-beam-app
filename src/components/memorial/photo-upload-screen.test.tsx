import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, fireEvent, screen, cleanup, waitForElementToBeRemoved } from "@testing-library/react";
import { afterEach } from "vitest";
import { PhotoUploadScreen } from "./photo-upload-screen";

// Unrelated network/instrumentation side effects — not what this suite covers.
vi.mock("@/lib/video-api-warmup", () => ({
  warmupVideoApi: vi.fn(async () => true),
}));
vi.mock("@/lib/image-trace", () => ({
  traceImage: vi.fn(async () => {}),
}));

afterEach(() => cleanup());

function makeImageFile(name: string, type = "image/png") {
  return new File([new Uint8Array([1, 2, 3])], name, { type });
}

function setInputFiles(input: HTMLInputElement, files: File[]) {
  Object.defineProperty(input, "files", { value: files, configurable: true });
  fireEvent.change(input);
}

/** Owns the pet-slot state a real parent (EternalBeamApp) would hold. */
function Harness() {
  const [uploadedImages, setUploadedImages] = useState<string[]>([]);
  let counter = 0;

  return (
    <PhotoUploadScreen
      language="en"
      uploadedImage={uploadedImages[0] ?? null}
      uploadedImages={uploadedImages}
      onImageUpload={(url) => setUploadedImages([url])}
      onImagesUpload={(files) => {
        const urls = files.map((f) => `blob:mock/${f.name}/${counter++}`);
        setUploadedImages((prev) => [...prev, ...urls].slice(0, 3));
      }}
      onReplaceImage={(index, file) => {
        setUploadedImages((prev) => {
          const next = [...prev];
          next[index] = `blob:mock/${file.name}/${counter++}`;
          return next;
        });
      }}
      onRemoveImage={(index) => {
        setUploadedImages((prev) => prev.filter((_, i) => i !== index));
      }}
      maxImages={3}
      onContinue={() => {}}
      onBack={() => {}}
    />
  );
}

function mainFileInput(container: HTMLElement) {
  const label = container.querySelector(".pet-intake__dropzone")!.closest("label")!;
  return label.querySelector<HTMLInputElement>("input[type='file']")!;
}

describe("PhotoUploadScreen upload DOM", () => {
  it("empty -> image selected", () => {
    const { container } = render(<Harness />);
    expect(screen.getByText("Choose from gallery")).toBeTruthy();

    const input = mainFileInput(container);
    setInputFiles(input, [makeImageFile("a.png")]);

    expect(container.querySelectorAll(".pet-intake__thumb")).toHaveLength(1);
    expect(container.querySelector(".pet-intake__media-check")).not.toBeNull();
  });

  it("replaces an image in place without remounting the thumbnail node", () => {
    const { container } = render(<Harness />);
    const input = mainFileInput(container);
    setInputFiles(input, [makeImageFile("a.png")]);

    const thumbBefore = container.querySelector(".pet-intake__thumb");
    expect(thumbBefore).not.toBeNull();
    const imgBefore = thumbBefore!.querySelector("img") as HTMLImageElement;

    const replaceInput = container.querySelector<HTMLInputElement>(
      ".pet-intake__thumb-btn input[type='file']",
    )!;
    setInputFiles(replaceInput, [makeImageFile("b.png")]);

    const thumbAfter = container.querySelector(".pet-intake__thumb");
    expect(thumbAfter).toBe(thumbBefore); // same slot node, not remounted
    const imgAfter = thumbAfter!.querySelector("img") as HTMLImageElement;
    expect(imgAfter).toBe(imgBefore); // same <img>, src just updated
    expect(imgAfter.src).toContain("b.png");
  });

  it("renders multiple thumbnails and keeps earlier slots stable on removal", () => {
    const { container } = render(<Harness />);
    const input = mainFileInput(container);
    setInputFiles(input, [
      makeImageFile("a.png"),
      makeImageFile("b.png"),
      makeImageFile("c.png"),
    ]);

    const thumbs = container.querySelectorAll(".pet-intake__thumb");
    expect(thumbs).toHaveLength(3);
    const firstSlotNode = thumbs[0];

    const removeSecond = screen.getByLabelText("Remove photo 2");
    fireEvent.click(removeSecond);

    const thumbsAfter = container.querySelectorAll(".pet-intake__thumb");
    expect(thumbsAfter).toHaveLength(2);
    // Slot 0 was untouched by removing slot 1 — same DOM node.
    expect(thumbsAfter[0]).toBe(firstSlotNode);
  });

  it("mounts and unmounts the feedback/error banner without crashing", async () => {
    const { container } = render(<Harness />);
    const input = mainFileInput(container);

    setInputFiles(input, [makeImageFile("bad.pdf", "application/pdf")]);

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Only JPG, PNG, or HEIC");

    const dismiss = screen.getByLabelText("Dismiss");
    fireEvent.click(dismiss);

    // framer-motion keeps the node mounted for its exit transition — wait
    // for that to finish rather than asserting immediate removal.
    await waitForElementToBeRemoved(() => screen.queryByRole("alert"), {
      timeout: 3000,
    });
    // The dropzone survived the banner's mount/unmount cycle.
    expect(container.querySelector(".pet-intake__dropzone")).not.toBeNull();
  });

  it("keeps the MediaFileTrigger label's direct children stable across the hasMedia transition", () => {
    const { container } = render(<Harness />);
    const label = container
      .querySelector(".pet-intake__dropzone")!
      .closest("label")!;
    const childrenBefore = Array.from(label.children);
    expect(childrenBefore).toHaveLength(2);
    const [inputBefore, wrapperBefore] = childrenBefore;

    const input = mainFileInput(container);
    setInputFiles(input, [makeImageFile("a.png")]);

    const childrenAfter = Array.from(label.children);
    expect(childrenAfter).toHaveLength(2);
    expect(childrenAfter[0]).toBe(inputBefore);
    expect(childrenAfter[1]).toBe(wrapperBefore);
  });
});

describe("PhotoUploadScreen locked photos (generation started)", () => {
  function LockedHarness({ locked }: { locked: boolean }) {
    return (
      <PhotoUploadScreen
        language="en"
        uploadedImage="blob:mock/a.png"
        uploadedImages={["blob:mock/a.png", "blob:mock/b.png"]}
        onImageUpload={() => {}}
        onImagesUpload={() => {}}
        onReplaceImage={() => {}}
        onRemoveImage={() => {}}
        maxImages={3}
        photosLocked={locked}
        onContinue={() => {}}
        onBack={() => {}}
      />
    );
  }

  it("before generation: add, replace and remove are all available", () => {
    const { container } = render(<LockedHarness locked={false} />);
    expect(screen.queryByText(/can no longer be added, removed or replaced/)).toBeNull();
    expect(container.querySelectorAll(".pet-intake__thumb")).toHaveLength(2);
    // 장마다 교체 + 삭제 버튼.
    expect(container.querySelectorAll(".pet-intake__thumb-btn")).toHaveLength(4);
    expect(mainFileInput(container).disabled).toBe(false);
  });

  it("after generation started: photos stay visible but cannot be changed", () => {
    const { container } = render(<LockedHarness locked />);
    expect(screen.getByText(/can no longer be added, removed or replaced/)).toBeTruthy();
    expect(container.querySelectorAll(".pet-intake__thumb")).toHaveLength(2);
    expect(container.querySelectorAll(".pet-intake__thumb-btn")).toHaveLength(0);
    expect(mainFileInput(container).disabled).toBe(true);
  });
});

// ── 멀티 포토: 한 펫의 사진 배열 / 펫 사이의 격리 ───────────────────────────

/**
 * EternalBeamApp 의 펫 슬롯 상태를 흉내 낸 부모. 규칙은 앱과 같다:
 *   - 펫마다 자기 사진 배열 하나 (petSlots[i].uploadedImages)
 *   - 사진 추가는 **활성 펫의 배열에** 붙는다 (handleImagesUpload)
 *   - 펫 전환은 활성 번호만 바꾼다 (selectPetSlot) — 배열은 건드리지 않는다
 *   - Start 는 활성 펫의 배열을 처리 화면에 넘긴다 (uploadedImages={activePetSlot.uploadedImages})
 * (앱 쪽 배선은 src/lib/multi-photo-submission.test.ts 가 소스로 고정한다.)
 */
function MultiPetHarness({ onStart }: { onStart: (petIndex: number, photos: string[]) => void }) {
  const [slots, setSlots] = useState<string[][]>([[]]);
  const [active, setActive] = useState(0);
  const photos = slots[active] ?? [];
  return (
    <PhotoUploadScreen
      language="en"
      uploadedImage={photos[0] ?? null}
      uploadedImages={photos}
      petSlotsCount={slots.length}
      activePetSlotIndex={active}
      maxPetSlots={3}
      onSelectPetSlot={setActive}
      onAddPetSlot={() => {
        setSlots((prev) => [...prev, []]);
        setActive(slots.length);
      }}
      onImageUpload={() => {}}
      onImagesUpload={(files) => {
        const index = active;
        setSlots((prev) =>
          prev.map((slot, i) => (i === index ? [...slot, ...files.map((f) => f.name)].slice(0, 3) : slot)),
        );
      }}
      onRemoveImage={() => {}}
      onReplaceImage={() => {}}
      maxImages={3}
      onContinue={() => onStart(active, photos)}
      onBack={() => {}}
    />
  );
}

function addPhotoInput(container: HTMLElement) {
  return container.querySelector<HTMLInputElement>(".pet-intake__add-photo input[type='file']");
}

function thumbs(container: HTMLElement) {
  return container.querySelectorAll(".pet-intake__thumb").length;
}

describe("PhotoUploadScreen multi-photo per pet", () => {
  it("adds a 2nd and 3rd photo to the SAME pet through the add-photo tile, and Start submits all of them", () => {
    const onStart = vi.fn();
    const { container } = render(<MultiPetHarness onStart={onStart} />);

    setInputFiles(mainFileInput(container), [makeImageFile("A.png")]);
    expect(thumbs(container)).toBe(1);
    fireEvent.click(screen.getByText("Start"));
    expect(onStart).toHaveBeenLastCalledWith(0, ["A.png"]);

    // 사진이 한 장 있으면 "같은 아이에 사진 추가" 타일이 보인다.
    expect(screen.getByText("Add photo")).toBeTruthy();
    setInputFiles(addPhotoInput(container)!, [makeImageFile("B.png")]);
    expect(thumbs(container)).toBe(2);
    fireEvent.click(screen.getByText("Start"));
    expect(onStart).toHaveBeenLastCalledWith(0, ["A.png", "B.png"]);

    setInputFiles(addPhotoInput(container)!, [makeImageFile("C.png")]);
    expect(thumbs(container)).toBe(3);
    fireEvent.click(screen.getByText("Start"));
    expect(onStart).toHaveBeenLastCalledWith(0, ["A.png", "B.png", "C.png"]);

    // 여전히 펫은 하나다 — 사진을 더했다고 펫이 늘지 않는다.
    expect(container.querySelectorAll(".pet-intake__tab")).toHaveLength(1);
    // 3장이 차면 추가 타일은 사라진다.
    expect(addPhotoInput(container)).toBeNull();
    expect(screen.queryByText("Add photo")).toBeNull();
  });

  it("several files picked at once all go to the active pet", () => {
    const onStart = vi.fn();
    const { container } = render(<MultiPetHarness onStart={onStart} />);
    setInputFiles(mainFileInput(container), [makeImageFile("A.png"), makeImageFile("B.png"), makeImageFile("C.png")]);
    fireEvent.click(screen.getByText("Start"));
    expect(onStart).toHaveBeenLastCalledWith(0, ["A.png", "B.png", "C.png"]);
  });

  it("each pet keeps its own photos: Pet 1's never enter Pet 2's submission, and switching preserves both", () => {
    const onStart = vi.fn();
    const { container } = render(<MultiPetHarness onStart={onStart} />);

    setInputFiles(mainFileInput(container), [makeImageFile("A.png"), makeImageFile("B.png"), makeImageFile("C.png")]);
    expect(thumbs(container)).toBe(3);

    // "Add pet" 는 **새 펫**을 만든다 — 사진을 더하는 버튼이 아니다.
    fireEvent.click(screen.getByText("Add pet"));
    expect(container.querySelectorAll(".pet-intake__tab")).toHaveLength(2);
    expect(thumbs(container)).toBe(0);
    setInputFiles(mainFileInput(container), [makeImageFile("D.png")]);
    expect(thumbs(container)).toBe(1);
    fireEvent.click(screen.getByText("Start"));
    expect(onStart).toHaveBeenLastCalledWith(1, ["D.png"]);

    // 펫 1로 돌아가면 세 장이 그대로다.
    fireEvent.click(screen.getByText("Pet 1"));
    expect(thumbs(container)).toBe(3);
    fireEvent.click(screen.getByText("Start"));
    expect(onStart).toHaveBeenLastCalledWith(0, ["A.png", "B.png", "C.png"]);

    // 다시 펫 2 — 한 장 그대로, 펫 1의 사진은 섞이지 않았다.
    fireEvent.click(screen.getByText("Pet 2"));
    expect(thumbs(container)).toBe(1);
    setInputFiles(addPhotoInput(container)!, [makeImageFile("E.png")]);
    fireEvent.click(screen.getByText("Start"));
    expect(onStart).toHaveBeenLastCalledWith(1, ["D.png", "E.png"]);

    fireEvent.click(screen.getByText("Pet 1"));
    fireEvent.click(screen.getByText("Start"));
    expect(onStart).toHaveBeenLastCalledWith(0, ["A.png", "B.png", "C.png"]);
  });

  it("the add-photo tile is not offered for a locked pet or an empty one", () => {
    const { container, rerender } = render(
      <PhotoUploadScreen
        language="en"
        uploadedImage="blob:mock/a.png"
        uploadedImages={["blob:mock/a.png"]}
        onImageUpload={() => {}}
        onImagesUpload={() => {}}
        maxImages={3}
        photosLocked
        onContinue={() => {}}
        onBack={() => {}}
      />,
    );
    expect(addPhotoInput(container)).toBeNull();

    rerender(
      <PhotoUploadScreen
        language="en"
        uploadedImage={null}
        uploadedImages={[]}
        onImageUpload={() => {}}
        onImagesUpload={() => {}}
        maxImages={3}
        onContinue={() => {}}
        onBack={() => {}}
      />,
    );
    // 빈 자리에서는 큰 선택 영역이 그 역할을 한다.
    expect(addPhotoInput(container)).toBeNull();
  });
});
