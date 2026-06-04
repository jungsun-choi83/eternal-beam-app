"use client";

import { Check, X, Dog } from "lucide-react";
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

      <p className="upload-guide-body">{g.body}</p>

      <div className="upload-guide-cards">
        <div className="guide-card guide-card--good">
          <span className="guide-card__badge guide-card__badge--good" aria-hidden>
            <Check className="w-3.5 h-3.5" strokeWidth={2.5} />
          </span>
          <div className="guide-card__thumb">
            <Dog className="w-10 h-10" strokeWidth={1.25} />
          </div>
          <span className="guide-card__label guide-card__label--good">
            {g.goodLabel}
          </span>
        </div>

        <div className="guide-card guide-card--bad">
          <span className="guide-card__badge guide-card__badge--bad" aria-hidden>
            <X className="w-3.5 h-3.5" strokeWidth={2.5} />
          </span>
          <div className="guide-card__thumb">
            <Dog className="w-10 h-10" strokeWidth={1.25} />
          </div>
          <span className="guide-card__label guide-card__label--bad">
            {g.badLabel}
          </span>
        </div>
      </div>
    </section>
  );
}
