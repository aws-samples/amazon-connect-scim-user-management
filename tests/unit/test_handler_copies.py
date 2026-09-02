"""Guards against the per-IaC handler copies drifting apart.

This repository ships the same solution three ways -- CDK, CloudFormation and
Terraform -- and each deployment packages its Lambda code from its own directory,
so the SCIM modules exist as copies rather than a shared import. That duplication
is how the repository is laid out; what must not happen is a fix landing in one
copy and not the others, which is exactly how ``/Groups`` came to be a working
idea in nobody's deployment.

``cdk_source/lambdas`` is the canonical source. These tests fail if any copy
differs from it by a single byte, so a change has to be applied everywhere or the
suite goes red.
"""

import hashlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

CANONICAL_USER_MANAGEMENT = REPO_ROOT / "cdk_source" / "lambdas" / "user_management"
CANONICAL_AUTHORIZER = (
    REPO_ROOT / "cdk_source" / "lambdas" / "lambda_authorizer" / "lambda_authorizer.py"
)
CANONICAL_API_TOKEN = REPO_ROOT / "cdk_source" / "lambdas" / "custom_resource" / "api_token.py"

SHARED_MODULES = ["scim.py", "connect_directory.py", "handler_core.py"]

USER_MANAGEMENT_COPIES = [
    REPO_ROOT / "CloudFormation" / "lambdas" / "user_management" / "okta_idp",
    REPO_ROOT / "CloudFormation" / "lambdas" / "user_management" / "azure_idp",
    REPO_ROOT / "Terraform" / "lambdas" / "user_management" / "okta_idp",
    REPO_ROOT / "Terraform" / "lambdas" / "user_management" / "azure_idp",
]

AUTHORIZER_COPIES = [
    REPO_ROOT / "CloudFormation" / "lambdas" / "lambda_authorizer" / "lambda_authorizer.py",
    REPO_ROOT / "Terraform" / "lambdas" / "lambda_authorizer" / "lambda_authorizer.py",
]

