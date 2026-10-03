"""Score provider candidates against a parsed media file (SPEC §7.2 step 4).

    score = 0.60 * title + 0.25 * year + 0.10 * runtime + 0.05 * popularity

- `title` is rapidfuzz `token_set_ratio` / 100 between the normalised parsed
  title and the best of the candidate's title, original title and alternative
  titles (translated titles count as alternative titles, so an Arabic file name
  can match an English TMDB entry). Normalisation is `apps.search.normalize`.
- `year` is 1.0 for the same year, 0.6 for one year apart, otherwise 0.
- `runtime` is 1.0 when the probed and the provider runtime are at most 5
  minutes apart, 0.5 at most 10 minutes, otherwise 0.
- `popularity` is the candidate's popularity divided by the highest popularity
  among the candidates.

A signal that cannot be measured (no year in the path, no probed or no
provider runtime, no popularity anywhere) is left out and the remaining weights
are rescaled, so it neither helps nor hurts. With every signal known this is
exactly the SPEC formula; without the rescaling no series file without a year
(`Breaking.Bad.S01E01.mkv`) could ever reach the auto-accept threshold. Pass
`ignore_unknown=False` for the literal formula (unknown counts as 0).

`decide` auto-accepts the best candidate when its score is at least
`threshold` and leads the runner-up by at least `margin` (both are settings,
defaulting to 0.85 and 0.10); otherwise the top five, each with its score
breakdown, go to a `MatchReview`. Note that `token_set_ratio` is 1.0 whenever
one title's words are a subset of the other's ("The Matrix" against "The Matrix
Reloaded"), so sequels are told apart by the year and runtime signals. A
different similarity can be passed in.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from rapidfuzz import fuzz

from apps.search.normalize import normalize

__all__ = [
    "DEFAULT_MARGIN",
    "DEFAULT_THRESHOLD",
    "DEFAULT_WEIGHTS",
    "TOP_CANDIDATES",
    "Candidate",
    "CandidateKind",
    "MatchDecision",
    "MatchQuery",
    "ScoreBreakdown",
    "ScoredCandidate",
    "Similarity",
    "Weights",
    "decide",
    "runtime_score",
    "score_candidates",
    "title_similarity",
    "token_set_similarity",
    "year_score",
]

type CandidateKind = Literal["movie", "tv"]
type DecisionReason = Literal["auto_accepted", "below_threshold", "ambiguous", "no_candidates"]
#: Similarity of two normalised titles, from 0.0 to 1.0.
type Similarity = Callable[[str, str], float]

DEFAULT_THRESHOLD: Final = 0.85
DEFAULT_MARGIN: Final = 0.10
#: How many candidates a review shows.
TOP_CANDIDATES: Final = 5
# Scores are rounded so that 0.95 - 0.85 compares as 0.10, not 0.0999…
_DIGITS: Final = 4
_EPSILON: Final = 1e-9


@dataclass(frozen=True, slots=True)
class Weights:
    """Weight of each signal; the SPEC values are the defaults."""

    title: float = 0.60
    year: float = 0.25
    runtime: float = 0.10
    popularity: float = 0.05

    def to_json(self) -> dict[str, float]:
        return {
            "title": self.title,
            "year": self.year,
            "runtime": self.runtime,
            "popularity": self.popularity,
        }


DEFAULT_WEIGHTS: Final = Weights()


@dataclass(frozen=True, slots=True)
class MatchQuery:
    """What the media file says about itself: the parsed title and year, the probed runtime."""

    title: str
    year: int | None = None
    runtime_min: float | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    """A provider entry that might be the file's movie or series."""

    provider_id: int
    kind: CandidateKind
    title: str
    original_title: str | None = None
    alternative_titles: tuple[str, ...] = ()
    year: int | None = None
    runtime_min: int | None = None
    popularity: float = 0.0
    poster_path: str | None = None
    overview: str = ""

    @classmethod
    def from_tmdb(cls, data: Mapping[str, Any], kind: CandidateKind) -> "Candidate":
        """Build from a TMDB search result or details response (`kind` "movie" or "tv").

        Details responses add the runtime and, when appended, the alternative and
        translated titles; search results carry neither.
        """
        if kind == "movie":
            title = _str(data.get("title"))
            original = _str(data.get("original_title"))
            year = _year(data.get("release_date"))
            runtime = _positive_int(data.get("runtime"))
            alternatives = _nested_titles(data.get("alternative_titles"), "titles", "title")
            translated = _translated_titles(data.get("translations"), "title")
        else:
            title = _str(data.get("name"))
            original = _str(data.get("original_name"))
            year = _year(data.get("first_air_date"))
            run_times = data.get("episode_run_time")
            runtime = (
                _positive_int(run_times[0]) if isinstance(run_times, list) and run_times else None
            )
            alternatives = _nested_titles(data.get("alternative_titles"), "results", "title")
            translated = _translated_titles(data.get("translations"), "name")
        known = {title, original}
        extra = [name for name in (*alternatives, *translated) if name and name not in known]
        popularity = data.get("popularity")
        return cls(
            provider_id=int(data["id"]),
            kind=kind,
            title=title or original or "",
            original_title=original or None,
            alternative_titles=tuple(dict.fromkeys(extra)),
            year=year,
            runtime_min=runtime,
            popularity=float(popularity) if isinstance(popularity, int | float) else 0.0,
            poster_path=_str(data.get("poster_path")) or None,
            overview=_str(data.get("overview")),
        )

    def titles(self) -> tuple[str, ...]:
        """Every title the candidate is known by, the main one first."""
        names = (self.title, self.original_title or "", *self.alternative_titles)
        return tuple(name for name in dict.fromkeys(names) if name)


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    """Each signal's score from 0.0 to 1.0; None when it could not be measured."""

    title: float
    year: float | None
    runtime: float | None
    popularity: float | None
    matched_title: str

    def to_json(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "year": self.year,
            "runtime": self.runtime,
            "popularity": self.popularity,
            "matched_title": self.matched_title,
        }


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    candidate: Candidate
    score: float
    breakdown: ScoreBreakdown

    def to_json(self) -> dict[str, Any]:
        """JSON-safe, for `MatchReview.candidates` (the admin picker shows these fields)."""
        candidate = self.candidate
        return {
            "provider": "tmdb",
            "kind": candidate.kind,
            "id": candidate.provider_id,
            "title": candidate.title,
            "original_title": candidate.original_title,
            "year": candidate.year,
            "runtime_min": candidate.runtime_min,
            "popularity": candidate.popularity,
            "poster_path": candidate.poster_path,
            "overview": candidate.overview,
            "score": self.score,
            "breakdown": self.breakdown.to_json(),
        }


