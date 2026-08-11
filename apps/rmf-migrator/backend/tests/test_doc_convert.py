"""Tests for the .doc conversion seam."""

from __future__ import annotations

import json

import pytest

from rmf_migrator.common.aws_clients import CONVERT_CONFIG
from rmf_migrator.common.config import _BACKENDS, Config, ConfigError
from rmf_migrator.common.limits import MAX_DOC_BYTES, ObjectTooLarge
from rmf_migrator.doc_convert import (
    CONVERSION_DISABLED_MESSAGE,
    ConversionError,
    ConversionFailed,
    ConversionUnavailable,
    LibreOfficeLambdaConverter,
    RejectingConverter,
    build_converter,
)


def test_rejecting_converter_raises_unavailable():
    with pytest.raises(ConversionUnavailable):
        RejectingConverter().convert(b"\xd0\xcf\x11\xe0anything")


def test_rejecting_converter_message_never_sends_the_user_to_word():
    """request_upload answers every refused .doc with this text, and it never
    sees bytes — so it cannot tell an unexamined file from one the upload
    screen already classified as a Word 97-2003 container. It has to assume the
    worse case and name a remedy that does not open the file locally. The
    frontend byte refusal pins the same doctrine (client.test.ts).
    """
    with pytest.raises(ConversionUnavailable) as excinfo:
        RejectingConverter().convert(b"\xd0\xcf\x11\xe0anything")
    message = str(excinfo.value)
    assert "not enabled" in message
    assert "Save As" not in message
    assert "open the file in Word" not in message
    # The safe alternative the user manual §4.1 already names.
    assert "re-save" in message


def test_conversion_errors_are_distinct_types():
    assert not issubclass(ConversionFailed, ConversionUnavailable)
    assert not issubclass(ConversionUnavailable, ConversionFailed)


def test_conversion_errors_share_a_base_so_callers_can_attribute_the_stage():
    assert issubclass(ConversionFailed, ConversionError)
    assert issubclass(ConversionUnavailable, ConversionError)


def test_rejecting_converter_raises_the_shared_disabled_message():
    with pytest.raises(ConversionUnavailable) as excinfo:
        RejectingConverter().convert(b"\xd0\xcf\x11\xe0anything")
    assert str(excinfo.value) == CONVERSION_DISABLED_MESSAGE


# The converter function's timeout. Task 12 will size it from
# var.converter_timeout_seconds on aws_lambda_function.converter, whose
# validation block will refuse a value that reaches CONVERT_CONFIG's read
# timeout; until that lands this constant is the only thing holding the two
# together. Raise one side without the other and botocore re-invokes a
# conversion that is still running.
_CONVERTER_FUNCTION_TIMEOUT = 120


class _FakeStore:
    """Minimal DocumentStore stand-in recording staged writes and purges.

    ``get_bytes`` answers only for the key the converter told the function to
    write; any other key raises the way a missing object would, so a fetch of
    the wrong scratch key cannot pass silently.
    """

    def __init__(
        self,
        converted: bytes = b"PK\x03\x04converted",
        get_error: Exception | None = None,
        delete_error: Exception | None = None,
    ):
        self.written: dict[str, bytes] = {}
        self.purged: list[str] = []
        self.converted_key: str | None = None
        self._converted = converted
        self._get_error = get_error
        self._delete_error = delete_error

    def put_bytes(self, key: str, data: bytes, content_type: str = "") -> None:
        self.written[key] = data

    def get_bytes(self, key: str, *, max_bytes: int | None = None) -> bytes:
        if self._get_error is not None:
            raise self._get_error
        if key != self.converted_key:
            raise KeyError(key)
        return self._converted

    def delete_prefix(self, prefix: str) -> int:
        if self._delete_error is not None:
            raise self._delete_error
        self.purged.append(prefix)
        return 2


class _FakeLambdaClient:
    def __init__(self, status: int = 200, error: str | None = None):
        self.status = status
        self.error = error
        self.calls: list[dict] = []
        self.store: _FakeStore | None = None

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        target_key = json.loads(kwargs["Payload"])["target_key"]
        body = {"ok": self.error is None}
        if self.error:
            body["error"] = self.error
        elif self.store is not None:
            # The function is what puts the converted .docx there.
            self.store.converted_key = target_key
        response = {
            "StatusCode": self.status,
            "Payload": _Payload(json.dumps(body).encode()),
        }
        if self.error:
            response["FunctionError"] = "Unhandled"
        return response


