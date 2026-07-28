"""Tests for resolve_identity's precedence: verified sources before headers.

Order under test: JWT claims (HTTP API JWT/Cognito authorizer) > SigV4 IAM
principal > the legacy trusted-proxy header > "anonymous". The header is
client-supplied and must never win over a cryptographically verified source.

Also covers parse_body's base64 handling: a gateway can deliver the body
base64-encoded with isBase64Encoded set (the toolbox front door does, because
its S3-serving mode forces binary_media_types = ["*/*"]).
"""

from __future__ import annotations

import base64
import json

import pytest

from rmf_migrator.common.http import HttpError, parse_body, resolve_identity


def _event(*, claims=None, iam=None, headers=None):
    authorizer = {}
    if claims is not None:
        authorizer["jwt"] = {"claims": claims}
    if iam is not None:
        authorizer["iam"] = iam
    return {
        "requestContext": {"authorizer": authorizer},
        "headers": headers or {},
    }


def test_claims_beat_a_conflicting_header():
    event = _event(claims={"email": "verified@example.mil"}, headers={"X-Remote-User": "spoofed"})
    assert resolve_identity(event, "X-Remote-User") == "verified@example.mil"


def test_claims_beat_the_iam_principal():
    event = _event(
        claims={"email": "verified@example.mil"},
        iam={"userArn": "arn:aws:iam::123456789012:role/reviewer"},
    )
    assert resolve_identity(event, None) == "verified@example.mil"


def test_claims_prefer_email_over_cognito_username_and_sub():
    event = _event(claims={"email": "e@x.mil", "cognito:username": "u", "sub": "s"})
    assert resolve_identity(event, None) == "e@x.mil"


def test_claims_fall_back_to_cognito_username_without_email():
    event = _event(claims={"cognito:username": "u", "sub": "s"})
    assert resolve_identity(event, None) == "u"


def test_claims_fall_back_to_sub_without_email_or_username():
    event = _event(claims={"sub": "s"})
    assert resolve_identity(event, None) == "s"


def test_iam_principal_beats_the_header():
    event = _event(
        iam={"userArn": "arn:aws:iam::123456789012:role/reviewer"},
        headers={"X-Remote-User": "spoofed"},
    )
    assert resolve_identity(event, "X-Remote-User") == "arn:aws:iam::123456789012:role/reviewer"


def test_iam_falls_back_to_caller_id_without_user_arn():
    event = _event(iam={"callerId": "AIDAEXAMPLE"})
    assert resolve_identity(event, None) == "AIDAEXAMPLE"


def test_header_used_only_when_no_claims_or_iam_present():
    event = _event(headers={"X-Remote-User": "jdoe"})
    assert resolve_identity(event, "X-Remote-User") == "jdoe"


def test_header_lookup_is_case_insensitive():
    event = _event(headers={"x-remote-user": "jdoe"})
    assert resolve_identity(event, "X-Remote-User") == "jdoe"


def test_empty_claims_and_iam_dicts_fall_through_to_header():
    event = _event(claims={}, iam={}, headers={"X-Remote-User": "jdoe"})
    assert resolve_identity(event, "X-Remote-User") == "jdoe"


def test_anonymous_when_nothing_present():
    assert resolve_identity(_event(), "X-Remote-User") == "anonymous"
    assert resolve_identity(_event(), None) == "anonymous"


def test_header_present_but_no_identity_header_configured_stays_anonymous():
    event = _event(headers={"X-Remote-User": "jdoe"})
    assert resolve_identity(event, None) == "anonymous"


def test_missing_request_context_does_not_raise():
    assert resolve_identity({"headers": {}}, "X-Remote-User") == "anonymous"


def test_none_request_context_does_not_raise():
    event = {"requestContext": None, "headers": {}}
    assert resolve_identity(event, "X-Remote-User") == "anonymous"


def test_none_authorizer_does_not_raise():
    event = {"requestContext": {"authorizer": None}, "headers": {}}
    assert resolve_identity(event, "X-Remote-User") == "anonymous"


def test_none_headers_does_not_raise():
    event = {"requestContext": {"authorizer": {}}, "headers": None}
    assert resolve_identity(event, "X-Remote-User") == "anonymous"


def test_missing_headers_key_does_not_raise():
    event = {"requestContext": {"authorizer": {}}}
    assert resolve_identity(event, "X-Remote-User") == "anonymous"


def test_completely_empty_event_does_not_raise():
    assert resolve_identity({}, "X-Remote-User") == "anonymous"
    assert resolve_identity({}, None) == "anonymous"


def test_parse_body_plain_json():
    event = {"body": json.dumps({"name": "Sys"})}
    assert parse_body(event) == {"name": "Sys"}


def test_parse_body_decodes_base64_when_flagged():
    event = {
        "body": base64.b64encode(json.dumps({"name": "Sys"}).encode()).decode(),
        "isBase64Encoded": True,
    }
    assert parse_body(event) == {"name": "Sys"}


def test_parse_body_rejects_undecodable_base64():
    event = {"body": "not-valid-base64!!!", "isBase64Encoded": True}
    with pytest.raises(HttpError) as exc:
        parse_body(event)
    assert exc.value.status == 400


def test_parse_body_base64_flag_with_empty_body_is_fine():
    assert parse_body({"body": "", "isBase64Encoded": True}) == {}
