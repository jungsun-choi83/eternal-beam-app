"use client";

import { memorialLang, memorialT } from "@/components/memorial/memorial-i18n";
import { motion } from "framer-motion";
import {
  ChevronRight,
  Heart,
  House,
  Image as ImageIcon,
  Laptop,
  Palette,
  PawPrint,
  Play,
  Plus,
  Settings,
  Sparkles,
  Star,
} from "lucide-react";
import { useMemo } from "react";
import { HolographicBackground } from "./holographic-background";
import { HologramEffects } from "./hologram-effects";
import { EternalBeamBrandMark } from "./eternal-beam-brand-mark";
import { MediaFileTrigger } from "./media-file-trigger";
import { PetPhoto } from "./pet-photo";
import { MAX_PET_SLOTS } from "@/lib/pet-slot-state";
import { resolvePairedDeviceId } from "@/lib/device-command-api";
import { useDeviceConnection } from "./use-device-connection";
import { StatusBadge, type StatusTone } from "@/components/ui/status-badge";
import { planHomePetCards } from "@/lib/home-pet-cards";
import { homeBeamCardState, type HomeBeamCardState } from "@/lib/home-beam-state";

/**
 * Permanent brand hero — the approved golden-retriever campaign image.
 *
 * This is Eternal Beam content, not the user's pet. Uploading, switching or
 * restoring pets never swaps it; user pet assets live in Your Pets, Library,
 * Theme/Preview and the generation flow.
 */
const HOME_HERO_IMAGE = "/home/hero-golden-retriever.jpg";
const HOME_BEAM_IMAGE = "/home/beam-device.webp";

/** 홈 대시보드의 "나의 반려" 카드 한 장분. 이름 필드는 없다 — 슬롯에 이름이 없다. */
export interface HomePetSummary {
  index: number;
  previewImage: string | null;
}

interface HomeScreenProps {
  /** 기기 음성 호출용으로 등록된 이름(전역 한 개) — 있으면 현재 펫 카드 라벨로 쓴다. */
  petName?: string;
  pets: HomePetSummary[];
  activePetIndex: number;
  userName?: string;
  language?: string;
  /** "Add your first pet" picks a photo straight into the active (empty) slot — same commitUpload path as the upload screen. */
  onMediaFile: (file: File) => void;
  onSelectPet: (index: number) => void;
  onAddPet: () => void;
  onLibrary?: () => void;
  onDevice?: () => void;
  onSettings?: () => void;
  onTryForest?: () => void;
  /** Hero "Create a Memory" — the App routes it into the upload / add-pet flow. */
  onSaveToNFC: () => void;
}

const BEAM_CARD_TONE: Record<HomeBeamCardState, StatusTone> = {
  setup: "neutral",
  checking: "loading",
  connected: "connected",
  offline: "offline",
};

