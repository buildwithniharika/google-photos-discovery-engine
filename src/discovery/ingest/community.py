"""Google Photos Help Community threads (architecture Section 5.4).

- Thread list: server-rendered HTML; `max_results` returns up to ~1,000 threads per request.
- Thread pages: content is only rendered by JavaScript, so they load in headless Chromium.
- Already-collected thread IDs are skipped, so runs are resumable and accumulate (ORC-01).
- Every page load is spaced `seconds_between_pages` (+ jitter) apart (P1.15).

The original question is the unit of analysis; replies are stored as labeled context, and
follow-ups by the original poster are kept as `op_followup` (ING-GC-06).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from discovery.config import GoogleCommunitySettings
from discovery.ingest.base import SourceError, hash_author, iso
from discovery.ingest.browser import BrowserSession, PageUnavailable
from discovery.ingest.http import HttpError, PoliteHttp
from discovery.models.schemas import Platform, RawItem, SourceName

log = logging.getLogger(__name__)

BASE_URL = "https://support.google.com"
THREAD_URL = BASE_URL + "/photos/thread/{thread_id}?hl=en"
_THREAD_ID = re.compile(r"/thread/(\d+)")
_INT = re.compile(r"\d[\d,]*")
MAX_CONSECUTIVE_PARSE_FAILURES = 5

# Class-name suffixes of the rendered thread page. Override in settings
# (`sources.google_community.selectors`) if the layout changes (ING-GC-03).
DEFAULT_SELECTORS: dict[str, str] = {
    "question_card": "QuestionQuestioncardcontent",
    "question_title": "QuestionQuestioncardtitle",
    "question_body": "QuestionQuestioncardbody",
    "same_question": "QuestionQuestionactionsupvote",
    "detail_link": "QuestionQuestiondetailsdetail-link",
    "state_chip": "QuestionStatechipschip",
    "state_alert": "QuestionQuestionstatealertroot",
    "reply_card": "MessageMessagelistmessage-card",
    "reply_body": "MessageMessagecardbody",
    "nested_reply": "MessageCommentcardnested-reply",
    "nested_body": "MessageCommentcardcomment",
    "author_name": "Post_headerUserinfoname",
    "author_role": "Post_headerUserinforole",
    "author_tag": "Post_headerUserinfotag",
    "date": "post-date-tooltip",
}
EXPERT_ROLES = ("product expert", "community specialist", "community manager", "google employee")
HIGHLIGHTS = ("recommended answer", "relevant answer")
_DATE_FORMATS = ("%m/%d/%Y, %I:%M:%S %p", "%m/%d/%Y, %H:%M:%S", "%b %d, %Y")


# --- parsing helpers -------------------------------------------------------------


def _has(tag: Tag, suffix: str) -> bool:
    return any(c.endswith(suffix) for c in tag.get("class") or [])


def _all(root: Tag, suffix: str) -> list[Tag]:
    return root.find_all(lambda t: _has(t, suffix))


def _first(root: Tag, suffix: str) -> Tag | None:
    return root.find(lambda t: _has(t, suffix))


def _own(root: Tag, suffix: str, barrier: str) -> Tag | None:
    """First match under `root` that is not inside a nested `barrier` element."""
    for el in _all(root, suffix):
        parent = el.parent
        while parent is not None and parent is not root and not _has(parent, barrier):
            parent = parent.parent
        if parent is root:
            return el
    return None


def _text(el: Tag | None, sep: str = " ") -> str:
    if el is None:
        return ""
    return el.get_text(sep, strip=True).replace("\u202f", " ").replace("\xa0", " ")


def _int(text: str) -> int | None:
    m = _INT.search(text)
    return int(m.group(0).replace(",", "")) if m else None


def parse_post_date(text: str) -> datetime | None:
    """Tooltip dates are rendered in the browser's timezone, which the session sets to UTC."""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def thread_id_from_url(url: str) -> str | None:
    m = _THREAD_ID.search(url)
    return m.group(1) if m else None


