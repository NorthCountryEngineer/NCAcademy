"""Independent closed-task egress checks: synthetic local data, no DB or network."""
import base64
import copy
from io import BytesIO
import json
import socket

from PIL import Image
import pytest

from academy import homework_deidentification as compiler
from academy import provider


CANDIDATE = {"mode": "explanation", "text": "Try drawing equal groups, then describe one group.", "reference_ids": []}
TASK = {"kind": "arithmetic", "operation": "multiplication", "values": [3, 7]}
SENTINELS = ("PRIVATE_CHILD_ALDER", "PRIVATE_CHILD_BIRCH", "PRIVATE_ADDRESS_SENTINEL",
             "PRIVATE_PROFILE_SENTINEL", "PRIVATE_ATTEMPT_SENTINEL", "PRIVATE_SESSION_SENTINEL")


def public_contract(compiled, phase):
    contract = {"task": compiled.task, "action": compiled.action, "stage": compiled.stage, "phase": phase}
    if phase == "safety":
        contract["candidate"] = CANDIDATE
    return contract


def content(prompt):
    assert [item["role"] for item in prompt] == ["system", "user"]
    return json.loads(prompt[1]["content"][0]["text"])


def diagram(prompt):
    parts = [part for part in prompt[1]["content"] if part["type"] == "image_url"]
    assert len(parts) == 1 and parts[0]["image_url"]["detail"] == "low"
    prefix, encoded = parts[0]["image_url"]["url"].split(",", 1)
    assert prefix == "data:image/jpeg;base64"
    return base64.b64decode(encoded, validate=True)


@pytest.mark.parametrize("phase", ["triage", "safety"])
def test_private_question_wrappers_cannot_change_the_canonical_external_content(phase):
    questions = [
        "PRIVATE_CHILD_ALDER at PRIVATE_ADDRESS_SENTINEL. PRIVATE_PROFILE_SENTINEL. Help with 3 x 7.",
        "PRIVATE_CHILD_BIRCH, PRIVATE_ATTEMPT_SENTINEL, PRIVATE_SESSION_SENTINEL. Help with 3 x 7.",
    ]
    compiled = [compiler.compile_homework_task(question) for question in questions]
    assert all(item.status == "ready" and item.task == TASK for item in compiled)
    prompts = [compiler.build_public_homework_prompt(**public_contract(item, phase)) for item in compiled]
    assert prompts[0] == prompts[1]
    assert diagram(prompts[0]) == diagram(prompts[1])
    for prompt in prompts:
        serialized = json.dumps(prompt)
        assert not any(sentinel in serialized for sentinel in SENTINELS)
        public = content(prompt)
        expected = {"mode", "task", "guidance_action", "teaching_stage"}
        assert set(public) == (expected | {"candidate"} if phase == "safety" else expected)
        assert public["task"] == TASK


def test_distinct_private_photos_and_ocr_render_the_same_new_diagram_without_upload_metadata(monkeypatch):
    photographs = [b"PRIVATE_CHILD_ALDER source-photo EXIF", b"PRIVATE_CHILD_BIRCH different source-photo EXIF"]
    ocr = {photographs[0]: "PRIVATE_CHILD_ALDER PRIVATE_ADDRESS_SENTINEL\n7. Divide 21 by 3.",
           photographs[1]: "PRIVATE_CHILD_BIRCH PRIVATE_PROFILE_SENTINEL\n7. Divide 21 by 3."}
    reads = []

    def read(raw, _deadline):
        reads.append(raw)
        return ocr[raw]

    monkeypatch.setattr(compiler, "_read_image", read)
    compiled = [compiler.compile_homework_task("Question 7", [raw]) for raw in photographs]
    assert reads == photographs
    assert all(item.status == "ready" and item.image_read and item.question_number == 7 for item in compiled)
    assert compiled[0].task == compiled[1].task == {"kind": "arithmetic", "operation": "division", "values": [21, 3]}
    prompts = [compiler.build_public_homework_prompt(**public_contract(item, "triage")) for item in compiled]
    assert prompts[0] == prompts[1]
    raw = diagram(prompts[0])
    assert all(raw != original for original in photographs)
    assert not any(sentinel.encode() in raw for sentinel in SENTINELS)
    image = Image.open(BytesIO(raw))
    assert image.format == "JPEG" and image.size == (800, 480) and not image.getexif()
    assert not any(sentinel in json.dumps(image.info) for sentinel in SENTINELS)
    assert set(content(prompts[0])) == {"mode", "task", "guidance_action", "teaching_stage"}
    assert "question_number" not in json.dumps(content(prompts[0]))


