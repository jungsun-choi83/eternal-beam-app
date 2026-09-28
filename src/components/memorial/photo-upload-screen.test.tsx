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
