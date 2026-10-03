"""Candidate scoring and the auto-accept rule (SPEC §7.2 step 4)."""

import json

import pytest

from apps.metadata.scoring import (
    DEFAULT_MARGIN,
    DEFAULT_THRESHOLD,
    DEFAULT_WEIGHTS,
    Candidate,
    MatchQuery,
    decide,
    runtime_score,
    score_candidates,
    title_similarity,
    year_score,
)

MATRIX = Candidate(603, "movie", "The Matrix", "The Matrix", year=1999, popularity=80.0)
RELOADED = Candidate(604, "movie", "The Matrix Reloaded", year=2003, popularity=40.0)
REVOLUTIONS = Candidate(605, "movie", "The Matrix Revolutions", year=2003, popularity=35.0)
RESURRECTIONS = Candidate(624860, "movie", "The Matrix Resurrections", year=2021, popularity=60.0)
MATRIX_FAMILY = (RELOADED, MATRIX, RESURRECTIONS, REVOLUTIONS)

WADJDA = Candidate(129112, "movie", "Wadjda", "وجدة", year=2012, popularity=9.0)
THE_MESSAGE = Candidate(
    1,
    "movie",
    "The Message",
    "الرسالة",
    alternative_titles=("Mohammad, Messenger of God",),
    year=1976,
)
BREAKING_BAD = Candidate(1396, "tv", "Breaking Bad", "Breaking Bad", year=2008, popularity=300.0)


def test_spec_defaults() -> None:
    assert (DEFAULT_THRESHOLD, DEFAULT_MARGIN) == (0.85, 0.10)
    weights = DEFAULT_WEIGHTS
    assert (weights.title, weights.year, weights.runtime, weights.popularity) == (
        0.60,
        0.25,
        0.10,
        0.05,
    )


@pytest.mark.parametrize(
    ("parsed", "provider", "expected"),
    [
        (1999, 1999, 1.0),
        (1999, 2000, 0.6),
        (1999, 1998, 0.6),
        (1999, 2001, 0.0),
        (1999, None, 0.0),
        (None, 1999, None),
    ],
)
def test_year_score(parsed: int | None, provider: int | None, expected: float | None) -> None:
    assert year_score(parsed, provider) == expected


@pytest.mark.parametrize(
    ("probed", "provider", "expected"),
    [
        (136.0, 136, 1.0),
        (131.0, 136, 1.0),
        (141.0, 136, 1.0),
        (130.9, 136, 0.5),
        (126.0, 136, 0.5),
        (125.9, 136, 0.0),
        (0.5, 136, 0.0),
        (None, 136, None),
        (136.0, None, None),
        (136.0, 0, None),
    ],
)
def test_runtime_score(probed: float | None, provider: int | None, expected: float | None) -> None:
    assert runtime_score(probed, provider) == expected


@pytest.mark.parametrize(
    ("title", "candidate", "similarity", "matched"),
    [
        ("The Matrix", MATRIX, 1.0, "The Matrix"),
        ("the matrix", MATRIX, 1.0, "The Matrix"),
        ("Matrix", MATRIX, 1.0, "The Matrix"),
        # token_set_ratio: a subset of the words is a full match (years break the tie).
        ("The Matrix", RELOADED, 1.0, "The Matrix Reloaded"),
        # Arabic: the original title, teh marbuta and the article fold away.
        ("وجدة", WADJDA, 1.0, "وجدة"),
        ("وجده", WADJDA, 1.0, "وجدة"),
        ("الرسالة", THE_MESSAGE, 1.0, "الرسالة"),
        ("رسالة", THE_MESSAGE, 1.0, "الرسالة"),
        ("Mohammad Messenger of God", THE_MESSAGE, 1.0, "Mohammad, Messenger of God"),
        # Hamza, madda and alef maksura spellings meet in the middle.
        (
            "امير الظلام",
            Candidate(2, "movie", "Prince of Darkness", "أمير الظلام"),
            1.0,
            "أمير الظلام",
        ),
        (
            "مصطفي",
            Candidate(3, "movie", "Mostafa", alternative_titles=("مصطفى",)),
            1.0,
            "مصطفى",
        ),
    ],
)
def test_title_similarity_takes_the_best_title(
    title: str, candidate: Candidate, similarity: float, matched: str
) -> None:
    assert title_similarity(title, candidate) == (similarity, matched)


