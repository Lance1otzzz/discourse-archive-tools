#!/usr/bin/env python3
"""
Archive a logged-in Discourse site using a Discourse User API key or
exported browser cookies.

This is intentionally a read-only GET crawler. It does not bypass login.
Give it a URL you can already access, plus a read-scope User API key or a
Netscape-format cookies.txt file exported from that browser session.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import posixpath
import re
import sys
import time
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urljoin, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener


def configure_utf8_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


configure_utf8_stdio()


ASSET_EXTENSIONS = {
    ".apng",
    ".avif",
    ".bmp",
    ".css",
    ".csv",
    ".doc",
    ".docx",
    ".eot",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".m4a",
    ".mov",
    ".mp3",
    ".mp4",
    ".ods",
    ".odt",
    ".otf",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".svg",
    ".tar",
    ".tgz",
    ".ttf",
    ".txt",
    ".wav",
    ".webm",
    ".woff",
    ".woff2",
    ".xls",
    ".xlsx",
    ".zip",
}

SKIP_SCHEMES = {"about", "blob", "data", "file", "irc", "javascript", "mailto", "magnet", "tel"}
PATH_SAFE_CHARS = "/:@!$&'()*+,;=-._~%"
QUERY_SAFE_CHARS = "/?:@!$&'()*+,;=-._~%[]"
URL_ATTRS = {
    "data-download-href",
    "data-large-src",
    "data-original-href",
    "data-small-upload",
    "data-src",
    "data-thumbnail",
    "href",
    "poster",
    "src",
}
MARKDOWN_URL_RE = re.compile(r"(?P<url>https?://[^\s<>()\"']+|/[A-Za-z0-9_./~:%?#[\]@!$&'()*+,;=-]+)")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
STYLE_URL_RE = re.compile(r"url\((?P<quote>['\"]?)(?P<url>.*?)(?P=quote)\)")
TOPIC_PATH_RE = re.compile(r"^/t(?:/[^/?#]+)*/(?P<topic_id>\d+)(?:/(?P<post_number>\d+))?(?:\.json)?/?$")
RAW_PATH_RE = re.compile(r"^/raw/(?P<post_id>\d+)(?:/[^/?#]+)?/?$")
POST_PATH_RE = re.compile(r"^/posts/(?P<post_id>\d+)(?:\.json)?/?$")


def quote_path(value: str) -> str:
    return quote(value, safe="")


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value for name, value in attrs if value}
        for attr, value in attr_map.items():
            if attr in URL_ATTRS or self.looks_like_discourse_upload_attr(attr, value):
                self.links.append(html.unescape(value))
        style = attr_map.get("style")
        if style:
            for match in STYLE_URL_RE.finditer(style):
                self.links.append(html.unescape(match.group("url")))
        srcset = attr_map.get("srcset") or attr_map.get("data-srcset")
        if srcset:
            for candidate in srcset.split(","):
                url = candidate.strip().split(" ", 1)[0]
                if url:
                    self.links.append(html.unescape(url))

    def looks_like_discourse_upload_attr(self, attr: str, value: str) -> bool:
        if not attr.startswith("data-"):
            return False
        lower = value.lower()
        starts_like_url = lower.startswith(("http://", "https://", "/", "//"))
        return starts_like_url and any(marker in lower for marker in ("upload", "uploads", "secure-media"))


@dataclass(frozen=True)
class QueueItem:
    url: str
    depth: int
    source: str


class ArchiveError(RuntimeError):
    pass


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class DiscourseArchiver:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.out = Path(args.out).expanduser().resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "topics").mkdir(exist_ok=True)
        (self.out / "pages").mkdir(exist_ok=True)
        (self.out / "assets").mkdir(exist_ok=True)
        (self.out / "raw").mkdir(exist_ok=True)

        parsed_root = urlparse(args.root or args.start[0])
        if parsed_root.scheme not in {"http", "https"} or not parsed_root.netloc:
            raise ArchiveError("Start URL must be absolute, for example https://forum.example.com/t/topic/123")
        self.root_scheme = parsed_root.scheme
        self.root_host = parsed_root.netloc.lower()
        self.allowed_hosts = {self.root_host, *(host.lower() for host in args.allow_host)}
        self.same_subdomains = args.allow_subdomains

        self.api_key = args.user_api_key or (os.environ.get(args.user_api_key_env, "") if args.user_api_key_env else "")
        self.client_id = args.user_api_client_id or (
            os.environ.get(args.user_api_client_id_env, "") if args.user_api_client_id_env else ""
        )
        self.cookie_header = os.environ.get(args.cookie_env, "") if args.cookie_env else ""
        self.opener = self._build_opener(args.cookies)
        self.queue: deque[QueueItem] = deque()
        self.queued: set[str] = set()
        self.seen_urls: set[str] = set()
        self.processed_topics: set[str] = set()
        self.processed_topic_posts: set[tuple[str, str]] = set()
        self.download_count = 0
        self.log_path = self.out / "archive_index.jsonl"
        self._load_previous_log()

    def _build_opener(self, cookie_path: str | None):
        handlers = [NoRedirectHandler()]
        if cookie_path:
            jar = MozillaCookieJar()
            jar.load(str(Path(cookie_path).expanduser()), ignore_discard=True, ignore_expires=True)
            handlers.append(HTTPCookieProcessor(jar))
        return build_opener(*handlers)

    def _load_previous_log(self) -> None:
        if not self.log_path.exists() or not self.args.resume:
            return
        with self.log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("status") == "ok" and event.get("url"):
                    self.seen_urls.add(event["url"])
                if event.get("status") == "ok" and event.get("kind") == "topic" and event.get("topic_id"):
                    if event.get("first_post_only"):
                        self.processed_topic_posts.add((str(event["topic_id"]), str(event.get("post_number") or "1")))
                    else:
                        self.processed_topics.add(str(event["topic_id"]))
                if event.get("status") == "ok" and event.get("kind") == "topic-post" and event.get("topic_id"):
                    self.processed_topic_posts.add((str(event["topic_id"]), str(event.get("post_number") or "1")))

    def log(self, **event: object) -> None:
        event.setdefault("time", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def normalize_url(self, url: str, base: str | None = None) -> str | None:
        url = html.unescape(url.strip())
        if not url:
            return None
        if base:
            url = urljoin(base, url)
        parsed = urlparse(url)
        if parsed.scheme.lower() in SKIP_SCHEMES:
            return None
        if parsed.scheme not in {"http", "https"}:
            return None
        parsed = parsed._replace(
            netloc=self.ascii_netloc(parsed),
            path=quote(parsed.path, safe=PATH_SAFE_CHARS),
            query=quote(parsed.query, safe=QUERY_SAFE_CHARS),
            fragment="",
        )
        return urlunparse(parsed)

    def ascii_netloc(self, parsed) -> str:  # type: ignore[no-untyped-def]
        try:
            parsed.netloc.encode("ascii")
            return parsed.netloc
        except UnicodeEncodeError:
            pass
        if not parsed.hostname:
            return parsed.netloc
        host = parsed.hostname.encode("idna").decode("ascii")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        userinfo = ""
        if parsed.username:
            userinfo = quote(parsed.username, safe="")
            if parsed.password:
                userinfo += ":" + quote(parsed.password, safe="")
            userinfo += "@"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{userinfo}{host}{port}"

    def expand_url_candidates(self, url: str) -> Iterable[str]:
        yield url
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for key in ("url", "u", "target", "redirect", "redirect_to", "return_path"):
            for value in query.get(key, []):
                unwrapped = self.normalize_url(value, url)
                if unwrapped:
                    yield unwrapped

    def host_allowed(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        if host in self.allowed_hosts:
            return True
        if self.same_subdomains:
            root = self.root_host.split(":", 1)[0]
            bare = host.split(":", 1)[0]
            return bare == root or bare.endswith("." + root)
        return False

    def enqueue(self, url: str | None, depth: int, source: str) -> None:
        if not url:
            return
        norm = self.normalize_url(url, source if source.startswith("http") else None)
        if not norm:
            return
        for candidate in dict.fromkeys(self.expand_url_candidates(norm)):
            if not self.host_allowed(candidate):
                continue
            if self.args.topic_links_only and not self.should_follow_in_topic_only_mode(candidate):
                continue
            if depth > self.args.max_depth and not self.is_asset_url(candidate):
                continue
            if candidate in self.seen_urls or candidate in self.queued:
                continue
            self.queued.add(candidate)
            self.queue.append(QueueItem(candidate, depth, source))

    def should_follow_in_topic_only_mode(self, url: str) -> bool:
        return bool(
            self.topic_id_from_url(url)
            or self.raw_post_id_from_url(url)
            or self.post_id_from_url(url)
            or self.is_asset_url(url)
        )

    def fetch(self, url: str, accept: str = "*/*", allow_external_redirect: bool = False) -> tuple[bytes, dict[str, str]]:
        if self.args.delay > 0:
            time.sleep(self.args.delay)
        current_url = url
        attempts = 0
        redirects = 0
        while True:
            attempts += 1
            request = Request(current_url, headers=self.request_headers(current_url, accept), method="GET")
            try:
                with self.opener.open(request, timeout=self.args.timeout) as response:
                    return response.read(), dict(response.headers.items())
            except HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    location = exc.headers.get("Location")
                    redirected_url = self.normalize_url(location or "", current_url)
                    if not redirected_url:
                        raise ArchiveError(f"Redirect without usable Location for {current_url}") from exc
                    if not self.host_allowed(redirected_url) and not allow_external_redirect:
                        raise ArchiveError(f"Blocked cross-host redirect from {current_url} to {redirected_url}") from exc
                    redirects += 1
                    if redirects > self.args.max_redirects:
                        raise ArchiveError(f"Too many redirects for {url}") from exc
                    current_url = redirected_url
                    attempts = 0
                    continue
                if exc.code in {429, 500, 502, 503, 504} and attempts <= self.args.retries:
                    wait = self._retry_delay(exc, attempts)
                    print(f"retry {exc.code} in {wait:.1f}s: {current_url}", file=sys.stderr)
                    time.sleep(wait)
                    continue
                body = exc.read(400).decode("utf-8", errors="replace")
                raise ArchiveError(f"HTTP {exc.code} for {current_url}: {body[:200]}") from exc
            except URLError as exc:
                if attempts <= self.args.retries:
                    wait = min(60.0, 2.0 ** attempts)
                    print(f"retry network error in {wait:.1f}s: {current_url}", file=sys.stderr)
                    time.sleep(wait)
                    continue
                raise ArchiveError(f"Network error for {current_url}: {exc}") from exc

    def request_headers(self, url: str, accept: str) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": self.args.user_agent,
        }
        if self.host_allowed(url):
            if self.api_key:
                headers["User-Api-Key"] = self.api_key
            if self.client_id:
                headers["User-Api-Client-Id"] = self.client_id
            if self.cookie_header:
                headers["Cookie"] = self.cookie_header
        return headers

    def _retry_delay(self, exc: HTTPError, attempts: int) -> float:
        retry_after = exc.headers.get("Retry-After")
        if retry_after:
            try:
                return min(300.0, float(retry_after))
            except ValueError:
                pass
        return min(120.0, 2.0 ** attempts)

    def run(self) -> None:
        for start_url in self.args.start:
            self.enqueue(start_url, 0, "seed")
        if self.args.input_file:
            with Path(self.args.input_file).expanduser().open("r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        self.enqueue(stripped, 0, "seed-file")
        if self.args.discover_latest:
            self.discover_latest_topics()

        while self.queue:
            if self.args.max_pages and self.download_count >= self.args.max_pages:
                print(f"Reached --max-pages={self.args.max_pages}", file=sys.stderr)
                break
            item = self.queue.popleft()
            self.queued.discard(item.url)
            if item.url in self.seen_urls:
                if self.topic_ref_from_url(item.url):
                    try:
                        self.process(item)
                    except ArchiveError as exc:
                        self.log(kind="error", status="error", url=item.url, depth=item.depth, message=str(exc))
                        print(f"ERROR: {exc}", file=sys.stderr)
                continue
            try:
                self.process(item)
                self.seen_urls.add(item.url)
            except ArchiveError as exc:
                self.log(kind="error", status="error", url=item.url, depth=item.depth, message=str(exc))
                print(f"ERROR: {exc}", file=sys.stderr)

        self.write_summary()

    def discover_latest_topics(self) -> None:
        page = self.args.latest_start_page
        fetched_pages = 0
        while True:
            if self.args.latest_pages and fetched_pages >= self.args.latest_pages:
                break
            if self.args.max_pages and self.download_count >= self.args.max_pages:
                break

            url = self.url_for_path(f"/latest.json?page={page}")
            try:
                body, _headers = self.fetch(url, "application/json,text/javascript,*/*")
                listing = json.loads(body.decode("utf-8"))
            except (ArchiveError, json.JSONDecodeError) as exc:
                self.log(kind="latest", status="error", url=url, page=page, message=str(exc))
                print(f"ERROR: latest page {page}: {exc}", file=sys.stderr)
                break

            topics = (listing.get("topic_list") or {}).get("topics") or []
            self.download_count += 1
            self.log(kind="latest", status="ok", url=url, page=page, topic_count=len(topics))
            if not topics:
                break

            for topic in topics:
                topic_id = topic.get("id")
                if not topic_id:
                    continue
                slug = str(topic.get("slug") or "-")
                self.enqueue(self.url_for_path(f"/t/{quote_path(slug)}/{topic_id}"), 0, url)

            page += 1
            fetched_pages += 1

    def process(self, item: QueueItem) -> None:
        topic_ref = self.topic_ref_from_url(item.url)
        topic_id = topic_ref[0] if topic_ref else None
        post_number = topic_ref[1] if topic_ref else None
        raw_post_id = self.raw_post_id_from_url(item.url)
        post_id = self.post_id_from_url(item.url)

        if topic_id:
            self.process_topic(item.url, topic_id, item.depth, post_number)
            return
        if raw_post_id:
            raw = self.download_raw_post(raw_post_id, item.url)
            self.extract_from_text(raw, item.url, item.depth)
            self.log(kind="raw", status="ok", url=item.url, post_id=raw_post_id, depth=item.depth)
            return
        if post_id:
            self.process_post_json(post_id, item.url, item.depth, None)
            return
        if self.is_asset_url(item.url):
            self.download_asset(item.url, item.depth)
            return
        self.download_page(item.url, item.depth)

    def process_topic(self, url: str, topic_id: str, depth: int, post_number: str | None = None) -> None:
        if self.args.first_post_only:
            target_post_number = str(post_number or "1")
            target_key = (topic_id, target_post_number)
            if target_key in self.processed_topic_posts:
                if self.extract_saved_topic(topic_id, depth, url, target_post_number):
                    self.log(
                        kind="topic-post-ref",
                        status="ok",
                        url=url,
                        topic_id=topic_id,
                        post_number=target_post_number,
                        depth=depth,
                    )
                    return
                self.processed_topic_posts.discard(target_key)
        else:
            target_post_number = None
        if not self.args.first_post_only and topic_id in self.processed_topics:
            if self.extract_saved_topic(topic_id, depth, url):
                self.log(kind="topic-ref", status="ok", url=url, topic_id=topic_id, depth=depth)
                return
            self.processed_topics.discard(topic_id)
        topic_json_url, body = self.fetch_topic_json(url, topic_id)
        try:
            topic = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ArchiveError(f"Topic JSON did not parse: {topic_json_url}") from exc

        topic_id = str(topic.get("id") or topic_id)
        topic_dir = self.out / "topics" / topic_id
        posts_dir = topic_dir / "posts"
        posts_dir.mkdir(parents=True, exist_ok=True)
        self.write_json(topic_dir / "topic.json", topic)

        post_stream = topic.get("post_stream") or {}
        posts = post_stream.get("posts") or []
        stream_ids = [str(pid) for pid in (post_stream.get("stream") or [])]
        known_post_ids = set()
        saved_post_count = 0

        if self.args.first_post_only:
            target_post = self.find_post_by_number(posts, target_post_number or "1")
            if not target_post and target_post_number and target_post_number != "1":
                target_post = self.fetch_post_by_number(topic_id, target_post_number, url)
            if target_post:
                self.save_post(topic_id, target_post, depth, url)
                saved_post_count = 1
            elif stream_ids and (target_post_number or "1") == "1":
                self.process_post_json(stream_ids[0], self.url_for_path(f"/posts/{stream_ids[0]}.json"), depth, topic_id)
                saved_post_count = 1
            else:
                raise ArchiveError(f"Topic has no matching post #{target_post_number or '1'}: {url}")
            self.processed_topic_posts.add((topic_id, target_post_number or "1"))
            self.download_count += 1
            self.log(
                kind="topic-post",
                status="ok",
                url=url,
                json_url=topic_json_url,
                topic_id=topic_id,
                post_number=target_post_number or "1",
                title=topic.get("title"),
                post_count=saved_post_count,
                first_post_only=True,
                depth=depth,
            )
            return

        for post in posts:
            post_id = str(post.get("id") or "")
            if not post_id:
                continue
            known_post_ids.add(post_id)
            self.save_post(topic_id, post, depth, url)
            saved_post_count += 1

        for post_id in stream_ids:
            if post_id not in known_post_ids:
                self.process_post_json(post_id, self.url_for_path(f"/posts/{post_id}.json"), depth, topic_id)
                saved_post_count += 1

        self.processed_topics.add(topic_id)
        self.download_count += 1
        self.log(
            kind="topic",
            status="ok",
            url=url,
            json_url=topic_json_url,
            topic_id=topic_id,
            title=topic.get("title"),
            post_count=saved_post_count,
            depth=depth,
        )

    def find_post_by_number(self, posts: list[dict], post_number: str) -> dict | None:
        for post in posts:
            if str(post.get("post_number") or "") == post_number:
                return post
        return posts[0] if posts and post_number == "1" else None

    def fetch_post_by_number(self, topic_id: str, post_number: str, source_url: str) -> dict | None:
        candidates = [
            self.ensure_json_url(source_url),
            self.url_for_path(f"/posts/by_number/{topic_id}/{post_number}.json"),
        ]
        for candidate in dict.fromkeys(url for url in candidates if url):
            try:
                body, _headers = self.fetch(candidate, "application/json,text/javascript,*/*")
                payload = json.loads(body.decode("utf-8"))
            except (ArchiveError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                if str(payload.get("post_number") or "") == post_number:
                    return payload
                post = payload.get("post")
                if isinstance(post, dict) and str(post.get("post_number") or "") == post_number:
                    return post
                posts = ((payload.get("post_stream") or {}).get("posts") or [])
                found = self.find_post_by_number(posts, post_number)
                if found:
                    return found
        return None

    def ensure_json_url(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        if path.endswith(".json"):
            return urlunparse(parsed._replace(query="", fragment=""))
        return urlunparse(parsed._replace(path=path + ".json", query="", fragment=""))

    def extract_saved_topic(self, topic_id: str, depth: int, source_url: str, post_number: str | None = None) -> bool:
        posts_dir = self.out / "topics" / topic_id / "posts"
        if not posts_dir.exists():
            return False
        if self.args.first_post_only:
            target_post_number = str(post_number or "1")
            for path in sorted(posts_dir.glob("*.json")):
                try:
                    post = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if str(post.get("post_number") or "") == target_post_number:
                    stem = path.stem
                    md_path = posts_dir / f"{stem}.md"
                    html_path = posts_dir / f"{stem}.html"
                    found = True
                    self.extract_from_post_link_counts(post, source_url, depth)
                    if md_path.exists():
                        self.extract_from_text(md_path.read_text(encoding="utf-8", errors="replace"), source_url, depth)
                    if html_path.exists():
                        self.extract_from_html(html_path.read_text(encoding="utf-8", errors="replace"), source_url, depth)
                    return found
            return False
        found = False
        for path in sorted(posts_dir.glob("*.md")):
            found = True
            self.extract_from_text(path.read_text(encoding="utf-8", errors="replace"), source_url, depth)
        for path in sorted(posts_dir.glob("*.html")):
            found = True
            self.extract_from_html(path.read_text(encoding="utf-8", errors="replace"), source_url, depth)
        return found

    def process_post_json(self, post_id: str, url: str, depth: int, topic_id_hint: str | None) -> None:
        body, _headers = self.fetch(url, "application/json,text/javascript,*/*")
        try:
            post = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ArchiveError(f"Post JSON did not parse: {url}") from exc
        topic_id = str(post.get("topic_id") or topic_id_hint or "unknown")
        self.save_post(topic_id, post, depth, url)
        self.download_count += 1
        self.log(kind="post", status="ok", url=url, post_id=post_id, topic_id=topic_id, depth=depth)

    def save_post(self, topic_id: str, post: dict, depth: int, source_url: str) -> None:
        post_id = str(post.get("id") or "")
        if not post_id:
            return
        posts_dir = self.out / "topics" / topic_id / "posts"
        posts_dir.mkdir(parents=True, exist_ok=True)
        self.write_json(posts_dir / f"{post_id}.json", post)
        self.extract_from_post_link_counts(post, source_url, depth)

        raw = str(post.get("raw") or "")
        if not raw:
            try:
                raw = self.download_raw_post(post_id, self.url_for_path(f"/raw/{post_id}"))
            except ArchiveError as exc:
                self.log(kind="raw", status="error", url=self.url_for_path(f"/raw/{post_id}"), post_id=post_id, message=str(exc))
        if raw:
            (posts_dir / f"{post_id}.md").write_text(raw, encoding="utf-8")
            self.extract_from_text(raw, source_url, depth)

        cooked = str(post.get("cooked") or "")
        if cooked:
            (posts_dir / f"{post_id}.html").write_text(cooked, encoding="utf-8")
            self.extract_from_html(cooked, source_url, depth)

    def extract_from_post_link_counts(self, post: dict, base_url: str, depth: int) -> None:
        for link in post.get("link_counts") or []:
            if not isinstance(link, dict):
                continue
            url = str(link.get("url") or "")
            if not url:
                continue
            self.enqueue(url, depth + 1, base_url)

    def download_raw_post(self, post_id: str, url: str) -> str:
        raw_url = self.url_for_path(f"/raw/{post_id}")
        body, headers = self.fetch(raw_url, "text/plain,text/markdown,*/*")
        text = body.decode(self.charset_from_headers(headers), errors="replace")
        raw_dir = self.out / "raw"
        raw_dir.mkdir(exist_ok=True)
        (raw_dir / f"{post_id}.md").write_text(text, encoding="utf-8")
        self.download_count += 1
        return text

    def download_asset(self, url: str, depth: int) -> None:
        body, headers = self.fetch(url, "*/*", allow_external_redirect=True)
        path = self.local_asset_path(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        self.download_count += 1
        self.log(
            kind="asset",
            status="ok",
            url=url,
            path=str(path.relative_to(self.out)),
            content_type=headers.get("Content-Type", ""),
            bytes=len(body),
            depth=depth,
        )

    def download_page(self, url: str, depth: int) -> None:
        body, headers = self.fetch(url, "text/html,application/json,text/plain,*/*")
        content_type = headers.get("Content-Type", "")
        path = self.local_page_path(url, content_type)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        self.download_count += 1
        self.log(
            kind="page",
            status="ok",
            url=url,
            path=str(path.relative_to(self.out)),
            content_type=content_type,
            bytes=len(body),
            depth=depth,
        )

        text = body.decode(self.charset_from_headers(headers), errors="replace")
        if "html" in content_type.lower() or text.lstrip().startswith("<"):
            self.extract_from_html(text, url, depth)
        elif self.looks_textual(content_type):
            self.extract_from_text(text, url, depth)

    def extract_from_html(self, text: str, base_url: str, depth: int) -> None:
        parser = LinkParser()
        try:
            parser.feed(text)
        except Exception:
            pass
        for link in parser.links:
            self.enqueue(link, depth + 1, base_url)

    def extract_from_text(self, text: str, base_url: str, depth: int) -> None:
        urls: list[str] = []
        urls.extend(match.group("url") for match in MARKDOWN_LINK_RE.finditer(text))
        urls.extend(match.group("url").rstrip(".,;:") for match in MARKDOWN_URL_RE.finditer(text))
        for link in urls:
            self.enqueue(link, depth + 1, base_url)

    def fetch_topic_json(self, url: str, topic_id: str) -> tuple[str, bytes]:
        parsed = urlparse(url)
        candidates = []
        path = parsed.path.rstrip("/")
        if path.endswith(".json"):
            candidates.append(urlunparse(parsed._replace(query="", fragment="")))
        else:
            candidates.append(self.ensure_json_url(url))
            topic_path = self.topic_path_without_post_number(path)
            if topic_path:
                candidates.append(urlunparse(parsed._replace(path=topic_path + ".json", query="", fragment="")))
        candidates.append(self.url_for_path(f"/t/{topic_id}.json"))
        candidates.append(self.url_for_path(f"/t/-/{topic_id}.json"))

        last_error: ArchiveError | None = None
        for candidate in dict.fromkeys(candidates):
            try:
                body, _headers = self.fetch(candidate, "application/json,text/javascript,*/*")
                json.loads(body.decode("utf-8"))
                return candidate, body
            except ArchiveError as exc:
                last_error = exc
            except json.JSONDecodeError as exc:
                last_error = ArchiveError(f"Topic probe returned non-JSON: {candidate}: {exc}")
        raise last_error or ArchiveError(f"Could not find topic JSON for {url}")

    def topic_path_without_post_number(self, path: str) -> str | None:
        parsed_id = self.topic_id_from_path(path)
        if not parsed_id:
            return None
        clean = path[:-5] if path.endswith(".json") else path
        parts = [part for part in clean.strip("/").split("/") if part]
        try:
            t_index = parts.index("t")
        except ValueError:
            return None
        after = parts[t_index + 1 :]
        numeric_positions = [i for i, part in enumerate(after) if part.isdigit()]
        if not numeric_positions:
            return None
        topic_pos = numeric_positions[-2] if len(numeric_positions) >= 2 else numeric_positions[-1]
        topic_parts = parts[: t_index + 1] + after[: topic_pos + 1]
        return "/" + "/".join(topic_parts)

    def topic_id_from_url(self, url: str) -> str | None:
        return self.topic_id_from_path(urlparse(url).path)

    def topic_id_from_path(self, path: str) -> str | None:
        topic_ref = self.topic_ref_from_path(path)
        return topic_ref[0] if topic_ref else None

    def topic_ref_from_url(self, url: str) -> tuple[str, str | None] | None:
        return self.topic_ref_from_path(urlparse(url).path)

    def topic_ref_from_path(self, path: str) -> tuple[str, str | None] | None:
        clean = path[:-5] if path.endswith(".json") else path
        parts = [part for part in clean.strip("/").split("/") if part]
        if "t" not in parts:
            return None
        t_index = parts.index("t")
        after = parts[t_index + 1 :]
        numeric_positions = [index for index, part in enumerate(after) if part.isdigit()]
        if not numeric_positions:
            return None
        if len(numeric_positions) >= 2:
            topic_pos = numeric_positions[-2]
            post_pos = numeric_positions[-1]
            return after[topic_pos], after[post_pos]
        return after[numeric_positions[-1]], None

    def raw_post_id_from_url(self, url: str) -> str | None:
        match = RAW_PATH_RE.match(urlparse(url).path)
        return match.group("post_id") if match else None

    def post_id_from_url(self, url: str) -> str | None:
        match = POST_PATH_RE.match(urlparse(url).path)
        return match.group("post_id") if match else None

    def is_asset_url(self, url: str) -> bool:
        parsed = urlparse(url)
        path = parsed.path.lower()
        if any(marker in path for marker in ("/uploads/", "/secure-media-uploads/")):
            return True
        ext = posixpath.splitext(path)[1]
        return ext in ASSET_EXTENSIONS and not path.endswith(".json")

    def local_asset_path(self, url: str) -> Path:
        parsed = urlparse(url)
        path = unquote(parsed.path).lstrip("/") or "index"
        path = self.safe_relative_path(path)
        if parsed.query:
            stem = path.stem
            suffix = path.suffix
            path = path.with_name(f"{stem}-{self.url_hash(url)[:10]}{suffix}")
        return self.out / "assets" / parsed.netloc / path

    def local_page_path(self, url: str, content_type: str) -> Path:
        parsed = urlparse(url)
        ext = ".html"
        if "json" in content_type.lower():
            ext = ".json"
        elif self.looks_textual(content_type):
            ext = ".txt"
        name = self.url_hash(url) + ext
        return self.out / "pages" / name

    def safe_relative_path(self, path: str) -> Path:
        parts = []
        for part in Path(path).parts:
            if part in {"", ".", ".."}:
                continue
            parts.append(self.safe_name(part))
        return Path(*parts) if parts else Path("index")

    def safe_name(self, name: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
        return cleaned or self.url_hash(name)[:12]

    def write_json(self, path: Path, data: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def write_summary(self) -> None:
        summary = {
            "download_count": self.download_count,
            "processed_topics": sorted(self.processed_topics, key=lambda value: int(value) if value.isdigit() else value),
            "seen_urls": len(self.seen_urls),
            "output": str(self.out),
        }
        self.write_json(self.out / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)

    def url_for_path(self, path: str) -> str:
        clean_path, _, query = path.partition("?")
        return urlunparse((self.root_scheme, self.root_host, clean_path, "", query, ""))

    def url_hash(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]

    def charset_from_headers(self, headers: dict[str, str]) -> str:
        content_type = headers.get("Content-Type", "")
        for name, value in parse_qsl(content_type.replace(";", "&"), keep_blank_values=True):
            if name.strip().lower() == "charset" and value.strip():
                return value.strip()
        return "utf-8"

    def looks_textual(self, content_type: str) -> bool:
        lower = content_type.lower()
        return lower.startswith("text/") or any(marker in lower for marker in ("json", "xml", "markdown", "javascript"))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive logged-in Discourse topics and same-site links.")
    parser.add_argument("start", nargs="*", help="Seed URL(s), for example https://forum.example.com/t/topic/123")
    parser.add_argument("--input-file", help="Optional file containing seed URLs, one per line.")
    parser.add_argument("--root", help="Root URL to define the canonical host when only --input-file is used.")
    parser.add_argument(
        "--discover-latest",
        action="store_true",
        help="Discover seed topics from /latest.json pages before archiving.",
    )
    parser.add_argument("--latest-start-page", type=int, default=0, help="First /latest.json page to scan. Default: 0")
    parser.add_argument(
        "--latest-pages",
        type=int,
        default=0,
        help="Number of latest pages to scan. 0 means continue until an empty page. Default: 0",
    )
    parser.add_argument("--user-api-key", help="Discourse User API key. Prefer --user-api-key-env to avoid shell history.")
    parser.add_argument(
        "--user-api-key-env",
        default="DISCOURSE_USER_API_KEY",
        help="Environment variable containing a Discourse User API key. Default: DISCOURSE_USER_API_KEY",
    )
    parser.add_argument("--user-api-client-id", help="Optional Discourse User API client ID.")
    parser.add_argument(
        "--user-api-client-id-env",
        default="DISCOURSE_USER_API_CLIENT_ID",
        help="Environment variable containing an optional User API client ID. Default: DISCOURSE_USER_API_CLIENT_ID",
    )
    parser.add_argument("--cookies", help="Netscape-format cookies.txt exported from your logged-in browser session.")
    parser.add_argument("--cookie-env", help="Environment variable containing a raw Cookie header.")
    parser.add_argument("--out", default="discourse_archive", help="Output directory. Default: discourse_archive")
    parser.add_argument("--max-depth", type=int, default=2, help="Recursive same-site link depth. Default: 2")
    parser.add_argument(
        "--first-post-only",
        action="store_true",
        help="Archive one post per topic URL: explicit /topic/id/N links save post #N; bare topic links save #1.",
    )
    parser.add_argument(
        "--topic-links-only",
        action="store_true",
        help="Follow only topic/raw/post URLs plus images/assets; skip category/user/page recursion.",
    )
    parser.add_argument("--max-pages", type=int, default=0, help="Stop after this many downloads. 0 means no limit.")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between requests in seconds. Default: 1.0")
    parser.add_argument("--timeout", type=float, default=30.0, help="Request timeout in seconds. Default: 30")
    parser.add_argument("--retries", type=int, default=3, help="Retry count for rate limits/server errors. Default: 3")
    parser.add_argument("--max-redirects", type=int, default=10, help="Maximum redirects per request. Default: 10")
    parser.add_argument("--allow-host", action="append", default=[], help="Additional host:port allowed for same-site downloads.")
    parser.add_argument("--allow-subdomains", action="store_true", help="Allow subdomains of the seed host.")
    parser.add_argument("--no-resume", dest="resume", action="store_false", help="Do not skip URLs already logged as downloaded.")
    parser.set_defaults(resume=True)
    parser.add_argument(
        "--user-agent",
        default="Mozilla/5.0 discourse-archiver/0.1 (+read-only personal archive)",
        help="User-Agent header.",
    )
    args = parser.parse_args(argv)
    if not args.start and not args.input_file and not args.discover_latest:
        parser.error("provide at least one start URL, --input-file, or --discover-latest")
    if not args.start and not args.root:
        parser.error("--root is required when using --input-file or --discover-latest without positional start URLs")
    has_api_key = bool(args.user_api_key or os.environ.get(args.user_api_key_env, ""))
    has_cookies = bool(args.cookies or args.cookie_env)
    if not has_api_key and not has_cookies:
        print("warning: no User API key or cookies supplied; private topics will likely fail", file=sys.stderr)
    return args


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        DiscourseArchiver(args).run()
        return 0
    except ArchiveError as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