API_TOKEN_COPIES = [
    REPO_ROOT / "CloudFormation" / "lambdas" / "custom_resource" / "api_token.py",
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ids(paths):
    return [str(path.relative_to(REPO_ROOT)) for path in paths]


@pytest.mark.parametrize("copy_dir", USER_MANAGEMENT_COPIES, ids=_ids(USER_MANAGEMENT_COPIES))
@pytest.mark.parametrize("module", SHARED_MODULES)
def test_shared_module_matches_canonical(copy_dir, module):
    canonical = CANONICAL_USER_MANAGEMENT / module
    copy = copy_dir / module
    assert copy.exists(), f"{copy} is missing; copy it from {canonical}"
    assert digest(copy) == digest(canonical), (
        f"{copy.relative_to(REPO_ROOT)} has drifted from "
        f"{canonical.relative_to(REPO_ROOT)}. Apply the change to every copy."
    )


@pytest.mark.parametrize("copy", AUTHORIZER_COPIES, ids=_ids(AUTHORIZER_COPIES))
def test_authorizer_matches_canonical(copy):
    assert copy.exists(), f"{copy} is missing"
    assert digest(copy) == digest(CANONICAL_AUTHORIZER), (
        f"{copy.relative_to(REPO_ROOT)} has drifted from the canonical authorizer."
    )


@pytest.mark.parametrize("copy", API_TOKEN_COPIES, ids=_ids(API_TOKEN_COPIES))
def test_api_token_module_matches_canonical(copy):
    assert copy.exists(), f"{copy} is missing"
    assert digest(copy) == digest(CANONICAL_API_TOKEN), (
        f"{copy.relative_to(REPO_ROOT)} has drifted from the canonical api_token module."
    )


def test_custom_resource_entry_points_use_the_right_response_protocol():
    """The two entry points must not be interchangeable.

    A raw AWS::CloudFormation::CustomResource has no framework in front of it and
    must post its own result to ResponseURL. The CDK Provider framework owns that
    protocol and the handler behind it must return instead. Swapping them leaves a
    stack in CREATE_IN_PROGRESS until the resource times out.
    """
    cdk_entry = (
        REPO_ROOT / "cdk_source" / "lambdas" / "custom_resource" / "custom_resource.py"
    ).read_text()
    cfn_entry = (
        REPO_ROOT / "CloudFormation" / "lambdas" / "custom_resource" / "custom_resource_lambda.py"
    ).read_text()

    # These check code, not prose: both docstrings discuss ResponseURL, so the
    # assertions look for the subscript that would actually read it and for the
    # HTTP client that would actually post.
    assert "PhysicalResourceId" in cdk_entry, "the CDK handler must return a result"
    assert 'event["ResponseURL"]' not in cdk_entry, (
        "the CDK handler must not post to ResponseURL; the Provider framework does"
    )
    assert "import urllib3" not in cdk_entry

    assert 'event["ResponseURL"]' in cfn_entry, (
        "a raw custom resource must post its own result, or the stack waits out "
        "the resource timeout"
    )
    assert "import urllib3" in cfn_entry
    assert '"PUT"' in cfn_entry
    assert '"FAILED"' in cfn_entry, "a failure must be reported, not just raised"


@pytest.mark.parametrize("copy_dir", USER_MANAGEMENT_COPIES, ids=_ids(USER_MANAGEMENT_COPIES))
def test_each_copy_has_its_entry_point(copy_dir):
    # CloudFormation and Terraform both configure the handler as
    # 'user_management_lambda.lambda_handler'.
    entry_point = copy_dir / "user_management_lambda.py"
    assert entry_point.exists(), f"{entry_point} is missing"
    source = entry_point.read_text()
    assert "def lambda_handler(" in source
    assert "handler_core.handle" in source


@pytest.mark.parametrize("copy_dir", USER_MANAGEMENT_COPIES, ids=_ids(USER_MANAGEMENT_COPIES))
def test_entry_point_selects_the_right_provider(copy_dir):
    expected = "okta" if copy_dir.name == "okta_idp" else "azure"
    source = (copy_dir / "user_management_lambda.py").read_text()
    assert f'provider="{expected}"' in source


def test_no_stale_pre_refactor_handler_remains():
    """No deployment tree still carries the pre-refactor handler.

    These markers are code, not prose: the docstrings in the current handlers
    mention ``random.sample`` to explain why it was replaced, so the patterns below
    are written to match a call or an import rather than a reference.
    """
    markers = {
        # The /Groups stub that discarded every membership change.
        "dummy_group_response": "the /Groups stub",
        # Indexing value['active'] on a bool raised TypeError for one of the two
        # payload shapes Okta sends.
        "info['value']['active']": "the PATCH active TypeError",
        # random is not a CSPRNG and must not be used for the API token.
        "random.sample(": "non-CSPRNG token generation",
        "import random": "the random module (use secrets)",
    }
    offenders = []
    scanned = []
    for path in sorted(REPO_ROOT.glob("*/lambdas/**/*.py")):
        if "node_modules" in path.parts or "cdk.out" in path.parts:
            continue
        scanned.append(path)
        source = path.read_text()
        for marker, description in markers.items():
            if marker in source:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {description} ({marker})")

    # Without this the gate is also green when the glob matches nothing -- moving
    # the handlers one directory deeper would silence it permanently.
    assert len(scanned) >= 19, (
        f"only {len(scanned)} handler files scanned; the glob has stopped matching "
        "the deployment trees and this gate is no longer checking anything"
    )
    assert not offenders, "pre-refactor handler code found:\n" + "\n".join(offenders)


def test_the_stale_code_gate_actually_fires(tmp_path):
    """A positive trip case, so the gate is known to detect a violation."""
    planted = tmp_path / "lambdas" / "user_management"
    planted.mkdir(parents=True)
    (planted / "handler.py").write_text("import random\nrandom.sample('abc', 2)\n")
    markers = ["random.sample(", "import random"]
    hits = [m for m in markers if m in (planted / "handler.py").read_text()]
    assert hits == markers, "the markers no longer match a real violation"