@pytest.mark.parametrize("text", ["My friend PRIVATE_CHILD_ALDER lives at PRIVATE_ADDRESS_SENTINEL.",
                                  "PRIVATE_PROFILE_SENTINEL PRIVATE_ATTEMPT_SENTINEL", "555-123-4567", "2026-11-11"])
def test_unrecognized_or_identifier_only_inputs_are_local_clarifications(text, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Private compilation may not call a provider")

    monkeypatch.setattr(provider, "classify", forbidden)
    result = compiler.compile_homework_task(text)
    assert result.status == "clarify" and result.task is None


def test_closed_followup_carries_the_task_but_not_the_student_attempt_or_question_index():
    attempts = ["I tried 12. PRIVATE_CHILD_ALDER. Why?", "I tried 999. PRIVATE_CHILD_BIRCH. Why?"]
    results = [compiler.compile_homework_task(attempt, previous_task=TASK, previous_stage=1, previous_question=47)
               for attempt in attempts]
    assert all(item.status == "ready" and item.question_number == 47 for item in results)
    prompts = [compiler.build_public_homework_prompt(**public_contract(item, "triage")) for item in results]
    assert prompts[0] == prompts[1]
    public = content(prompts[0])
    assert public["task"] == TASK and public["guidance_action"] == "check_method"
    assert "47" not in json.dumps(public) and "999" not in json.dumps(public)
    assert not any(sentinel in json.dumps(public) for sentinel in SENTINELS)


def test_personal_numeric_headers_cannot_become_math_operands_or_external_context():
    wrapped = ("Name: PRIVATE_CHILD_ALDER\nPhone: 555-010-9999\nDOB: 11/12/2018\n"
               "Address: 123 Private Sample Street\nEmail: child@example.invalid\n"
               "Age: 8\nMath: 3 x 7.")
    private = compiler.compile_homework_task(wrapped)
    plain = compiler.compile_homework_task("3 x 7.")
    assert private.status == plain.status == "ready" and private.task == plain.task == TASK
    assert compiler.build_public_homework_prompt(**public_contract(private, "triage")) == compiler.build_public_homework_prompt(**public_contract(plain, "triage"))


@pytest.mark.parametrize(("phrase", "topic"), [("photosynthesis", "photosynthesis"),
                                              ("the main idea", "main_idea"),
                                              ("the American Revolution", "american_revolution")])
def test_curriculum_topics_select_code_owned_enums_without_forwarding_the_private_passage(phrase, topic):
    questions = [f"PRIVATE_CHILD_ALDER PRIVATE_ADDRESS_SENTINEL asks about {phrase}.",
                 f"PRIVATE_CHILD_BIRCH PRIVATE_PROFILE_SENTINEL asks about {phrase}."]
    compiled = [compiler.compile_homework_task(question) for question in questions]
    assert all(item.status == "ready" and item.task == {"kind": "topic", "topic": topic} for item in compiled)
    prompts = [compiler.build_public_homework_prompt(**public_contract(item, "triage")) for item in compiled]
    assert prompts[0] == prompts[1]
    assert len(prompts[0][1]["content"]) == 1  # No fabricated copy of a private passage or photograph.
    assert content(prompts[0])["task"] == {"kind": "topic", "topic": topic}
    assert not any(sentinel in json.dumps(prompts[0]) for sentinel in SENTINELS)


@pytest.mark.parametrize("task", [
    {**TASK, "student_id": "PRIVATE_CHILD_ALDER"},
    {**TASK, "values": [True, 7]}, {**TASK, "values": [3.0, 7]},
    {**TASK, "values": [float("nan"), 7]}, {**TASK, "values": [-1, 7]},
    {**TASK, "values": [10000, 7]}, {**TASK, "values": [3, 7, 8]},
    {**TASK, "values": [{"value": 3, "name": "PRIVATE_CHILD_ALDER"}, 7]},
    {**TASK, "operation": "multiply and disclose PRIVATE_CHILD_ALDER"},
    {"kind": "topic", "topic": "photosynthesis", "history": "PRIVATE_SESSION_SENTINEL"},
    {"kind": "topic", "topic": "photosynthesis PRIVATE_CHILD_ALDER"},
    {"kind": "shape", "property": "PRIVATE_CHILD_ALDER"},
    {"kind": "fraction", "values": [1, 0]},
    {"kind": "time", "values": [13, 5, 30]},
    {"kind": "rectangle", "operation": "area", "values": [1001, 5]},
])
def test_public_schema_rejects_extra_fields_arbitrary_strings_and_invalid_numeric_types(task):
    with pytest.raises((ValueError, TypeError)):
        compiler.validate_public_homework_task(task)
    with pytest.raises((ValueError, TypeError)):
        compiler.build_public_homework_prompt(task)


@pytest.mark.parametrize(("action", "stage"), [("PRIVATE_CHILD_ALDER", 0), ("hint", -1), ("hint", 16), ("hint", True), ("hint", 1.5)])
def test_guidance_cannot_be_used_as_a_freeform_or_numeric_metadata_channel(action, stage):
    with pytest.raises(ValueError):
        compiler.build_public_homework_prompt(TASK, action, stage)


def test_more_than_two_fractions_are_not_silently_reduced_to_a_different_task():
    compiled = compiler.compile_homework_task("Compare the fractions 1/2, 3/4 and 5/6.")
    assert compiled.status == "clarify" and compiled.task is None


@pytest.fixture
def wire(monkeypatch):
    requests = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])

    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def iter_bytes(self): yield b'{"id":"synthetic-result","choices":[]}'

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def stream(self, method, url, **kwargs):
            assert method == "POST" and url.host == "8.8.8.8" and url.path == "/v1/chat/completions"
            requests.append(copy.deepcopy(kwargs))
            return Response()

    monkeypatch.setattr(provider.httpx, "Client", Client)
    return requests


