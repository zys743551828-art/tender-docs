import json

from packages.domain.review_identity import identities
from packages.domain.runtime import ROOT


def test_protocol_identity_fixed_vectors():
    vectors = json.loads(
        (ROOT / "packages/contracts/schemas/review-identity-vectors.json").read_text()
    )
    for vector in vectors["vectors"]:
        assert identities(vector["manifest"], vector["rerun_nonce"]) == vector["expected"], vector[
            "name"
        ]
