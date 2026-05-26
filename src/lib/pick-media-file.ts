import { inferMediaKind, MEDIA_FILE_ACCEPT, type MediaKind } from "./media-file-kind";

export type PickedMedia = {
  file: File;
  kind: MediaKind;
};

/** 레거시 — 가능하면 MediaFileTrigger 사용 */
export function pickMediaFile(): Promise<PickedMedia | null> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = MEDIA_FILE_ACCEPT;
    input.multiple = false;
    input.style.cssText =
      "position:fixed;inset:0;width:100%;height:100%;opacity:0.02;z-index:2147483647;";

    let settled = false;
    const finish = (value: PickedMedia | null) => {
      if (settled) return;
      settled = true;
      input.remove();
      resolve(value);
    };

    const cancelTimer = window.setTimeout(() => finish(null), 120_000);

    input.addEventListener(
      "change",
      () => {
        window.clearTimeout(cancelTimer);
        const file = input.files?.[0];
        if (!file) {
          finish(null);
          return;
        }
        const kind = inferMediaKind(file);
        if (!kind) {
          finish(null);
          return;
        }
        finish({ file, kind });
      },
      { once: true },
    );

    document.body.appendChild(input);
    input.click();
  });
}
