#!/usr/bin/env python3
"""IPTVnator end-to-end check of the Xtream host (SPEC §7.5 and §15, compat/iptvnator.md).

Drives the IPTVnator PWA (MIT; it runs as its own container and is never part of
this project) with Playwright the way a customer would: add the Xtream account,
list the categories, open a movie and a series, and start playback of both.

    XC_USER=... XC_PASS=... uvx --with playwright==1.63.0 python compat/iptvnator_e2e.py \\
        --app http://127.0.0.1:4333 --server http://tv.localhost:8080

--server is the URL typed into IPTVnator. Its container calls player_api.php there
and the browser plays from it, so both must reach the server under that name. The
movie and the series are the first ones the account lists over player_api.php
(override with --movie and --series). Playback needs H.264/AAC, which Playwright's
bundled Chromium cannot decode, so the browser is the installed Google Chrome
(--channel chrome). Exit status: 0 when every step passed, 1 when one failed, 2 when
the check could not run.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
# compat/ is a directory of scripts, not a package: share validate.py's plumbing.
from validate import (
    HttpClient,
    LiveError,
    Problem,
    Redactor,
    Reporter,
    SuiteError,
    parse_json,
)

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page, Response

PLAYWRIGHT_VERSION = "1.63.0"
_PLAY_PATH = re.compile(r"/(movie|series)/[^/]+/[^/]+/[0-9]+\.[a-z0-9]+")
_MEDIA_ERRORS = {1: "aborted", 2: "network error", 3: "decode error", 4: "source not supported"}
_VIDEO_STATE = """(video) => ({
    readyState: video.readyState,
    currentTime: video.currentTime,
    paused: video.paused,
    error: video.error ? video.error.code : 0,
    width: video.videoWidth,
    height: video.videoHeight,
    source: video.currentSrc,
})"""
# The PWA's selectors, as IPTVnator's own Playwright suite uses them (v0.24.0).
_CATEGORY = ".context-panel .category-item"
_CARD = "app-grid-list mat-card"
_DETAIL = "app-portal-detail-shell"
_PLAYER_VIDEO = "app-portal-inline-player app-web-player-view video"


class StepFailed(Exception):
    """One step of the journey did not reach the state it waits for."""


def _wait(locator: Locator, timeout_ms: float, failure: Callable[[], str]) -> None:
    """Wait for the locator to be visible; a timeout becomes a StepFailed(failure())."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # noqa: PLC0415

    try:
        locator.wait_for(timeout=timeout_ms)
    except PlaywrightTimeoutError as exc:
        raise StepFailed(failure()) from exc


@dataclass(frozen=True)
class Pick:
    """A title to open, and the category it is listed under."""

    kind: str  # movie | series
    name: str
    category: str
    categories: tuple[str, ...]  # every category of this kind, in the API's order


@dataclass
class PlayLog:
    """Responses to play URLs (/movie/... and /series/...) on the Xtream host."""

    origin: str
    seen: list[tuple[str, int, str]] = field(default_factory=list)

    def record(self, response: Response) -> None:
        parts = urlsplit(response.url)
        match = _PLAY_PATH.fullmatch(parts.path)
        if match and f"{parts.scheme}://{parts.netloc}" == self.origin:
            location = response.headers.get("location", "")
            self.seen.append((match.group(1), response.status, location))


# --------------------------------------------------------------------------- API


def _api(client: HttpClient, credentials: tuple[str, str], action: str) -> Any:
    query = {"username": credentials[0], "password": credentials[1], "action": action}
    try:
        response = client.request("GET", "/player_api.php", query=query)
    except LiveError as exc:
        raise SuiteError(f"the Xtream host is unreachable: {exc}") from exc
    payload, problems = parse_json(response.body)
    if response.status != 200 or problems:
        detail = problems[0] if problems else f"HTTP {response.status}"
        raise SuiteError(f"player_api.php action={action}: {detail}")
    user_info = payload.get("user_info") if isinstance(payload, dict) else None
    if isinstance(user_info, dict) and user_info.get("auth") == 0:
        raise SuiteError("authentication failed (auth 0): check XC_USER and XC_PASS")
    if not isinstance(payload, list):
        raise SuiteError(f"player_api.php action={action} did not return a list")
    return payload


