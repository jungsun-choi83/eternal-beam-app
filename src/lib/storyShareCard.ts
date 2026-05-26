/**
 * Renders a 9:16 (1080×1920) PNG for Instagram Story–style sharing.
 * Emphasizes one line + child name; minimal visual noise.
 */

const W = 1080;
const H = 1920;

const BG_TOP = "#151311";
const BG_BOTTOM = "#0a0908";
const GOLD_MAIN = "#e6d5b8";
const GOLD_MUTED = "rgba(198, 182, 154, 0.38)";
const FONT_SERIF = '"Noto Serif KR", "Times New Roman", serif';

function wrapKoreanLines(
  ctx: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
  maxLines: number
): string[] {
  const t = text.trim();
  const lines: string[] = [];
  let i = 0;
  while (i < t.length && lines.length < maxLines) {
    let line = "";
    while (i < t.length) {
      const next = line + t[i];
      if (ctx.measureText(next).width > maxWidth && line.length > 0) break;
      line = next;
      i++;
    }
    const isLastAllowed = lines.length === maxLines - 1;
    if (isLastAllowed && i < t.length) {
      let ell = line;
      while (ell.length > 0 && ctx.measureText(`${ell}…`).width > maxWidth) {
        ell = ell.slice(0, -1);
      }
      lines.push(`${ell}…`);
      break;
    }
    lines.push(line);
  }
  return lines;
}

async function ensureFonts(): Promise<void> {
  if (typeof document === "undefined" || !document.fonts?.load) return;
  try {
    await document.fonts.load(`600 64px ${FONT_SERIF}`);
    await document.fonts.load(`400 28px ${FONT_SERIF}`);
  } catch {
    /* ignore */
  }
}

export interface StoryShareCardOptions {
  childName: string;
  /** Single most impactful line (Korean or mixed). */
  impactLine: string;
  /** Optional faint footer (e.g. brand). */
  footer?: string;
}

export async function renderStorySharePng(
  opts: StoryShareCardOptions
): Promise<Blob> {
  await ensureFonts();

  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas 2D not available");

  const grd = ctx.createLinearGradient(0, 0, 0, H);
  grd.addColorStop(0, BG_TOP);
  grd.addColorStop(1, BG_BOTTOM);
  ctx.fillStyle = grd;
  ctx.fillRect(0, 0, W, H);

  // Subtle vignette
  const vg = ctx.createRadialGradient(W / 2, H * 0.45, 200, W / 2, H * 0.45, H * 0.85);
  vg.addColorStop(0, "rgba(0,0,0,0)");
  vg.addColorStop(1, "rgba(0,0,0,0.55)");
  ctx.fillStyle = vg;
  ctx.fillRect(0, 0, W, H);

  // Fine grain (deterministic dots)
  ctx.globalAlpha = 0.04;
  ctx.fillStyle = "#fff";
  for (let y = 0; y < H; y += 4) {
    for (let x = 0; x < W; x += 4) {
      if ((x * 13 + y * 7) % 11 === 0) ctx.fillRect(x, y, 1, 1);
    }
  }
  ctx.globalAlpha = 1;

  const padX = 88;
  const maxTextW = W - padX * 2;

  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  ctx.font = `500 22px ${FONT_SERIF}`;
  ctx.fillStyle = GOLD_MUTED;
  ctx.fillText("SOUL TRACE", W / 2, 120);

  const impact = opts.impactLine.trim();
  const fontSize = impact.length > 42 ? 52 : impact.length > 28 ? 58 : 64;
  ctx.font = `600 ${fontSize}px ${FONT_SERIF}`;
  ctx.fillStyle = GOLD_MAIN;

  const lines = wrapKoreanLines(ctx, impact, maxTextW, 6);
  const lineHeight = fontSize * 1.35;
  const blockH = lines.length * lineHeight;
  const centerY = H * 0.42;
  let y = centerY - blockH / 2 + lineHeight / 2;
  for (const line of lines) {
    ctx.fillText(line, W / 2, y);
    y += lineHeight;
  }

  ctx.font = `400 28px ${FONT_SERIF}`;
  ctx.fillStyle = "rgba(230, 213, 184, 0.55)";
  ctx.fillText(opts.childName.trim(), W / 2, H * 0.72);

  const foot = opts.footer?.trim();
  if (foot) {
    ctx.font = `400 20px ${FONT_SERIF}`;
    ctx.fillStyle = "rgba(198, 182, 154, 0.35)";
    ctx.fillText(foot, W / 2, H - 100);
  }

  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error("toBlob failed"))),
      "image/png",
      0.92
    );
  });
}
