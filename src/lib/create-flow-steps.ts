/**
 * Create-flow stepper 판정 — Upload Photos → Prepare Pet → Choose a Theme →
 * Preview & Create → Play on Beam.
 *
 * Theme Selection 화면에 렌더된다는 사실 자체가 Upload Photos 를 이미 마쳤다는
 * 뜻이다 — 그 앞 단계를 거치지 않고는 이 화면에 도달할 수 없다. 나머지 하나
 * 판정할 수 있는 단계(Prepare Pet)는 실제 신호(누끼 존재)로 정한다 — 화면이
 * 가짜 진행률을 지어내지 않는다. Preview & Create/Play on Beam 은 이 화면
 * 뒤에 오므로 항상 예정이다.
 */

export type CreateFlowStepId =
  | "uploadPhotos"
  | "preparePet"
  | "chooseTheme"
  | "previewCreate"
  | "playOnBeam";

export type CreateFlowStepStatus = "complete" | "current" | "upcoming";

export interface CreateFlowStepState {
  id: CreateFlowStepId;
  status: CreateFlowStepStatus;
}

export function computeCreateFlowSteps(hasPreparedPet: boolean): CreateFlowStepState[] {
  return [
    { id: "uploadPhotos", status: "complete" },
    { id: "preparePet", status: hasPreparedPet ? "complete" : "current" },
    { id: "chooseTheme", status: hasPreparedPet ? "current" : "upcoming" },
    { id: "previewCreate", status: "upcoming" },
    { id: "playOnBeam", status: "upcoming" },
  ];
}
