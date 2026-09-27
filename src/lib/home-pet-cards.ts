/**
 * Home "Your Pets" card plan.
 *
 * The App always holds at least one intake slot (slot 0), and a slot that has
 * no photo yet is still a slot — but it is not a pet. Home shows only slots
 * that actually have something to show, so a brand-new user never sees
 * "Pet 1 / Pet 2 / Pet 3" placeholders:
 *
 *   0 pets → "Add your first pet" only
 *   1–2   → the real pets + "Add Pet"
 *   3     → the real pets, no "Add Pet"
 *
 * Nothing here invents a name, breed or thumbnail: a card is exactly the slot
 * index plus the preview the slot already has.
 */
export interface HomePetCardSource {
  index: number;
  previewImage: string | null;
}

export interface HomePetCard {
  index: number;
  previewImage: string;
}

export interface HomePetCardPlan {
  pets: HomePetCard[];
  showAddFirstPet: boolean;
  showAddPet: boolean;
}

export function planHomePetCards(pets: HomePetCardSource[], maxSlots: number): HomePetCardPlan {
  const real: HomePetCard[] = [];
  for (const pet of pets) {
    if (pet.previewImage !== null && pet.previewImage !== "") {
      real.push({ index: pet.index, previewImage: pet.previewImage });
    }
  }
  return {
    pets: real,
    showAddFirstPet: real.length === 0,
    showAddPet: real.length > 0 && real.length < maxSlots,
  };
}