@dataclass(frozen=True, slots=True)
class MatchDecision:
    """The outcome of `decide`: an accepted candidate, or the shortlist for review."""

    reason: DecisionReason
    candidates: tuple[ScoredCandidate, ...]
    threshold: float
    margin: float

    @property
    def accepted(self) -> ScoredCandidate | None:
        return self.candidates[0] if self.reason == "auto_accepted" else None

    @property
    def needs_review(self) -> bool:
        return self.reason != "auto_accepted"

    @property
    def confidence(self) -> float:
        """The best score, 0.0 without candidates (`MediaFile.match_confidence`)."""
        return self.candidates[0].score if self.candidates else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "confidence": self.confidence,
            "threshold": self.threshold,
            "margin": self.margin,
            "candidates": [scored.to_json() for scored in self.candidates],
        }


def token_set_similarity(left: str, right: str) -> float:
    """rapidfuzz `token_set_ratio` / 100 of two already-normalised strings."""
    if not left or not right:
        return 0.0
    return fuzz.token_set_ratio(left, right) / 100.0


def title_similarity(
    title: str, candidate: Candidate, similarity: Similarity = token_set_similarity
) -> tuple[float, str]:
    """The best similarity over the candidate's titles, and the title that gave it."""
    query = normalize(title)
    best, best_title = 0.0, candidate.title
    for name in candidate.titles():
        value = similarity(query, normalize(name))
        if value > best:
            best, best_title = value, name
            if best >= 1.0:
                break
    return best, best_title


def year_score(parsed: int | None, provider: int | None) -> float | None:
    if parsed is None:
        return None
    if provider is None:
        return 0.0
    difference = abs(parsed - provider)
    if difference == 0:
        return 1.0
    return 0.6 if difference == 1 else 0.0


def runtime_score(probed_min: float | None, provider_min: int | None) -> float | None:
    if probed_min is None or provider_min is None or provider_min <= 0:
        return None
    difference = abs(probed_min - provider_min)
    if difference <= 5:
        return 1.0
    return 0.5 if difference <= 10 else 0.0


