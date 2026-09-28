import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, fireEvent } from "@testing-library/react";
import { PetPhoto, looksTransparent } from "./pet-photo";

afterEach(() => cleanup());

describe("looksTransparent", () => {
  it("treats PNG/WebP data URLs and .png/.webp paths as cutouts", () => {
    expect(looksTransparent("data:image/png;base64,AAAA")).toBe(true);
    expect(looksTransparent("data:image/webp;base64,AAAA")).toBe(true);
    expect(looksTransparent("https://cdn.example.com/pets/1/cutout.PNG?token=x")).toBe(true);
    expect(looksTransparent("/local/cutout.webp#frag")).toBe(true);
  });

  it("treats JPEG data URLs, blob: URLs and .jpg paths as photos", () => {
    expect(looksTransparent("data:image/jpeg;base64,AAAA")).toBe(false);
    expect(looksTransparent("blob:http://localhost/abc-123")).toBe(false);
    expect(looksTransparent("https://cdn.example.com/pets/1/original.jpg")).toBe(false);
  });
});

describe("PetPhoto", () => {
  it("full variant: contain image plus blurred backdrop for a photo", () => {
    const { container } = render(<PetPhoto src="blob:http://localhost/photo" />);
    const frame = container.querySelector(".eb-pet-photo")!;
    expect(frame.classList.contains("eb-pet-photo--avatar")).toBe(false);
    expect(frame.classList.contains("eb-pet-photo--backdrop")).toBe(true);
    expect(container.querySelector(".eb-pet-photo__backdrop")).not.toBeNull();
    expect(container.querySelector(".eb-pet-photo__img")).not.toBeNull();
  });

  it("full variant: no backdrop for a transparent cutout", () => {
    const { container } = render(<PetPhoto src="data:image/png;base64,AAAA" />);
    expect(container.querySelector(".eb-pet-photo__backdrop")).toBeNull();
    expect(container.querySelector(".eb-pet-photo--backdrop")).toBeNull();
  });

  it("backdrop prop overrides the heuristic", () => {
    const { container: on } = render(<PetPhoto src="data:image/png;base64,AAAA" backdrop />);
    expect(on.querySelector(".eb-pet-photo__backdrop")).not.toBeNull();
    const { container: off } = render(<PetPhoto src="blob:http://localhost/photo" backdrop={false} />);
    expect(off.querySelector(".eb-pet-photo__backdrop")).toBeNull();
  });

  it("avatar variant: single cover image, never a backdrop", () => {
    const { container } = render(<PetPhoto src="blob:http://localhost/photo" variant="avatar" />);
    const frame = container.querySelector(".eb-pet-photo")!;
    expect(frame.classList.contains("eb-pet-photo--avatar")).toBe(true);
    expect(container.querySelectorAll("img")).toHaveLength(1);
    expect(container.querySelector(".eb-pet-photo__backdrop")).toBeNull();
  });

  it("reports the natural aspect ratio once the image loads", () => {
    const onAspect = vi.fn();
    const { container } = render(<PetPhoto src="blob:http://localhost/photo" onAspect={onAspect} />);
    const img = container.querySelector<HTMLImageElement>(".eb-pet-photo__img")!;
    Object.defineProperty(img, "naturalWidth", { value: 1200, configurable: true });
    Object.defineProperty(img, "naturalHeight", { value: 1600, configurable: true });
    fireEvent.load(img);
    expect(onAspect).toHaveBeenCalledWith(0.75);
  });

  it("ignores a load event with no dimensions", () => {
    const onAspect = vi.fn();
    const { container } = render(<PetPhoto src="blob:http://localhost/photo" onAspect={onAspect} />);
    fireEvent.load(container.querySelector(".eb-pet-photo__img")!);
    expect(onAspect).not.toHaveBeenCalled();
  });
});