export function HomeScreen({
  petName,
  pets,
  activePetIndex,
  userName,
  language = "ko",
  onMediaFile,
  onSelectPet,
  onAddPet,
  onLibrary,
  onDevice,
  onSettings,
  onTryForest,
  onSaveToNFC,
}: HomeScreenProps) {
  const lang = memorialLang(language);
  const texts = memorialT(language).home;
  const deviceTexts = memorialT(language).device;
  const canAddPet = pets.length < MAX_PET_SLOTS;
  // Only slots with something to show are pets. A free slot number, or an
  // existing slot the user backed out of, both mean "Add Pet" can offer a place.
  const petCards = planHomePetCards(pets, MAX_PET_SLOTS);
  const showAddPet = petCards.showAddPet && (canAddPet || petCards.pets.length < pets.length);

  // Real Beam gateway status — same source as the full My Beam screen. The
  // resolver returns null when no production pairing exists; the id stays
  // internal and is never displayed.
  const deviceId = useMemo(() => resolvePairedDeviceId(), []);
  const { status, unavailableReason, retry } = useDeviceConnection(deviceId);
  const beamState = homeBeamCardState({ deviceId, status, unavailableReason });
  const beamLabel =
    beamState === "connected"
      ? texts.beamConnected
      : beamState === "offline"
        ? texts.beamOffline
        : beamState === "checking"
          ? deviceTexts.statusChecking
          : texts.beamNotSetUp;

  const petCardLabel = (index: number) =>
    index === activePetIndex && petName?.trim() ? petName.trim() : texts.petLabel(index + 1);

  return (
    <div className="hologram-bg-active memorial-screen-shell h-full flex flex-col relative overflow-hidden min-h-0">
      <HolographicBackground />
      <HologramEffects />

      {/* Header — mobile/tablet only; desktop identity lives in the top nav. */}
      <header className="px-6 pt-[var(--eb-header-top)] pb-4 relative z-10 shrink-0 lg:hidden">
        <div className="eb-home__header-row">
          <EternalBeamBrandMark language={language} className="justify-start" />

          <motion.button
            type="button"
            onClick={onSettings}
            className="mem-icon-btn relative shrink-0"
            whileTap={{ scale: 0.95 }}
            aria-label="Settings"
          >
            <Settings className="w-5 h-5" />
          </motion.button>
        </div>

        {userName ? (
          <motion.p
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="eb-home__greeting relative"
          >
            {lang === "ko" ? `${userName}님, ${texts.welcome}` : `${texts.welcome}, ${userName}`}
          </motion.p>
        ) : null}
      </header>

      {/* Dashboard content */}
      <div className="eb-home__scroll flex-1 overflow-y-auto min-h-0 relative z-10">
        <div className="eb-home__content">
          {/* Brand hero — permanent campaign content, never a user pet photo. */}
          <motion.section
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
            className="eb-home__hero"
            aria-labelledby="eb-home-hero-headline"
          >
            <div className="eb-home__hero-media">
              <img
                src={HOME_HERO_IMAGE}
                alt={texts.heroImageAlt}
                className="eb-home__hero-img"
                width={1672}
                height={941}
                loading="eager"
                fetchPriority="high"
                decoding="async"
              />
              <p className="eb-home__hero-handwritten" aria-hidden>
                {texts.heroHandwritten}
                <span>♡</span>
              </p>
              <p className="eb-home__hero-quote">{texts.heroQuote}</p>
            </div>

            <div className="eb-home__hero-content">
              <p className="eb-eyebrow">{texts.heroEyebrow}</p>
              <h1 id="eb-home-hero-headline" className="eb-home__hero-headline">
                {texts.heroHeadline}
              </h1>
              <p className="eb-body eb-home__hero-copy">{texts.heroBody}</p>

              <div className="eb-home__hero-actions">
                <motion.button
                  type="button"
                  onClick={onSaveToNFC}
                  className="cta-gold eb-btn eb-home__hero-cta"
                  whileHover={{ scale: 1.015 }}
                  whileTap={{ scale: 0.98 }}
                >
                  <Plus className="eb-home__button-icon" aria-hidden />
                  <span className="font-semibold tracking-wide">{texts.heroCtaCreate}</span>
                </motion.button>

                {onLibrary ? (
                  <button
                    type="button"
                    onClick={onLibrary}
                    className="eb-btn eb-btn--secondary eb-home__hero-cta"
                  >
                    {texts.heroCtaLibrary}
                  </button>
                ) : null}
              </div>

              {onTryForest ? (
                <button type="button" onClick={onTryForest} className="eb-home__hero-secondary">
                  {texts.tryForest}
                </button>
              ) : null}

              <p className="eb-home__hero-signoff">
                <Heart aria-hidden />
                <span>{texts.heroSignoff}</span>
              </p>
            </div>
          </motion.section>

          <div className="eb-home__dashboard-grid">
            {/* Your Pets — real slots only; never fake Pet 1/2/3 placeholders. */}
            <motion.section
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.2 }}
              className="eb-home__dashboard-card eb-home__pets-section"
              aria-labelledby="eb-home-pets-title"
            >
              <div className="eb-home__card-header">
                <h2 id="eb-home-pets-title" className="eb-home__card-heading">
                  <span className="eb-home__card-heading-icon"><PawPrint aria-hidden /></span>
                  {texts.yourPets}
                </h2>
                {showAddPet ? (
                  <button type="button" onClick={onAddPet} className="eb-home__card-link">
                    {texts.addPet}
                    <ChevronRight aria-hidden />
                  </button>
                ) : null}
              </div>
              <p className="eb-home__card-description">{texts.petsHint}</p>

              <div className="eb-home__pets-track">
                {petCards.showAddFirstPet ? (
                  <MediaFileTrigger
                    onFile={onMediaFile}
                    className="eb-home__pet-card eb-home__pet-card--first touch-manipulation"
                  >
                    <span className="eb-home__pet-card-body">
                      <span className="eb-home__pet-thumb eb-home__pet-thumb--add">
                        <Plus className="w-5 h-5" />
                      </span>
                      <span className="eb-home__pet-empty-copy">
                        <span className="eb-home__pet-name eb-home__pet-name--first">
                          <span aria-hidden>+ </span>{texts.addFirstPet}
                        </span>
                        <span className="eb-home__pet-empty-hint">{texts.addFirstPetHint}</span>
                      </span>
                      <span className="eb-home__pet-empty-action" aria-hidden>
                        {texts.addPet}
                      </span>
                    </span>
                  </MediaFileTrigger>
                ) : null}

                {petCards.pets.map((pet) => (
                  <button
                    key={pet.index}
                    type="button"
                    onClick={() => onSelectPet(pet.index)}
                    aria-pressed={pet.index === activePetIndex}
                    className={`eb-home__pet-card${
                      pet.index === activePetIndex ? " eb-home__pet-card--active" : ""
                    }`}
                  >
                    <span className="eb-home__pet-thumb">
                      <PetPhoto src={pet.previewImage} variant="avatar" />
                    </span>
                    <span className="eb-home__pet-name">{petCardLabel(pet.index)}</span>
                  </button>
                ))}

                {showAddPet ? (
                  <button
                    type="button"
                    onClick={onAddPet}
                    className="eb-home__pet-card eb-home__pet-card--add"
                    aria-label={texts.addPet}
                  >
                    <span className="eb-home__pet-thumb eb-home__pet-thumb--add">
                      <Plus className="w-5 h-5" />
                    </span>
                    <span className="eb-home__pet-name">{texts.addPet}</span>
                  </button>
                ) : null}
              </div>
            </motion.section>

            <motion.section
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.3 }}
              className="eb-home__dashboard-card eb-home__quick-start"
              aria-labelledby="eb-home-quick-title"
            >
              <div className="eb-home__card-header">
                <h2 id="eb-home-quick-title" className="eb-home__card-heading">
                  <span className="eb-home__card-heading-icon"><Sparkles aria-hidden /></span>
                  {texts.quickStart}
                </h2>
                <button type="button" onClick={onSaveToNFC} className="eb-home__card-link">
                  {texts.learnMore}
                  <ChevronRight aria-hidden />
                </button>
              </div>
              <p className="eb-home__card-description">{texts.quickStartHint}</p>
              <ol className="eb-home__steps">
                <li>
                  <span className="eb-home__step-number">1</span>
                  <span className="eb-home__step-icon"><ImageIcon aria-hidden /></span>
                  <span><strong>{texts.quickUploadTitle}</strong><small>{texts.quickUploadHint}</small></span>
                </li>
                <li>
                  <span className="eb-home__step-number">2</span>
                  <span className="eb-home__step-icon"><Palette aria-hidden /></span>
                  <span><strong>{texts.quickThemeTitle}</strong><small>{texts.quickThemeHint}</small></span>
                </li>
                <li>
                  <span className="eb-home__step-number">3</span>
                  <span className="eb-home__step-icon"><Play aria-hidden /></span>
                  <span><strong>{texts.quickBeamTitle}</strong><small>{texts.quickBeamHint}</small></span>
                </li>
              </ol>
            </motion.section>

            {onDevice ? (
              <motion.section
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.4 }}
                className="eb-home__dashboard-card eb-home__beam-card"
                aria-labelledby="eb-home-beam-title"
              >
                <div className="eb-home__card-header">
                  <h2 id="eb-home-beam-title" className="eb-home__card-heading">
                    <span className="eb-home__card-heading-icon"><Laptop aria-hidden /></span>
                    {texts.myBeam}
                  </h2>
                  <StatusBadge tone={BEAM_CARD_TONE[beamState]}>{beamLabel}</StatusBadge>
                </div>

                <p className="eb-home__card-description">
                  {beamState === "setup" ? texts.beamSetupTitle : texts.beamDescription}
                </p>

                <button type="button" onClick={onDevice} className="eb-home__beam-media">
                  <img
                    src={HOME_BEAM_IMAGE}
                    alt=""
                    width={1200}
                    height={600}
                    loading="lazy"
                    decoding="async"
                  />
                  <span className="eb-home__beam-media-shade" aria-hidden />
                </button>

                <div className="eb-home__beam-actions">
                  {beamState === "offline" ? (
                    <button type="button" className="eb-home__beam-retry" onClick={retry}>
                      {texts.beamRetry}
                    </button>
                  ) : null}
                  <button type="button" className="eb-home__beam-action" onClick={onDevice}>
                    {texts.beamOpenMyBeam}
                    <ChevronRight aria-hidden />
                  </button>
                </div>
              </motion.section>
            ) : null}
          </div>

          {/* The library remains reachable on compact layouts without relying on desktop nav. */}
          {onLibrary ? (
            <button type="button" onClick={onLibrary} className="eb-home__mobile-library eb-card">
              <span>
                <strong>{texts.myLibrary}</strong>
                <small>{texts.libraryHint}</small>
              </span>
              <ChevronRight aria-hidden />
            </button>
          ) : null}

          <motion.section
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.5 }}
            className="eb-home__value-strip"
            aria-label={texts.valuesLabel}
          >
            <div>
              <span className="eb-home__value-icon"><Heart aria-hidden /></span>
              <span><strong>{texts.valueMemoriesTitle}</strong><small>{texts.valueMemoriesHint}</small></span>
            </div>
            <div>
              <span className="eb-home__value-icon"><Palette aria-hidden /></span>
              <span><strong>{texts.valueThemesTitle}</strong><small>{texts.valueThemesHint}</small></span>
            </div>
            <div>
              <span className="eb-home__value-icon"><Star aria-hidden /></span>
              <span><strong>{texts.valueMotionTitle}</strong><small>{texts.valueMotionHint}</small></span>
            </div>
            <div>
              <span className="eb-home__value-icon"><House aria-hidden /></span>
              <span><strong>{texts.valueHomeTitle}</strong><small>{texts.valueHomeHint}</small></span>
            </div>
          </motion.section>
        </div>
      </div>
    </div>
  );
}