def score_candidates(
    query: MatchQuery,
    candidates: Iterable[Candidate],
    *,
    weights: Weights = DEFAULT_WEIGHTS,
    similarity: Similarity = token_set_similarity,
    ignore_unknown: bool = True,
) -> list[ScoredCandidate]:
    """Every distinct candidate with its score, best first.

    Ties keep a stable order: higher popularity first, then the lower provider ID.
    """
    unique: dict[tuple[str, int], Candidate] = {}
    for item in candidates:
        unique.setdefault((item.kind, item.provider_id), item)
    top_popularity = max((item.popularity for item in unique.values()), default=0.0)
    scored = [
        _score(
            query,
            item,
            top_popularity=top_popularity,
            weights=weights,
            similarity=similarity,
            ignore_unknown=ignore_unknown,
        )
        for item in unique.values()
    ]
    scored.sort(key=lambda s: (-s.score, -s.candidate.popularity, s.candidate.provider_id))
    return scored


def decide(  # noqa: PLR0913 (every knob is a setting)
    query: MatchQuery,
    candidates: Iterable[Candidate],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    margin: float = DEFAULT_MARGIN,
    limit: int = TOP_CANDIDATES,
    weights: Weights = DEFAULT_WEIGHTS,
    similarity: Similarity = token_set_similarity,
    ignore_unknown: bool = True,
) -> MatchDecision:
    """Auto-accept the best candidate or shortlist the top `limit` for review."""
    ranked = score_candidates(
        query, candidates, weights=weights, similarity=similarity, ignore_unknown=ignore_unknown
    )
    shortlist = tuple(ranked[:limit])
    reason = _reason(ranked, threshold=threshold, margin=margin)
    return MatchDecision(reason=reason, candidates=shortlist, threshold=threshold, margin=margin)


def _reason(
    ranked: Sequence[ScoredCandidate], *, threshold: float, margin: float
) -> DecisionReason:
    if not ranked:
        return "no_candidates"
    top = ranked[0].score
    runner_up = ranked[1].score if len(ranked) > 1 else 0.0
    if top + _EPSILON < threshold:
        return "below_threshold"
    if top - runner_up + _EPSILON < margin:
        return "ambiguous"
    return "auto_accepted"


def _score(  # noqa: PLR0913
    query: MatchQuery,
    candidate: Candidate,
    *,
    top_popularity: float,
    weights: Weights,
    similarity: Similarity,
    ignore_unknown: bool,
) -> ScoredCandidate:
    title, matched = title_similarity(query.title, candidate, similarity)
    year = year_score(query.year, candidate.year)
    runtime = runtime_score(query.runtime_min, candidate.runtime_min)
    popularity = candidate.popularity / top_popularity if top_popularity > 0 else None
    signals = (
        (weights.title, title),
        (weights.year, year),
        (weights.runtime, runtime),
        (weights.popularity, popularity),
    )
    if ignore_unknown:
        counted = [(weight, value) for weight, value in signals if value is not None]
        total_weight = sum(weight for weight, _ in counted)
        total = sum(weight * value for weight, value in counted) / total_weight
    else:
        total = sum(weight * (value or 0.0) for weight, value in signals)
    breakdown = ScoreBreakdown(
        title=round(title, _DIGITS),
        year=year,
        runtime=runtime,
        popularity=None if popularity is None else round(popularity, _DIGITS),
        matched_title=matched,
    )
    return ScoredCandidate(candidate=candidate, score=round(total, _DIGITS), breakdown=breakdown)


def _str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _year(value: object) -> int | None:
    text = _str(value)
    return int(text[:4]) if len(text) >= 4 and text[:4].isdigit() else None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value) if value > 0 else None


def _nested_titles(block: object, list_key: str, title_key: str) -> list[str]:
    if not isinstance(block, Mapping):
        return []
    items = block.get(list_key)
    if not isinstance(items, list):
        return []
    return [_str(item.get(title_key)) for item in items if isinstance(item, Mapping)]


def _translated_titles(block: object, title_key: str) -> list[str]:
    if not isinstance(block, Mapping):
        return []
    items = block.get("translations")
    if not isinstance(items, list):
        return []
    titles: list[str] = []
    for item in items:
        data = item.get("data") if isinstance(item, Mapping) else None
        if isinstance(data, Mapping):
            titles.append(_str(data.get(title_key)))
    return titles