def test_unrelated_titles_are_dissimilar() -> None:
    value, _ = title_similarity("Breaking Bad", MATRIX)
    assert value < 0.5
    assert title_similarity("", MATRIX)[0] == 0.0


@pytest.mark.parametrize(
    ("query", "candidates", "reason", "accepted_id"),
    [
        # The year separates a film from its sequels.
        (MatchQuery("The Matrix", 1999), MATRIX_FAMILY, "auto_accepted", 603),
        (MatchQuery("The Matrix", 2000), MATRIX_FAMILY, "auto_accepted", 603),
        (MatchQuery("The Matrix Resurrections", 2021), MATRIX_FAMILY, "auto_accepted", 624860),
        # Without a year the whole family scores alike: a human decides.
        (MatchQuery("The Matrix"), MATRIX_FAMILY, "ambiguous", None),
        # A series named without a year and a single candidate.
        (MatchQuery("Breaking Bad"), (BREAKING_BAD,), "auto_accepted", 1396),
        # Arabic file names against the original title.
        (MatchQuery("وجدة", 2012), (WADJDA, MATRIX), "auto_accepted", 129112),
        (
            MatchQuery("الرسالة", 1976),
            (THE_MESSAGE,),
            "auto_accepted",
            1,
        ),
        # Nothing similar enough.
        (MatchQuery("Breaking Bad", 2008), MATRIX_FAMILY, "below_threshold", None),
        (MatchQuery("Anything"), (), "no_candidates", None),
    ],
)
def test_decide(
    query: MatchQuery,
    candidates: tuple[Candidate, ...],
    reason: str,
    accepted_id: int | None,
) -> None:
    decision = decide(query, candidates)
    assert decision.reason == reason
    accepted = decision.accepted
    assert (accepted.candidate.provider_id if accepted else None) == accepted_id
    assert decision.needs_review is (accepted_id is None)


def test_scores_follow_the_spec_formula_when_every_signal_is_known() -> None:
    candidate = Candidate(603, "movie", "The Matrix", year=2000, runtime_min=128, popularity=40.0)
    other = Candidate(9, "movie", "Something Else", popularity=80.0)
    query = MatchQuery("The Matrix", 1999, runtime_min=136.2)
    scored = {s.candidate.provider_id: s for s in score_candidates(query, (candidate, other))}
    # 0.60 * 1.0 + 0.25 * 0.6 + 0.10 * 0.5 + 0.05 * (40 / 80)
    assert scored[603].score == pytest.approx(0.825)
    breakdown = scored[603].breakdown
    assert (breakdown.title, breakdown.year, breakdown.runtime, breakdown.popularity) == (
        1.0,
        0.6,
        0.5,
        0.5,
    )


def test_unknown_signals_are_left_out_and_the_rest_rescaled() -> None:
    query = MatchQuery("Breaking Bad")  # no year, no probe
    (scored,) = score_candidates(query, (BREAKING_BAD,))
    assert scored.breakdown.year is None
    assert scored.breakdown.runtime is None
    assert scored.score == 1.0
    # The literal formula counts the unknown year as 0 and can never auto-accept.
    (literal,) = score_candidates(query, (BREAKING_BAD,), ignore_unknown=False)
    assert literal.score == pytest.approx(0.65)
    assert decide(query, (BREAKING_BAD,), ignore_unknown=False).reason == "below_threshold"


def test_a_runtime_mismatch_costs_points() -> None:
    near = Candidate(1, "movie", "Inception", year=2010, runtime_min=148, popularity=10.0)
    far = Candidate(1, "movie", "Inception", year=2010, runtime_min=15, popularity=10.0)
    query = MatchQuery("Inception", 2010, runtime_min=147.5)
    (good,) = score_candidates(query, (near,))
    (bad,) = score_candidates(query, (far,))
    assert good.score == 1.0
    assert bad.score == pytest.approx(0.9)


