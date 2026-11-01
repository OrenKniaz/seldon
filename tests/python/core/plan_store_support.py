def draft_node(identity, kind, fields, parents):
    return dict(
        id=identity, kind=kind, title=identity, text=identity, fields=fields, parents=parents
    )


def mock_catalog():
    return dict(
        schema="dot-reviewer-catalog-v1",
        id="RC-00000000-0000-0000-0000-000000000001",
        observedAt="2026-10-15T10:00:00+00:00",
        providers=[
            dict(
                provider="codex",
                executable="C:/mock/codex.exe",
                executableDigest="a" * 64,
                version="mock-1",
                installed=True,
                authentication="available",
                discovery="observed",
                source=dict(
                    kind="supported-list", reference="mock:supported-list", digest="b" * 64
                ),
                choices=[
                    dict(
                        model="gpt-6.1-sol",
                        effort=dict(kind="named", value="high"),
                        availability="available",
                        reviewPathSupported=True,
                        reason="Mock only",
                    )
                ],
                reason="Injected mock; no discovery or provider execution",
            )
        ],
    )


def mock_choice(catalog, origin=None):
    choice = catalog["providers"][0]["choices"][0]
    authority = (
        None
        if origin is None
        else dict(
            approvalId="mock:approval",
            eventId=1,
            origin=origin,
            statement="Select exact tuple",
            sourceRef="mock:direct-or-delegated",
            authorizedScopeDigest="c" * 64,
        )
    )
    return draft_node(
        "CHOICE",
        "decision",
        dict(
            schema="dot-reviewer-selection-v1",
            catalogId=catalog["id"],
            catalogDigest=catalog["digest"],
            reviewers=[
                dict(
                    name="primary",
                    provider="codex",
                    **{key: choice[key] for key in ("choiceId", "model", "effort")},
                )
            ],
            stages=["plan"],
            assurance=dict(
                independentRequired=True,
                minIndependentReviewers=1,
                crossProviderRequired=True,
                basisRefs=["mock:policy"],
            ),
            confirmation=authority,
            recommendation=dict(
                choiceIds=[choice["choiceId"]],
                reason="Exact mocked choice",
                catchRateEvidenceRefs=[],
            ),
        ),
        ["V1"],
    )