class _Payload:
    """The real invoke response carries a streaming body; the converter never
    reads it, because a function error string can echo document content."""

    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


def _converter(store, client) -> LibreOfficeLambdaConverter:
    client.store = store
    return LibreOfficeLambdaConverter(
        function_name="rmf-doc-converter",
        bucket="docs-bucket",
        store=store,
        lambda_client=client,
    )


def _scratch_prefix(client) -> str:
    """The one prefix covering both scratch keys of the invoke and nothing else.

    Asserting the two keys differ only by extension is what makes purging the
    prefix equivalent to purging exactly those two objects.
    """
    payload = json.loads(client.calls[0]["Payload"])
    prefix = payload["source_key"].removesuffix(".doc")
    assert payload["source_key"] == f"{prefix}.doc"
    assert payload["target_key"] == f"{prefix}.docx"
    return prefix


def test_lambda_backend_returns_converted_bytes():
    store, client = _FakeStore(), _FakeLambdaClient()
    assert _converter(store, client).convert(b"\xd0\xcf\x11\xe0doc") == b"PK\x03\x04converted"


def test_lambda_backend_stages_input_and_cleans_up_both_scratch_keys():
    store, client = _FakeStore(), _FakeLambdaClient()
    _converter(store, client).convert(b"\xd0\xcf\x11\xe0doc")

    assert len(store.written) == 1
    source_key = next(iter(store.written))
    assert source_key.startswith("convert-scratch/")
    assert source_key.endswith(".doc")
    # The staged key and the key the function is told to read must be the same
    # object, and the purge must cover both scratch keys — by identity, not by
    # count. delete_prefix, not delete_key: the bucket is versioned, so a plain
    # delete would leave the CUI bytes behind as a noncurrent version.
    payload = json.loads(client.calls[0]["Payload"])
    assert payload["source_key"] == source_key
    assert store.purged == [_scratch_prefix(client)]


def test_lambda_backend_passes_keys_in_the_invoke_payload():
    store, client = _FakeStore(), _FakeLambdaClient()
    _converter(store, client).convert(b"\xd0\xcf\x11\xe0doc")

    payload = json.loads(client.calls[0]["Payload"])
    assert payload["bucket"] == "docs-bucket"
    assert payload["source_key"].endswith(".doc")
    assert payload["target_key"].endswith(".docx")


def test_lambda_backend_raises_failed_when_the_function_errors():
    store, client = _FakeStore(), _FakeLambdaClient(error="soffice exited 1")
    with pytest.raises(ConversionFailed) as excinfo:
        _converter(store, client).convert(b"\xd0\xcf\x11\xe0doc")
    # Pins the `except ConversionError: raise` guard: drop it and _invoke's
    # own ConversionFailed gets re-wrapped as "conversion backend error: ...".
    assert str(excinfo.value) == "conversion function returned an error"


def test_lambda_backend_wraps_a_store_error_as_conversion_failed():
    # get_bytes is called with max_bytes=MAX_DOCX_BYTES, so an oversized
    # conversion output surfaces as ObjectTooLarge — exactly the storage type
    # the port forbids from escaping to the caller.
    store = _FakeStore(get_error=ObjectTooLarge("object exceeds the limit"))
    client = _FakeLambdaClient()
    with pytest.raises(ConversionFailed) as excinfo:
        _converter(store, client).convert(b"\xd0\xcf\x11\xe0doc")
    assert str(excinfo.value) == "conversion backend error: ObjectTooLarge"
    assert store.purged == [_scratch_prefix(client)]


def test_lambda_backend_cleans_up_scratch_even_on_failure():
    store, client = _FakeStore(), _FakeLambdaClient(error="soffice exited 1")
    with pytest.raises(ConversionFailed):
        _converter(store, client).convert(b"\xd0\xcf\x11\xe0doc")
    assert store.purged == [_scratch_prefix(client)]


def test_lambda_backend_raises_failed_on_a_non_200_status():
    store, client = _FakeStore(), _FakeLambdaClient(status=202)
    with pytest.raises(ConversionFailed) as excinfo:
        _converter(store, client).convert(b"\xd0\xcf\x11\xe0doc")
    assert str(excinfo.value) == "conversion function status 202"


