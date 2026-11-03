"""Real worker/builder/receipt resume with fake paid providers and media QA."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.services import durable_provider_jobs as jobs, pet_generation_run_service as runs
from backend.services import motion_spec as ms, motion_video_service as mv, motion_video_prompts as prompts
from backend.services import video_motion_providers as vp, motion_delivery_service, motion_publication_service
from backend.services import motion_video_qa, vlm_identity
from backend.services.provider_job_contract import ProviderSubmission, ProviderJobCheck, SUCCEEDED, PENDING
from .test_motion_video_generation import (
    _mock_backend, storage, _prepare_pipeline, _run, GOOD, USER, PET,
    sampler_identical, conformance_ok,
)


class FakePaidProvider(vp.VideoGenerationProvider):
    vendor_id = "fal"
    supports_durable_jobs = True
    supports_end_frame = True

    def __init__(self, model, adapter):
        self.name = self.logical_model_id = model
        self.adapter_id = adapter
        self.state = {"requests": [], "polls": [], "collects": [], "pending_checks": 0}

    def available(self):
        return True

    def model_name(self):
        return self.name + "/original-endpoint"

    def submit(self, request):
        self.state["requests"].append(request)
        return ProviderSubmission(f"{self.name}-job-{len(self.state['requests'])}")

    def check(self, external_job_id):
        self.state["polls"].append(external_job_id)
        if self.state["pending_checks"]:
            self.state["pending_checks"] -= 1
            return ProviderJobCheck(PENDING, "IN_PROGRESS")
        return ProviderJobCheck(SUCCEEDED, "COMPLETED")

    def collect(self, external_job_id):
        self.state["collects"].append(external_job_id)
        return vp.MotionVideoResult(video_bytes=GOOD(), provider=self.name,
                                    model=self.model_name(), external_job_id=external_job_id)


@pytest.fixture
def case(monkeypatch, storage):
    runs.__reset_for_tests()
    monkeypatch.setenv("GENERATION_PROVIDER_POLL_SECONDS", "0")
    monkeypatch.setenv("PHASE6_MAX_PRIMARY_MICRO", "2")
    monkeypatch.setenv("MOTION_MODEL_BREATHING", "wan_3_standard")
    monkeypatch.setenv("MOTION_VENDOR_WAN_3_STANDARD", "fal")
    monkeypatch.setenv("MOTION_VENDOR_MINIMAX_H3_MAX_TURBO", "fal")
    h, _ = _prepare_pipeline(monkeypatch, storage)
    contract = _run(ms.resolve_video_generation_spec(user_id=USER, pet_id=PET, motion_id="BREATHING"))
    wan = FakePaidProvider("wan_3_standard", "FalWan3StandardProvider")
    mini = FakePaidProvider("minimax_h3_max_turbo", "FalMinimaxH3MaxTurboProvider")
    monkeypatch.setitem(vp._MODEL_VENDOR_ADAPTERS, wan.name, {"fal": wan})
    monkeypatch.setitem(vp._MODEL_VENDOR_ADAPTERS, mini.name, {"fal": mini})
    row = dict(id="pinned-run", user_id=USER, pet_id=PET, content_id=PET.removeprefix("pet_"),
               motion_id="BREATHING", request_kind="FREE_HOME", idempotency_key="pin-test",
               status=runs.STATUS_QUEUED, current_stage=runs.STAGE_MOTION_GENERATION,
               identity_profile_id="identity", identity_profile_version=1,
               reference_set_id="references", reference_set_version=1,
               canonical_version_id=contract['canonical_version_id'], canonical_version=1,
               keyframes={"STAND_READY": {"id": contract['start_keyframe']['keyframe_id'],
                                          "version": contract['start_keyframe']['version']}},
               motion_spec_version=ms.MOTION_SPEC_VERSION, motion_version_id=None,
               provider_state={}, retry_count=0, credits_reserved=0)
    runs._MOCK_RUNS.append(row)
    state = SimpleNamespace(wan=wan, mini=mini, row=row, first_fails=False, first_review=False, qa_calls=[], contract=contract)
    build = mv.build_motion_video

    async def build_with_io(**kwargs):
        return await build(**kwargs, fetch_bytes=lambda obj: storage.get(obj.object_path) or h.kf_fetch(obj),
                           sign_url_fn=lambda obj: "https://test/" + obj.object_path,
                           frame_sampler=sampler_identical, conformance_fn=conformance_ok)

    async def evaluate(**kwargs):
        state.qa_calls.append(deepcopy(kwargs['contract']))
        acceptance = deepcopy(kwargs['contract']['requirements']['qa']['business'])
        acceptance['motion_id'] = 'BREATHING'
        checks = dict.fromkeys(acceptance['acceptance']['required_checks'], 'PASS')
        if state.first_fails and len(state.qa_calls) == 1:
            checks['vlm_anatomy'] = 'FAIL'
        if state.first_review and len(state.qa_calls) == 1:
            checks['vlm_anatomy'] = 'REVIEW'
        return dict(decision='FAIL' if 'FAIL' in checks.values() else 'REVIEW' if 'REVIEW' in checks.values() else 'PASS', checks=checks,
                    motion_business_contract=acceptance, identity_similarity=1.0,
                    qa_version=motion_video_qa.active_qa_version(),
                    sampling_version=motion_video_qa.FRAME_SAMPLING_VERSION,
                    vlm={'source': vlm_identity.VLM_MOTION_ACCEPTANCE_QA_VERSION})

    async def package(**kwargs):
        pass

    async def publish(**kwargs):
        v = next(v for v in mv._MOCK_VERSIONS if v['id'] == kwargs['motion_version_id'])
        return SimpleNamespace(publication_id='publication', selected_candidate_id=v['selected_candidate_id'])

    monkeypatch.setattr(mv, 'build_motion_video', build_with_io)
    monkeypatch.setattr(mv, '_evaluate_candidate_qa', evaluate)
    monkeypatch.setattr(motion_delivery_service, 'package_breathing_for_delivery', package)
    monkeypatch.setattr(motion_publication_service, 'publish_breathing', publish)
    yield state
    runs.__reset_for_tests()


def tick():
    return _run(runs.process_next_generation_run(worker_id='single-worker'))


def change_config(monkeypatch):
    monkeypatch.setenv('MOTION_MODEL_BREATHING', 'minimax_h3_max_turbo')
    monkeypatch.setenv('MOTION_VENDOR_WAN_3_STANDARD', 'runway')
    monkeypatch.setenv('PHASE6_RESOLUTION', '1080p')
    monkeypatch.setenv('PHASE6_MAX_PRIMARY_MICRO', '1')
    monkeypatch.setattr(ms, 'MOTION_SPEC_VERSION', 'future-spec')
    monkeypatch.setattr(ms, 'MOTION_QA_CONTRACT_VERSION', 'future-qa-contract')
    monkeypatch.setattr(prompts, 'MOTION_VIDEO_PROMPT_VERSION', 'future-prompt')
    spec = ms.MOTIONS['BREATHING']
    requirements = deepcopy(spec.requirements)
    requirements['qa']['business']['version'] = 'future-qa-contract'
    monkeypatch.setitem(ms.MOTIONS, 'BREATHING', replace(spec, description='future breathing prompt', requirements=requirements))


@pytest.mark.parametrize('regenerate', [False, True])
def test_resume_pins_model_versions_inputs_and_qa_retry(case, monkeypatch, regenerate):
    case.first_fails = regenerate
    first = tick()
    assert first.status == runs.STATUS_WAITING_PROVIDER, first.last_error
    original = deepcopy(mv._MOCK_VERSIONS[0])
    first_job = deepcopy(jobs.list_for_run(first.id)[0])
    change_config(monkeypatch)

    def forbidden(*args, **kwargs):
        pytest.fail('Resume consulted current routing/spec/prompt instead of its snapshot')
    monkeypatch.setattr(ms, 'provider_order_for_motion', forbidden)
    monkeypatch.setattr(ms, 'resolve_video_generation_spec', forbidden)
    monkeypatch.setattr(prompts, 'build_motion_video_prompt', forbidden)
    monkeypatch.setattr(mv, 'reevaluate_motion_candidate', forbidden)
    monkeypatch.setattr(case.wan, 'model_name', lambda: 'changed-endpoint')
    resumed = tick()
    if regenerate:
        assert resumed.status == runs.STATUS_WAITING_PROVIDER, resumed.last_error
        assert mv._MOCK_CANDIDATES[0]['qa_result']['business_qa']['retry_action'] == 'REGENERATE'
        resumed = tick()
    assert resumed.status == runs.STATUS_PUBLISHED, resumed.last_error
    count = 2 if regenerate else 1
    assert len(case.wan.state['requests']) == count
    assert not case.mini.state['requests']
    assert len(mv._MOCK_VERSIONS) == 1
    final = mv._MOCK_VERSIONS[0]
    for key in ('id', 'prompt', 'prompt_version', 'motion_spec_version', 'output_spec', 'analyzer_versions'):
        assert final[key] == original[key]
    assert all(q == original['analyzer_versions'][mv.DURABLE_CONTRACT_KEY]['contract'] for q in case.qa_calls)
    for req in case.wan.state['requests']:
        assert req.prompt == original['prompt']
        assert req.output_spec == original['output_spec']
        assert req.start_image_bytes == case.wan.state['requests'][0].start_image_bytes
    receipts = jobs.list_for_run(first.id)
    assert len(receipts) == count
    assert receipts[0]['external_job_id'] == first_job['external_job_id']
    assert all(j['phase_version_id'] == original['id'] and j['provider'] == 'wan_3_standard' for j in receipts)
    assert [j['attempt'] for j in receipts] == list(range(1, count + 1))
    assert all(j['model'] == first_job['model'] for j in receipts)
    assert tick() is None  # Completed runs cannot submit again.


def test_completely_new_run_uses_new_model(case, monkeypatch):
    assert tick().status == runs.STATUS_WAITING_PROVIDER
    assert tick().status == runs.STATUS_PUBLISHED
    # Change only the model: the spec/keyframes remain identical, so a lineage-
    # only cache shortcut would incorrectly hand this run the old Wan video.
    monkeypatch.setenv('MOTION_MODEL_BREATHING', 'minimax_h3_max_turbo')
    row = deepcopy(case.row)
    row.update(id='new-run', status=runs.STATUS_QUEUED, current_stage=runs.STAGE_MOTION_SPEC,
               idempotency_key='new-generation', motion_version_id=None, motion_version=None,
               selected_candidate_id=None, publication_id=None, provider_state={})
    runs._MOCK_RUNS.append(row)
    result = tick()
    assert result.status == runs.STATUS_WAITING_PROVIDER, result.last_error
    assert len(case.mini.state['requests']) == 1
    assert len(case.wan.state['requests']) == 1
    new_job = jobs.list_for_run('new-run')[0]
    old_job = jobs.list_for_run('pinned-run')[0]
    assert new_job['provider'] == 'minimax_h3_max_turbo'
    assert new_job['phase_version_id'] != old_job['phase_version_id']
    version = next(v for v in mv._MOCK_VERSIONS if v['id'] == new_job['phase_version_id'])
    assert version['motion_spec_version'] == ms.MOTION_SPEC_VERSION
    assert version['prompt_version'] == prompts.MOTION_VIDEO_PROMPT_VERSION


@pytest.mark.parametrize('damage', ['missing_snapshot', 'foreign_pet', 'second_version', 'wrong_adapter'])
def test_unverifiable_pin_blocks_without_submission(case, damage):
    assert tick().status == runs.STATUS_WAITING_PROVIDER
    job = jobs._MOCK_JOBS[0]
    if damage == 'missing_snapshot':
        mv._MOCK_VERSIONS[0]['analyzer_versions'].pop(mv.DURABLE_CONTRACT_KEY)
    elif damage == 'foreign_pet':
        job['pet_id'] = 'another-pet'
    elif damage == 'second_version':
        jobs._MOCK_JOBS.append({**deepcopy(job), 'id': 'other-job', 'phase_version_id': 'another-version'})
    else:
        job['result_metadata']['provider_identity']['adapter'] = 'wrong-adapter'
    result = tick()
    assert result.status == runs.STATUS_FAILED
    assert result.last_error['code'] in {'MOTION_RESUME_CONTRACT_MISSING', 'MOTION_RESUME_CONTRACT_INVALID'}
    assert len(case.wan.state['requests']) == 1
    assert not case.mini.state['requests']
    assert not case.wan.state['collects']


def test_repeated_pending_resumes_never_resubmit(case, monkeypatch):
    assert tick().status == runs.STATUS_WAITING_PROVIDER
    case.wan.state['pending_checks'] = 2
    change_config(monkeypatch)
    for _ in range(2):
        result = tick()
        assert result.status == runs.STATUS_WAITING_PROVIDER, result.last_error
        assert len(jobs.list_for_run(result.id)) == 1
        assert len(case.wan.state['requests']) == 1
    assert tick().status == runs.STATUS_PUBLISHED
    assert len(case.wan.state['requests']) == 1
    assert len(case.wan.state['collects']) == 1


def test_review_candidate_publishes_without_replacement_or_second_submission(case, monkeypatch):
    case.first_review = True
    assert tick().status == runs.STATUS_WAITING_PROVIDER
    published = tick()
    assert published.status == runs.STATUS_PUBLISHED, published.last_error
    source = mv._MOCK_VERSIONS[0]
    candidate = mv._MOCK_CANDIDATES[0]
    assert candidate['decision'] == 'REVIEW'
    assert candidate['qa_result']['business_qa']['delivery_action'] == 'DELIVER_WITH_ADVISORY'
    change_config(monkeypatch)
    with pytest.raises(runs.PetGenerationRunError) as error:
        _run(runs.request_replacement_generation(
            user_id=USER, run_id=published.id, idempotency_key='replacement', reason='reviewed'
        ))
    assert error.value.code == 'RUN_ALREADY_PUBLISHED'
    assert len(mv._MOCK_VERSIONS) == 1
    assert published.motion_version_id == source['id']
    assert len(case.wan.state['requests']) == 1 and not case.mini.state['requests']
    assert tick() is None


def test_new_run_with_unchanged_config_reuses_completed_output(case):
    assert tick().status == runs.STATUS_WAITING_PROVIDER
    first = tick()
    assert first.status == runs.STATUS_PUBLISHED
    row = deepcopy(case.row)
    row.update(id='unchanged-run', status=runs.STATUS_QUEUED, current_stage=runs.STAGE_MOTION_SPEC,
               idempotency_key='unchanged', motion_version_id=None, motion_version=None,
               selected_candidate_id=None, publication_id=None, provider_state={})
    runs._MOCK_RUNS.append(row)
    result = tick()
    assert result.status == runs.STATUS_PUBLISHED, result.last_error
    assert result.motion_version_id == first.motion_version_id
    assert len(case.wan.state['requests']) == 1
    assert jobs.list_for_run(result.id) == []
    assert len(case.qa_calls) == 1
