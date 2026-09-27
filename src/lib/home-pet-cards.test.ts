import { strict as assert } from "node:assert";
import { test } from "node:test";
import { planHomePetCards } from "./home-pet-cards.ts";

const MAX = 3;
const pet = (index: number) => ({ index, previewImage: `blob:pet-${index}` });
const empty = (index: number) => ({ index, previewImage: null });

test("0 pets — only the 'Add your first pet' card, no Pet 1/2/3 placeholders", () => {
  const plan = planHomePetCards([empty(0)], MAX);
  assert.deepEqual(plan.pets, []);
  assert.equal(plan.showAddFirstPet, true);
  assert.equal(plan.showAddPet, false);
});

test("0 pets even when several empty slots exist", () => {
  const plan = planHomePetCards([empty(0), empty(1), empty(2)], MAX);
  assert.deepEqual(plan.pets, []);
  assert.equal(plan.showAddFirstPet, true);
  assert.equal(plan.showAddPet, false);
});

test("1 pet — the real pet + Add Pet", () => {
  const plan = planHomePetCards([pet(0)], MAX);
  assert.deepEqual(plan.pets, [{ index: 0, previewImage: "blob:pet-0" }]);
  assert.equal(plan.showAddFirstPet, false);
  assert.equal(plan.showAddPet, true);
});

test("1 pet + an empty slot the user backed out of — still 1 real pet + Add Pet", () => {
  const plan = planHomePetCards([pet(0), empty(1)], MAX);
  assert.deepEqual(plan.pets, [{ index: 0, previewImage: "blob:pet-0" }]);
  assert.equal(plan.showAddPet, true);
});

test("2 pets — two real pets + Add Pet", () => {
  const plan = planHomePetCards([pet(0), pet(1)], MAX);
  assert.equal(plan.pets.length, 2);
  assert.equal(plan.showAddFirstPet, false);
  assert.equal(plan.showAddPet, true);
});

test("3 pets — three real pets, no Add Pet", () => {
  const plan = planHomePetCards([pet(0), pet(1), pet(2)], MAX);
  assert.equal(plan.pets.length, 3);
  assert.equal(plan.showAddFirstPet, false);
  assert.equal(plan.showAddPet, false);
});

test("cards carry only the slot index and its real preview — nothing fabricated", () => {
  const plan = planHomePetCards([pet(2)], MAX);
  assert.deepEqual(Object.keys(plan.pets[0]).sort(), ["index", "previewImage"]);
  assert.equal(plan.pets[0].index, 2);
});

test("an empty-string preview is not a pet", () => {
  const plan = planHomePetCards([{ index: 0, previewImage: "" }], MAX);
  assert.equal(plan.showAddFirstPet, true);
});