def pick(client: HttpClient, credentials: tuple[str, str], kind: str, wanted: str | None) -> Pick:
    """The title to open: the first listed one (or the one named), with its category."""
    category_action, list_action = (
        ("get_vod_categories", "get_vod_streams")
        if kind == "movie"
        else ("get_series_categories", "get_series")
    )
    categories = _api(client, credentials, category_action)
    names = {category["category_id"]: category["category_name"] for category in categories}
    for item in _api(client, credentials, list_action):
        if wanted is not None and item["name"] != wanted:
            continue
        if item["category_id"] in names:
            return Pick(kind, item["name"], names[item["category_id"]], tuple(names.values()))
    named = f" named {wanted!r}" if wanted else ""
    raise SuiteError(f"the account lists no {kind}{named} in a category it can see")


# --------------------------------------------------------------------------- browser


class Journey:
    """The customer's path through IPTVnator, one reported step at a time."""

    def __init__(
        self,
        page: Page,
        reporter: Reporter,
        plays: PlayLog,
        *,
        timeout: float,
        artifacts: Path | None,
    ) -> None:
        self.page, self.reporter, self.plays = page, reporter, plays
        self.timeout_ms = timeout * 1000
        self.artifacts = artifacts
        self.vod_url = ""

    def step(self, label: str, action: Callable[[], str]) -> bool:
        from playwright.sync_api import Error as PlaywrightError  # noqa: PLC0415

        try:
            detail = action()
        except (StepFailed, PlaywrightError) as exc:
            message = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            self.reporter.fail(label, [Problem("IPTVnator", "e2e", message)])
            if self.artifacts is not None:
                name = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:60]
                try:
                    self.page.screenshot(path=self.artifacts / f"{name}.png", full_page=True)
                except PlaywrightError:
                    self.reporter.write(f"        (no screenshot of {label!r}: the page is gone)")
            return False
        self.reporter.ok(label, detail)
        return True

    # -- steps

    def add_account(self, server: str, credentials: tuple[str, str]) -> str:
        page = self.page
        page.get_by_role("button", name="Add playlist").click(timeout=self.timeout_ms)
        dialog = page.locator("mat-dialog-container")
        dialog.wait_for(timeout=self.timeout_ms)
        dialog.get_by_role("radio", name=re.compile("Xtream credentials", re.I)).click()
        dialog.locator("#title").fill("Smart IPTV contract check")
        dialog.locator("#serverUrl").fill(server)
        dialog.locator("#username").fill(credentials[0])
        dialog.locator("#password").fill(credentials[1])
        dialog.get_by_role("button", name="Add", exact=True).click()
        dialog.wait_for(state="detached", timeout=self.timeout_ms)
        page.wait_for_url(re.compile(r"/xtreams/.+/vod"), timeout=self.timeout_ms)
        self.vod_url = re.sub(r"/vod.*$", "/vod", page.url)
        return "the account opens on its movies"

    def list_categories(self, target: Pick) -> str:
        if target.kind == "series":
            self.page.goto(self.vod_url.removesuffix("/vod") + "/series")
        items = self.page.locator(_CATEGORY)
        _wait(
            items.filter(has_text=target.category).first,
            self.timeout_ms,
            lambda: f"category {target.category!r} never appeared",
        )
        shown = [text.strip() for text in items.all_inner_texts()]
        missing = [name for name in target.categories if not any(name in s for s in shown)]
        if missing:
            raise StepFailed(f"categories listed by the API but not shown: {missing}")
        return f"{len(target.categories)} {target.kind} categories shown"

    def open_title(self, target: Pick) -> str:
        page = self.page
        page.locator(_CATEGORY).filter(has_text=target.category).first.click()
        card = page.locator(_CARD).filter(has_text=target.name).first
        _wait(
            card,
            self.timeout_ms,
            lambda: (
                f"{target.name!r} is not shown under {target.category!r}; cards: "
                f"{[text.strip()[:40] for text in page.locator(_CARD).all_inner_texts()[:8]]}"
            ),
        )
        card.click()
        if target.kind == "movie":
            heading = page.locator(_DETAIL).get_by_role("heading", level=1)
            _wait(
                heading.filter(has_text=target.name),
                self.timeout_ms,
                lambda: f"the details page never showed the title {target.name!r}",
            )
            return "details page open"
        episodes = page.locator(".episode-card")
        _wait(episodes.first, self.timeout_ms, lambda: "the series page shows no episodes")
        count = episodes.count()
        return f"{count} episode{'' if count == 1 else 's'} in the first season tab"

    def play(self, kind: str, start: Callable[[], None]) -> str:
        before = len(self.plays.seen)
        start()
        state = self._wait_playing(self.page.locator(_PLAYER_VIDEO).first)
        responses = [entry for entry in self.plays.seen[before:] if entry[0] == kind]
        if not responses:
            raise StepFailed(f"the video played, but not from a /{kind}/ URL on the Xtream host")
        _, status, location = responses[0]
        if status != 302:
            raise StepFailed(f"/{kind}/ answered {status}; play URLs redirect (302) to the edge")
        edge = urlsplit(location)
        return (
            f"/{kind}/ 302 to {edge.scheme}://{edge.netloc}, "
            f"{state['width']}x{state['height']} at {state['currentTime']:.1f}s"
        )

    def _wait_playing(self, video: Locator) -> dict[str, Any]:
        video.wait_for(state="attached", timeout=self.timeout_ms)
        deadline = time.monotonic() + self.timeout_ms / 1000
        while True:
            state: dict[str, Any] = video.evaluate(_VIDEO_STATE)
            if state["error"]:
                reason = _MEDIA_ERRORS.get(state["error"], "unknown")
                raise StepFailed(f"the player reports MediaError {state['error']} ({reason})")
            if (
                state["readyState"] >= 2
                and state["currentTime"] >= 1.0
                and not state["paused"]
                and state["width"] > 0
            ):
                return state
            if time.monotonic() > deadline:
                source = urlsplit(state["source"]).netloc or "no source"
                raise StepFailed(
                    f"playback did not start: readyState {state['readyState']}, "
                    f"currentTime {state['currentTime']:.1f}, paused {state['paused']}, "
                    f"size {state['width']}x{state['height']}, source host {source}"
                )
            self.page.wait_for_timeout(250)