def test_lambda_backend_logs_a_scratch_delete_failure_and_still_returns(capsys):
    # A broken DeleteObjectVersion grant would otherwise leave a copy of the
    # document under convert-scratch/ with nothing anywhere reporting it. It is
    # read off stderr because every other error this service emits goes there.
    store = _FakeStore(delete_error=RuntimeError("access denied"))
    client = _FakeLambdaClient()
    assert _converter(store, client).convert(b"\xd0\xcf\x11\xe0doc") == b"PK\x03\x04converted"

    lines = [json.loads(line) for line in capsys.readouterr().err.strip().splitlines()]
    assert [line["event"] for line in lines] == ["doc_convert.scratch_delete_failed"]
    assert lines[0]["error_type"] == "RuntimeError"
    assert lines[0]["prefix"] == _scratch_prefix(client)


def test_lambda_backend_rejects_oversized_input_without_invoking():
    store, client = _FakeStore(), _FakeLambdaClient()
    with pytest.raises(ConversionFailed):
        _converter(store, client).convert(b"\xd0\xcf\x11\xe0" + b"x" * MAX_DOC_BYTES)
    assert client.calls == []


def test_lambda_backend_rejects_output_that_is_not_a_docx():
    store, client = _FakeStore(converted=b"%PDF-1.7 not a docx"), _FakeLambdaClient()
    with pytest.raises(ConversionFailed):
        _converter(store, client).convert(b"\xd0\xcf\x11\xe0doc")


def test_lambda_backend_client_outwaits_the_function_and_never_retries(monkeypatch):
    """A RequestResponse invoke is not cancelled when botocore gives up, so a
    read timeout below the function's own timeout would leave concurrent
    LibreOffice runs writing to scratch keys the caller has already deleted.

    Asserted on the built client's effective config, not on the literal the
    module authored: botocore reads a config's ``max_attempts`` as a *retry*
    count and stores it as ``total_max_attempts = value + 1``, so a pin that
    reads as "one attempt" in the source can still permit a second invoke.
    """
    # No client is injected, so this is the real boto3 construction path, which
    # needs a region to resolve an endpoint. Nothing here calls AWS.
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    converter = LibreOfficeLambdaConverter(
        function_name="rmf-doc-converter", bucket="docs-bucket", store=_FakeStore()
    )

    config = converter._lambda.meta.config
    assert converter._lambda.meta.service_model.service_name == "lambda"
    assert config.read_timeout > _CONVERTER_FUNCTION_TIMEOUT
    assert config.retries["total_max_attempts"] == 1


def test_building_the_convert_client_leaves_the_shared_pin_alone(monkeypatch):
    """CONVERT_CONFIG is a module constant every later client is built from, and
    botocore rewrites a retries dict given as ``max_attempts`` in place. Stating
    the pin as ``total_max_attempts`` leaves it nothing to rewrite."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    LibreOfficeLambdaConverter(
        function_name="rmf-doc-converter", bucket="docs-bucket", store=_FakeStore()
    )

    assert CONVERT_CONFIG.retries == {"total_max_attempts": 1, "mode": "standard"}


def _config(**overrides) -> Config:
    base = dict(
        documents_bucket="docs-bucket",
        table_name="t",
        kms_key_id="k",
        parse_queue_url="q",
        bedrock_model_id="m",
        bedrock_region="us-gov-west-1",
        identity_header=None,
        bedrock_guardrail_id=None,
        bedrock_guardrail_version=None,
        doc_conversion_backend="reject",
        doc_converter_function_name=None,
    )
    base.update(overrides)
    return Config(**base)


def test_build_converter_defaults_to_rejecting():
    assert isinstance(build_converter(_config(), store=_FakeStore()), RejectingConverter)


def test_build_converter_returns_lambda_backend_when_configured(monkeypatch):
    # The factory passes no lambda_client, so this builds the real boto3 client
    # and botocore needs a region to resolve an endpoint.
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    store = _FakeStore()
    converter = build_converter(
        _config(doc_conversion_backend="lambda", doc_converter_function_name="fn"),
        store=store,
    )
    assert isinstance(converter, LibreOfficeLambdaConverter)
    # documents_bucket and table_name are both plain strings, so a swap between
    # them only surfaces at runtime as a ConversionFailed against a bucket that
    # does not exist.
    assert converter._function_name == "fn"
    assert converter._bucket == "docs-bucket"
    assert converter._store is store


def test_build_converter_rejects_lambda_backend_without_a_function_name():
    with pytest.raises(ValueError, match="DOC_CONVERTER_FUNCTION_NAME"):
        build_converter(_config(doc_conversion_backend="lambda"), store=_FakeStore())


def test_build_converter_rejects_an_unknown_backend():
    """Fail closed: a typo'd or retired backend name must not fall through to a
    converter the deployment did not ask for."""
    with pytest.raises(ValueError, match="unknown DOC_CONVERSION_BACKEND"):
        build_converter(_config(doc_conversion_backend="libreoffice"), store=_FakeStore())


def test_conversion_enabled_reflects_the_backend():
    assert _config().conversion_enabled is False
    assert (
        _config(
            doc_conversion_backend="lambda", doc_converter_function_name="fn"
        ).conversion_enabled
        is True
    )


def test_conversion_enabled_is_an_allow_list_not_a_reject_check():
    """The gate decides whether a .doc upload is accepted into the CUI bucket,
    so only backends that can actually convert may report enabled."""
    for backend in ("", "Reject", "REJECT", " reject", "none", "off", "false"):
        assert _config(doc_conversion_backend=backend).conversion_enabled is False


def test_every_backend_declares_whether_it_converts():
    """The table is the single source for both the typo check and the .doc
    gate. A mapping forces an explicit converts-or-not answer for each backend,
    where a derived set would have let a future non-converting mode (dry-run,
    audit, aliased reject) inherit "converts" by omission and silently report
    the upload gate as open."""
    assert _BACKENDS == {"reject": False, "lambda": True}
    assert all(isinstance(converts, bool) for converts in _BACKENDS.values())


def test_build_converter_handles_exactly_the_declared_backends(monkeypatch):
    """Drift guard: a name the table declares but the factory cannot build
    would pass config validation and then fail at Deps.build() in the Lambda.

    The region is set because the lambda backend constructs a real boto3 client
    when no client is injected; nothing here calls AWS.
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-gov-west-1")
    for backend in _BACKENDS:
        config = _config(doc_conversion_backend=backend, doc_converter_function_name="fn")
        assert build_converter(config, store=_FakeStore()) is not None