def parse_thread_list(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    threads: dict[str, dict[str, Any]] = {}
    for a in soup.select("a.thread-list-thread"):
        href = a.get("href") or ""
        tid = thread_id_from_url(href)
        if not tid or tid in threads:
            continue

        def count(modifier: str, anchor: Tag = a) -> int | None:
            el = anchor.select_one(f".thread-list-counts__count--{modifier}")
            return _int(_text(el)) if el else None

        threads[tid] = {
            "thread_id": tid,
            "url": urljoin(BASE_URL, href),
            "title": _text(a.select_one(".thread-list-thread__title")),
            "snippet": _text(a.select_one(".thread-list-thread__snippet")),
            "counts": {
                "replies": count("reply"),
                "upvotes": count("me-too"),
                "recommended_answers": count("recommended-answer"),
                "relevant_answers": count("suggested-answer"),
            },
        }
    return list(threads.values())


def _post(el: Tag, sel: dict[str, str], *, body_key: str, barrier: str, salt: str) -> dict:
    name = _text(_own(el, sel["author_name"], barrier))
    role = _text(_own(el, sel["author_role"], barrier))
    tag = _text(_own(el, sel["author_tag"], barrier))
    if "original poster" in tag.lower():
        kind = "op_followup"
    elif any(r in role.lower() for r in EXPERT_ROLES):
        kind = "expert"
    else:
        kind = "user"
    date = parse_post_date(_text(_own(el, sel["date"], barrier)))
    return {
        "kind": kind,
        "role": role or None,
        "author_hash": hash_author(name, salt),
        "date": iso(date),
        "body": _text(_own(el, sel[body_key], barrier), "\n"),
    }


def parse_thread(
    html: str,
    *,
    salt: str,
    max_replies: int = 10,
    include_replies: bool = True,
    selectors: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Question + labeled replies from a rendered thread page; None if there is no question."""
    sel = DEFAULT_SELECTORS | (selectors or {})
    soup = BeautifulSoup(html, "html.parser")
    card = _first(soup, sel["question_card"]) or soup
    title = _text(_first(card, sel["question_title"]))
    if not title:
        return None
    question = _post(card, sel, body_key="question_body", barrier=sel["reply_card"], salt=salt)

    # A highlighted answer ("relevant" / "recommended") is rendered twice: in a callout above
    # the replies and again in the list. Keep one copy and mark it.
    by_key: dict[tuple, dict[str, Any]] = {}
    if include_replies:
        for rc in _all(soup, sel["reply_card"]):
            card_text = _text(rc)[:200].lower()
            highlighted = next((h for h in HIGHLIGHTS if h in card_text), None)
            posts = [_post(rc, sel, body_key="reply_body", barrier=sel["nested_reply"], salt=salt)]
            posts += [
                _post(n, sel, body_key="nested_body", barrier="\0", salt=salt)
                for n in _all(rc, sel["nested_reply"])
            ]
            for i, post in enumerate(posts):
                key = (post["author_hash"], post["date"], post["body"])
                kept_post = by_key.setdefault(key, post | {"highlighted": None})
                if i == 0 and highlighted:
                    kept_post["highlighted"] = highlighted
    replies = list(by_key.values())
    op = [r for r in replies if r["kind"] == "op_followup"][:max_replies]
    others = [r for r in replies if r["kind"] != "op_followup"][:max_replies]
    kept = sorted(op + others, key=lambda r: r["date"] or "")

    same = _first(card, sel["same_question"])
    alert = _text(_first(card, sel["state_alert"]))
    return {
        "title": title,
        "body": question["body"],
        "created_at": question["date"],
        "author_hash": question["author_hash"],
        "same_question_count": _int(_text(same)) if same else None,
        "details": [t for t in (_text(d) for d in _all(card, sel["detail_link"])) if t != ","],
        "state": [_text(c) for c in _all(card, sel["state_chip"])],
        "marked_duplicate": "duplicate" in alert.lower(),
        "replies_seen": len(replies),
        "replies_truncated": len(replies) > len(kept),
        "replies": kept,
    }


# --- connector -------------------------------------------------------------------


class CommunityConnector:
    source_name = SourceName.GOOGLE_COMMUNITY
    platform = Platform.GOOGLE_COMMUNITY

    def __init__(
        self,
        settings: GoogleCommunitySettings,
        *,
        run_id: str,
        salt: str,
        http: PoliteHttp,
        known_ids: set[str] | None = None,
        debug_dir: Path | None = None,
        browser_factory: Callable[[], AbstractContextManager[Any]] | None = None,
    ):
        self.settings = settings
        self.run_id = run_id
        self.salt = salt
        self.http = http
        self.known_ids = known_ids or set()
        self.debug_dir = debug_dir
        self._browser_factory = browser_factory or (lambda: BrowserSession(http))
        self.selectors = DEFAULT_SELECTORS | settings.selectors
        self.warnings: list[dict[str, Any]] = []
        self.stats: dict[str, int] = {
            "listed": 0,
            "already_collected": 0,
            "unavailable": 0,
            "parse_failed": 0,
            "older_than_since": 0,
        }

    @property
    def title_selector(self) -> str:
        return f"[class*='{self.selectors['question_title']}']"

    def _list_url(self, n: int) -> str:
        return f"{self.settings.list_url}&max_results={n}"

    def crawl_list(self, wanted: int) -> list[dict[str, Any]]:
        """Threads not collected yet, newest activity first, up to `wanted`."""
        n = min(
            self.settings.max_threads, len(self.known_ids) + wanted + self.settings.list_page_size
        )
        while True:
            try:
                refs = parse_thread_list(self.http.get(self._list_url(n)).text)
            except HttpError as exc:
                raise SourceError(f"thread list: {exc}") from exc
            fresh = [r for r in refs if f"google_community:{r['thread_id']}" not in self.known_ids]
            end_of_list = len(refs) < n
            if len(fresh) >= wanted or end_of_list or n >= self.settings.max_threads:
                self.stats["listed"] = len(refs)
                self.stats["already_collected"] = len(refs) - len(fresh)
                return fresh[:wanted]
            n = min(self.settings.max_threads, n + wanted)

    def fetch(self, since: datetime | None, limit: int | None) -> Iterator[RawItem]:
        wanted = min(limit or self.settings.max_threads_per_run, self.settings.max_threads_per_run)
        refs = self.crawl_list(wanted)
        log.info("Community: %d new threads to fetch (%d listed)", len(refs), self.stats["listed"])
        if not refs:
            return
        failures = 0
        with self._browser_factory() as browser:
            for ref in refs:
                url = THREAD_URL.format(thread_id=ref["thread_id"])
                try:
                    html = browser.load(url, wait_for=self.title_selector)
                except PageUnavailable as exc:
                    self.stats["unavailable"] += 1
                    log.info("Thread %s unavailable: %s", ref["thread_id"], exc)
                    continue
                thread = parse_thread(
                    html,
                    salt=self.salt,
                    max_replies=self.settings.max_replies,
                    include_replies=self.settings.include_replies,
                    selectors=self.selectors,
                )
                if thread is None:
                    failures += 1
                    self.stats["parse_failed"] += 1
                    self._save_debug(ref["thread_id"], html)
                    if failures >= MAX_CONSECUTIVE_PARSE_FAILURES:
                        raise SourceError(
                            f"{failures} thread pages in a row could not be parsed; the page "
                            "layout probably changed (update sources.google_community.selectors)"
                        )
                    continue
                failures = 0
                created = thread["created_at"]
                if since is not None and created and datetime.fromisoformat(created) < since:
                    self.stats["older_than_since"] += 1
                    continue
                yield self._raw_item(ref, url, thread)

    def _save_debug(self, thread_id: str, html: str) -> None:
        self.warnings.append({"message": "thread page could not be parsed", "thread": thread_id})
        if self.debug_dir is not None:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            (self.debug_dir / f"{thread_id}.html").write_text(html, encoding="utf-8")

    def _raw_item(self, ref: dict[str, Any], url: str, thread: dict[str, Any]) -> RawItem:
        payload = {
            "thread_id": ref["thread_id"],
            "url": url,
            **thread,
            "_meta": {
                "list_url": self.settings.list_url,
                "list_title": ref["title"],
                "list_snippet": ref["snippet"],
                "list_counts": ref["counts"],
            },
        }
        return RawItem(
            raw_id=f"google_community:{ref['thread_id']}",
            source_name=self.source_name,
            platform=self.platform,
            source_url=url,
            run_id=self.run_id,
            payload=payload,
        )