def run_journey(
    args: argparse.Namespace,
    reporter: Reporter,
    *,
    client: HttpClient,
    credentials: tuple[str, str],
    targets: tuple[Pick, Pick],
) -> None:
    try:
        from playwright.sync_api import Error as PlaywrightError  # noqa: PLC0415
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError as exc:
        raise SuiteError(
            f"Playwright for Python is required: uvx --with playwright=={PLAYWRIGHT_VERSION} "
            "python compat/iptvnator_e2e.py ..."
        ) from exc

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                channel=args.channel or None,
                headless=not args.headed,
                args=["--autoplay-policy=no-user-gesture-required"],
            )
        except PlaywrightError as exc:
            first_line = str(exc).strip().splitlines()[0]
            raise SuiteError(
                f"cannot start the browser (--channel {args.channel!r}): {first_line}"
            ) from exc
        # The PWA's service worker is not under test; blocking it keeps every request
        # visible to Playwright and the run free of cached state.
        context = browser.new_context(
            service_workers="block", viewport={"width": 1440, "height": 900}, locale="en-US"
        )
        if args.artifacts is not None:
            context.tracing.start(screenshots=True, snapshots=True)
        page = context.new_page()
        plays = PlayLog(client.origin)
        page.on("response", plays.record)
        journey = Journey(page, reporter, plays, timeout=args.timeout, artifacts=args.artifacts)
        try:
            try:
                page.goto(args.app, wait_until="domcontentloaded", timeout=journey.timeout_ms)
            except PlaywrightError as exc:
                first_line = str(exc).strip().splitlines()[0]
                raise SuiteError(f"IPTVnator does not load at {args.app}: {first_line}") from exc
            _walk(journey, args.server, credentials, *targets)
        finally:
            if args.artifacts is not None:
                context.tracing.stop(path=args.artifacts / "trace.zip")
            context.close()
            browser.close()


