import { useMemo } from 'react'
import {
  configuredExhibitionLocation,
  type PassportRecord,
} from '@/lib/exhibition-passport-api'
import {
  buildPassportSvg,
  PASSPORT_TEMPLATE_PATH,
  passportLocationLabel,
} from '@/lib/exhibition-passport-art'

export type PassportLanguage = 'en' | 'ko'

type PassportArtworkProps = {
  record: PassportRecord
  language: PassportLanguage
  photoUnavailable?: boolean
  onPhotoError?: () => void
}

export function PassportArtwork({
  record,
  language,
  photoUnavailable = false,
  onPhotoError,
}: PassportArtworkProps) {
  const hasPhoto = Boolean(record.photo_url) && !photoUnavailable
  const location = passportLocationLabel(record.exhibition_id, configuredExhibitionLocation())
  const svg = useMemo(() => buildPassportSvg(record, {
    templateHref: PASSPORT_TEMPLATE_PATH,
    photoHref: hasPhoto ? record.photo_url : null,
    location,
    language,
  }), [hasPhoto, language, location, record])

  return (
    <div className="passport-art" data-testid="passport-art">
      <div className="passport-art-svg" dangerouslySetInnerHTML={{ __html: svg }} />
      {hasPhoto && (
        <img
          className="passport-art-photo-probe"
          src={record.photo_url || undefined}
          alt="Exhibition portrait for your Pet Passport"
          onError={onPhotoError}
        />
      )}
    </div>
  )
}
