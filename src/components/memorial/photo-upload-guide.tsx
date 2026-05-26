"use client";

import { Check, X } from "lucide-react";
import { memorialT } from "@/components/memorial/memorial-i18n";

interface PhotoUploadGuideProps {
  language?: string;
}

export function PhotoUploadGuide({ language = "ko" }: PhotoUploadGuideProps) {
  const g = memorialT(language).upload.guide;

  return (
    <section className="upload-guide mb-5" aria-labelledby="upload-guide-title">
      <h3 id="upload-guide-title" className="upload-guide-title">
        {g.title}
      </h3>

      <p className="upload-guide-quote">&ldquo;{g.leashQuote}&rdquo;</p>

      <p className="upload-guide-body">{g.body}</p>

      <ul className="upload-guide-list">
        <li className="upload-guide-item upload-guide-item--good">
          <span className="upload-guide-badge upload-guide-badge--good" aria-hidden>
            <Check className="w-3.5 h-3.5" strokeWidth={2.5} />
          </span>
          <span>
            <span className="upload-guide-label upload-guide-label--good">O ({g.goodLabel})</span>
            <span className="upload-guide-text">{g.good}</span>
          </span>
        </li>
        <li className="upload-guide-item upload-guide-item--bad">
          <span className="upload-guide-badge upload-guide-badge--bad" aria-hidden>
            <X className="w-3.5 h-3.5" strokeWidth={2.5} />
          </span>
          <span>
            <span className="upload-guide-label upload-guide-label--bad">X ({g.badLabel})</span>
            <span className="upload-guide-text">{g.bad}</span>
          </span>
        </li>
      </ul>
    </section>
  );
}