def test_margin_boundary_is_inclusive_despite_float_rounding() -> None:
    # popularity 0 everywhere: unknown, so each score is exactly the title similarity.
    similarities = {"a": 0.95, "b": 0.85}
    candidates = (Candidate(1, "movie", "a"), Candidate(2, "movie", "b"))

    def fixed(_query: str, title: str) -> float:
        return similarities[title]

    decision = decide(MatchQuery("x"), candidates, similarity=fixed)
    assert [s.score for s in decision.candidates] == [0.95, 0.85]
    assert decision.reason == "auto_accepted"
    assert decide(MatchQuery("x"), candidates, similarity=fixed, margin=0.11).reason == "ambiguous"
    assert (
        decide(MatchQuery("x"), candidates, similarity=fixed, threshold=0.96).reason
        == "below_threshold"
    )


def test_review_shortlist_is_the_top_five_best_first() -> None:
    candidates = [
        Candidate(i, "movie", f"The Matrix Part {i}", year=1990 + i, popularity=float(i))
        for i in range(1, 9)
    ]
    decision = decide(MatchQuery("The Matrix Part 7", 1997), candidates)
    ids = [s.candidate.provider_id for s in decision.candidates]
    assert len(ids) == 5
    assert ids[0] == 7
    scores = [s.score for s in decision.candidates]
    assert scores == sorted(scores, reverse=True)
    assert len(decide(MatchQuery("x"), candidates, limit=2).candidates) == 2


def test_duplicate_candidates_count_once() -> None:
    decision = decide(MatchQuery("Breaking Bad"), (BREAKING_BAD, BREAKING_BAD))
    assert len(decision.candidates) == 1
    assert decision.reason == "auto_accepted"


def test_decision_json_is_plain_data_for_the_review_queue() -> None:
    decision = decide(MatchQuery("The Matrix"), MATRIX_FAMILY)
    data = json.loads(json.dumps(decision.to_json()))
    assert data["reason"] == "ambiguous"
    assert data["confidence"] == decision.confidence
    first = data["candidates"][0]
    assert set(first) >= {"provider", "kind", "id", "title", "year", "score", "breakdown"}
    assert set(first["breakdown"]) == {"title", "year", "runtime", "popularity", "matched_title"}


def test_candidate_from_tmdb_movie_details() -> None:
    details = {
        "id": 603,
        "title": "The Matrix",
        "original_title": "The Matrix",
        "release_date": "1999-03-31",
        "runtime": 136,
        "popularity": 81.5,
        "poster_path": "/p.jpg",
        "overview": "A hacker learns the truth.",
        "alternative_titles": {
            "titles": [{"iso_3166_1": "SA", "title": "المصفوفة", "type": ""}, {"title": ""}]
        },
        "translations": {
            "translations": [
                {"iso_639_1": "ar", "data": {"title": "ذا ماتريكس", "overview": ""}},
                {"iso_639_1": "en", "data": {"title": "The Matrix"}},
                {"iso_639_1": "fr", "data": {"title": ""}},
            ]
        },
    }
    candidate = Candidate.from_tmdb(details, "movie")
    assert candidate == Candidate(
        provider_id=603,
        kind="movie",
        title="The Matrix",
        original_title="The Matrix",
        alternative_titles=("المصفوفة", "ذا ماتريكس"),
        year=1999,
        runtime_min=136,
        popularity=81.5,
        poster_path="/p.jpg",
        overview="A hacker learns the truth.",
    )
    assert title_similarity("المصفوفة", candidate) == (1.0, "المصفوفة")


def test_candidate_from_tmdb_tv_search_result_and_details() -> None:
    result = {
        "id": 1396,
        "name": "Breaking Bad",
        "original_name": "Breaking Bad",
        "first_air_date": "2008-01-20",
        "popularity": 300,
    }
    candidate = Candidate.from_tmdb(result, "tv")
    assert (candidate.title, candidate.year, candidate.runtime_min) == ("Breaking Bad", 2008, None)
    assert candidate.popularity == 300.0
    details = result | {
        "episode_run_time": [47, 45],
        "alternative_titles": {"results": [{"title": "بريكنج باد"}]},
        "translations": {"translations": [{"data": {"name": "Breaking Bad"}}]},
    }
    detailed = Candidate.from_tmdb(details, "tv")
    assert detailed.runtime_min == 47
    assert detailed.alternative_titles == ("بريكنج باد",)


def test_candidate_from_sparse_tmdb_data() -> None:
    candidate = Candidate.from_tmdb({"id": "7", "release_date": "", "runtime": 0}, "movie")
    assert candidate == Candidate(provider_id=7, kind="movie", title="")
    assert candidate.titles() == ()