def _walk(
    journey: Journey, server: str, credentials: tuple[str, str], movie: Pick, series: Pick
) -> None:
    page = journey.page
    if not journey.step(
        "log in: add the Xtream account", lambda: journey.add_account(server, credentials)
    ):
        journey.reporter.write("  Remaining steps skipped: the account must be added first.")
        return
    if journey.step("list movie categories", lambda: journey.list_categories(movie)) and (
        journey.step(f"open the movie {movie.name!r}", lambda: journey.open_title(movie))
    ):
        journey.step(
            "play the movie",
            lambda: journey.play("movie", page.locator(f"{_DETAIL} button.play-btn").first.click),
        )
    if journey.step("list series categories", lambda: journey.list_categories(series)) and (
        journey.step(f"open the series {series.name!r}", lambda: journey.open_title(series))
    ):
        journey.step(
            "play the first episode",
            lambda: journey.play("series", page.locator(".episode-card").first.click),
        )


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive IPTVnator against the Xtream host: log in, list categories, "
        "open a movie and a series, and play both.",
        epilog="Credentials come from the XC_USER and XC_PASS environment variables.",
    )
    parser.add_argument(
        "--app", default="http://127.0.0.1:4333", help="the IPTVnator PWA (http://127.0.0.1:4333)"
    )
    parser.add_argument(
        "--server",
        default="http://tv.localhost:8080",
        help="the Xtream host as typed into IPTVnator (http://tv.localhost:8080)",
    )
    parser.add_argument("--movie", help="the name of the movie to open (default: the first)")
    parser.add_argument("--series", help="the name of the series to open (default: the first)")
    parser.add_argument(
        "--channel",
        default="chrome",
        help='browser channel: "chrome" (default, plays H.264/AAC) or "" for Playwright\'s '
        "bundled Chromium, which cannot decode them",
    )
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds per step (30)")
    parser.add_argument(
        "--artifacts",
        type=Path,
        help="write a screenshot of every failed step and a Playwright trace here; the "
        "trace holds the test account's credentials",
    )
    args = parser.parse_args(argv)

    redact = Redactor()
    reporter = Reporter(redact, verbose=False)
    try:
        credentials = os.environ.get("XC_USER", ""), os.environ.get("XC_PASS", "")
        if not all(credentials):
            raise SuiteError("set the XC_USER and XC_PASS environment variables")
        for secret in credentials:
            redact.add(secret)
        if args.artifacts is not None:
            args.artifacts.mkdir(parents=True, exist_ok=True)
        client = HttpClient(args.server, args.timeout)
        movie = pick(client, credentials, "movie", args.movie)
        series = pick(client, credentials, "series", args.series)
        reporter.write(f"IPTVnator end-to-end check: {args.app} against {client.origin}")
        reporter.section(f"Journey (movie {movie.name!r}, series {series.name!r})")
        run_journey(args, reporter, client=client, credentials=credentials, targets=(movie, series))
    except SuiteError as exc:
        sys.stderr.write(redact(f"iptvnator_e2e.py: {exc}") + "\n")
        return 2
    reporter.write()
    verdict = "FAILED" if reporter.failed else "OK"
    reporter.write(f"{verdict}: {reporter.passed} passed, {reporter.failed} failed")
    return 1 if reporter.failed else 0


if __name__ == "__main__":
    sys.exit(main())
