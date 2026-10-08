import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { MediaFileTrigger } from "./media-file-trigger";

/**
 * Regression coverage for the production `insertBefore` DOM crash.
 *
 * The crash happens when React tries to reconcile a subtree whose live DOM
 * no longer matches what React expects (a Chrome extension/translate touched
 * a nearby node). The defense is structural: the <label> must keep the same
 * two direct children — the real file <input> and one permanent content
 * wrapper — no matter what content flows through `children`.
 */
describe("MediaFileTrigger DOM stability", () => {
  it("keeps the same <label> direct-child structure (input + wrapper) across content changes", () => {
    const { container, rerender } = render(
      <MediaFileTrigger>
        <div data-testid="empty-state">empty</div>
      </MediaFileTrigger>,
    );

    const label = container.querySelector("label");
    expect(label).not.toBeNull();
    const childrenBefore = Array.from(label!.children);
    expect(childrenBefore).toHaveLength(2);

    const input = childrenBefore[0];
    const wrapper = childrenBefore[1];
    expect(input!.tagName).toBe("INPUT");
    expect((input as HTMLInputElement).type).toBe("file");

    // Same identity across a completely different `children` subtree — this
    // is the "hasMedia" transition from the real screen (empty prompt ->
    // photo/video content).
    rerender(
      <MediaFileTrigger>
        <div data-testid="media-state">
          <img alt="" src="blob:mock" />
          <span>badge</span>
        </div>
      </MediaFileTrigger>,
    );

    const childrenAfter = Array.from(label!.children);
    expect(childrenAfter).toHaveLength(2);
    // Same DOM nodes were reused — React patched content in place instead of
    // swapping the label's own child structure.
    expect(childrenAfter[0]).toBe(input);
    expect(childrenAfter[1]).toBe(wrapper);

    // The wrapper is the one place content actually changed.
    expect(wrapper!.querySelector('[data-testid="empty-state"]')).toBeNull();
    expect(wrapper!.querySelector('[data-testid="media-state"]')).not.toBeNull();
  });

  it("keeps the file input mounted and functional while `disabled` toggles", () => {
    const { container, rerender } = render(
      <MediaFileTrigger disabled={false}>
        <div>content</div>
      </MediaFileTrigger>,
    );
    const inputBefore = container.querySelector("input[type='file']");
    expect(inputBefore).not.toBeNull();
    expect((inputBefore as HTMLInputElement).disabled).toBe(false);

    rerender(
      <MediaFileTrigger disabled={true}>
        <div>content</div>
      </MediaFileTrigger>,
    );
    const inputAfter = container.querySelector("input[type='file']");
    // Same node — disabling never remounts the input.
    expect(inputAfter).toBe(inputBefore);
    expect((inputAfter as HTMLInputElement).disabled).toBe(true);
  });
});