def test_conversion_enabled_is_closed_for_a_backend_missing_from_the_table():
    """A Config built directly bypasses from_env's validation, so the gate must
    refuse an undeclared backend rather than assume it converts."""
    assert _config(doc_conversion_backend="dry-run").conversion_enabled is False


@pytest.fixture
def config_env(monkeypatch):
    for key, value in {
        "DOCUMENTS_BUCKET": "docs-bucket",
        "TABLE_NAME": "t",
        "KMS_KEY_ID": "k",
        "PARSE_QUEUE_URL": "q",
        "BEDROCK_MODEL_ID": "m",
        "AWS_REGION": "us-gov-west-1",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("DOC_CONVERSION_BACKEND", raising=False)
    monkeypatch.delenv("DOC_CONVERTER_FUNCTION_NAME", raising=False)


def test_from_env_defaults_the_conversion_backend_to_reject(config_env):
    config = Config.from_env()
    assert config.doc_conversion_backend == "reject"
    assert config.doc_converter_function_name is None
    assert config.conversion_enabled is False


def test_from_env_treats_an_empty_conversion_backend_as_unset(config_env, monkeypatch):
    """Terraform is not the only writer of a Lambda env var; an operator
    clearing the variable in the console must land on the fail-closed default,
    not on a value every handler then chokes on at cold start."""
    monkeypatch.setenv("DOC_CONVERSION_BACKEND", "")
    monkeypatch.setenv("DOC_CONVERTER_FUNCTION_NAME", "")

    config = Config.from_env()
    assert config.doc_conversion_backend == "reject"
    assert config.doc_converter_function_name is None
    assert config.conversion_enabled is False


def test_from_env_reads_the_configured_conversion_backend(config_env, monkeypatch):
    monkeypatch.setenv("DOC_CONVERSION_BACKEND", "lambda")
    monkeypatch.setenv("DOC_CONVERTER_FUNCTION_NAME", "rmf-doc-converter")

    config = Config.from_env()
    assert config.doc_conversion_backend == "lambda"
    assert config.doc_converter_function_name == "rmf-doc-converter"
    assert config.conversion_enabled is True


def test_from_env_rejects_an_unknown_conversion_backend(config_env, monkeypatch):
    """An unrecognized value is a deployment error; it belongs with the other
    required-variable checks, not as a ValueError out of dependency assembly."""
    monkeypatch.setenv("DOC_CONVERSION_BACKEND", "libreoffice")

    with pytest.raises(ConfigError, match="DOC_CONVERSION_BACKEND"):
        Config.from_env()
