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
CANONICAL_CUSTOM_RESOURCE = (
    REPO_ROOT / "cdk_source" / "lambdas" / "custom_resource" / "custom_resource.py"
)

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

CUSTOM_RESOURCE_COPIES = [
    REPO_ROOT / "CloudFormation" / "lambdas" / "custom_resource" / "custom_resource_lambda.py",
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


@pytest.mark.parametrize("copy", CUSTOM_RESOURCE_COPIES, ids=_ids(CUSTOM_RESOURCE_COPIES))
def test_custom_resource_matches_canonical(copy):
    assert copy.exists(), f"{copy} is missing"
    assert digest(copy) == digest(CANONICAL_CUSTOM_RESOURCE), (
        f"{copy.relative_to(REPO_ROOT)} has drifted from the canonical custom resource."
    )


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
    for path in sorted(REPO_ROOT.glob("*/lambdas/**/*.py")):
        if "node_modules" in path.parts:
            continue
        source = path.read_text()
        for marker, description in markers.items():
            if marker in source:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {description} ({marker})")
    assert not offenders, "pre-refactor handler code found:\n" + "\n".join(offenders)