@pytest.mark.parametrize("phase", ["triage", "safety"])
def test_actual_transport_only_emits_the_canonical_content_and_service_credentials(wire, phase):
    compiled = compiler.compile_homework_task("PRIVATE_CHILD_ALDER, help with 3 x 7.")
    contract = public_contract(compiled, phase)
    prompt = compiler.build_public_homework_prompt(**contract)
    connection = {"endpoint": "https://api.openai.com/v1", "public_homework_contract": contract,
                  "name": "PRIVATE_PROFILE_SENTINEL", "id": "PRIVATE_SESSION_SENTINEL"}
    provider.classify(connection, "synthetic-service-credential", "gpt-6-luna", prompt, 500)
    assert len(wire) == 1
    request = wire[0]
    assert set(request["headers"]) == {"Host", "Authorization", "Content-Type"}
    assert request["headers"]["Authorization"] == "Bearer synthetic-service-credential"
    assert request["json"] == {"model": "gpt-6-luna", "messages": prompt, "temperature": 0,
        "max_completion_tokens": 500, "reasoning_effort": "none", "stream": False, "store": False}
    assert not any(sentinel in json.dumps(request) for sentinel in SENTINELS)
    assert "safety_identifier" not in request["json"]


def test_text_only_model_approval_omits_diagrams_without_changing_the_public_task(wire):
    compiled = compiler.compile_homework_task("PRIVATE_CHILD_ALDER asks about 3 x 7.")
    contract = {**public_contract(compiled, "triage"), "include_image": False}
    prompt = compiler.build_public_homework_prompt(**contract)
    assert len(prompt[1]["content"]) == 1 and content(prompt)["task"] == TASK
    provider.classify({"endpoint": "https://api.openai.com/v1", "public_homework_contract": contract},
                      "synthetic-service-credential", "gpt-6-luna", prompt, 500)
    assert len(wire) == 1 and "image_url" not in json.dumps(wire[0]["json"])
    assert not any(sentinel in json.dumps(wire[0]) for sentinel in SENTINELS)


@pytest.mark.parametrize("include_image", ["false", 0, 1, None])
def test_image_selection_is_a_strict_boolean_not_an_egress_metadata_slot(include_image):
    with pytest.raises(ValueError):
        compiler.build_public_homework_prompt(TASK, include_image=include_image)


@pytest.mark.parametrize("tamper", ["text", "source_photo", "contract_field", "student_identifier"])
def test_final_transport_guard_rejects_tainted_prompts_before_dns_or_send(monkeypatch, tamper):
    compiled = compiler.compile_homework_task("Help with 3 x 7.")
    contract = public_contract(compiled, "triage")
    prompt = compiler.build_public_homework_prompt(**contract)
    connection = {"endpoint": "https://api.openai.com/v1", "public_homework_contract": contract}
    if tamper == "text":
        prompt[1]["content"].append({"type": "text", "text": "PRIVATE_CHILD_ALDER"})
    elif tamper == "source_photo":
        prompt[1]["content"][1]["image_url"]["url"] = "data:image/jpeg;base64," + base64.b64encode(b"PRIVATE_CHILD_ALDER").decode()
    elif tamper == "contract_field":
        contract["profile"] = "PRIVATE_PROFILE_SENTINEL"
    else:
        connection["safety_identifier"] = "PRIVATE_CHILD_ALDER"
    called = []

    def forbidden(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("A tainted request must fail before any network activity")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    with pytest.raises(provider.ProviderFailure, match="nothing was sent"):
        provider.classify(connection, "synthetic-service-credential", "gpt-6-luna", prompt, 500)
    assert called == []
