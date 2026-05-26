/**
 * 모바일 Safari / Chrome — 숨김 file input + label 조합이 안 열리는 경우가 많아
 * 사용자 탭 직후 body에 input을 붙였다가 click() 하는 방식을 씁니다.
 */
export type PickedMedia = {
  file: File;
  kind: "image" | "video";
};

const ACCEPT =
  "image/*,image/heic,image/heif,video/mp4,video/webm,video/quicktime,.heic,.heif";

function inferKind(file: File): "image" | "video" | null {
  const type = (file.type || "").toLowerCase();
  if (type.startsWith("image/")) return "image";
  if (type.startsWith("video/")) return "video";
  const name = (file.name || "").toLowerCase();
  if (/\.(jpe?g|png|gif|webp|heic|heif|bmp)$/i.test(name)) return "image";
  if (/\.(mp4|webm|mov|m4v)$/i.test(name)) return "video";
  return null;
}

export function pickMediaFile(): Promise<PickedMedia | null> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ACCEPT;
    input.multiple = false;

    input.style.cssText =
      "position:fixed;left:-9999px;top:0;width:1px;height:1px;opacity:0;pointer-events:none;";

    let settled = false;
    const finish = (value: PickedMedia | null) => {
      if (settled) return;
      settled = true;
      input.remove();
      resolve(value);
    };

    const cancelTimer = window.setTimeout(() => finish(null), 30_000);

    input.addEventListener(
      "change",
      () => {
        window.clearTimeout(cancelTimer);
        const file = input.files?.[0];
        if (!file) {
          finish(null);
          return;
        }
        const kind = inferKind(file);
        if (!kind) {
          finish(null);
          return;
        }
        finish({ file, kind });
      },
      { once: true },
    );

    input.addEventListener(
      "cancel",
      () => {
        window.clearTimeout(cancelTimer);
        finish(null);
      },
      { once: true },
    );

    document.body.appendChild(input);
    input.click();
  });
}
