"""Talk to op1.fun without a browser.

Two things are needed to port op1.fun's core behaviour:

1. Patch discovery. The site renders patch lists server-side, so a plain GET
   plus a small parser is enough. No API key, no login for public patches.
2. The actual audio file. Every patch page embeds an S3 preview URL
   ``...uploads/patch/<id>/<name>.aiff.mp3``. The real AIFF sits beside it with
   the ``.mp3`` suffix removed, and S3 serves it anonymously.

Downloads are cached under ``data_dir/cache`` so a pack of 60 patches is
fetched once.
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

BASE = "https://op1.fun"
S3_PREVIEW = re.compile(
    r"https://op1fun\.s3\.amazonaws\.com/uploads/patch/\d+/[^\"']+?\.aif{1,2}\.mp3"
)
PATCH_HREF = re.compile(r'href="(/users/[^"/]+/patches/[^"/]+)"')
USER_AGENT = "op1-studio/0.1 (+local proxy)"
TIMEOUT = 30

PATCH_LINK = re.compile(
    r'<a[^>]+href="(/users/([^"/]+)/patches/([^"/]+))"[^>]*>(.*?)</a>', re.S
)
PACK_LINK = re.compile(r'href="(/users/[^"/]+/packs/[^"/]+)"')


class Op1FunError(RuntimeError):
    pass


@dataclass
class Patch:
    title: str
    url: str
    user: str
    kind: str  # drum | synth | sampler | unknown
    file_url: str | None = None
    size_bytes: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def patch_type(slug: str) -> str:
    """Guess the engine type from a patch slug or filename.

    Slugs look like ``patch-drum-<uuid>``, ``synth_-_guitar`` or
    ``patch-sampler-<uuid>``; the AIFF metadata is authoritative, this is only
    what the list page gives us before download.
    """
    s = slug.lower()
    if "drum" in s:
        return "drum"
    if "sampl" in s:
        return "sampler"
    if "synth" in s or "bass" in s or "lead" in s or "pad" in s:
        return "synth"
    return "unknown"


def preview_url(html: str) -> str | None:
    """Return the real AIFF URL behind a page's S3 ``.mp3`` preview, if any."""
    m = S3_PREVIEW.search(html)
    if not m:
        return None
    url = m.group(0)[: -len(".mp3")]
    if url.endswith(".aif"):
        url += "f"
    return url


def parse_patches(html: str) -> list[Patch]:
    """Extract the patch cards out of a ``/patches`` listing page."""
    patches: list[Patch] = []
    seen: set[str] = set()
    for m in PATCH_LINK.finditer(html):
        path, user, slug, inner = m.groups()
        if path.endswith("/like") or path in seen:
            continue
        seen.add(path)
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", inner)).strip() or slug
        patches.append(
            Patch(title=title, url=BASE + path, user=user, kind=patch_type(slug))
        )

    for m in PACK_LINK.finditer(html):
        path = m.group(1)
        if path in seen:
            continue
        seen.add(path)
        slug = path.rsplit("/", 1)[-1]
        patches.append(
            Patch(title=slug, url=BASE + path, user=path.split("/")[2], kind="pack")
        )
    return patches


def parse_pack(html: str) -> tuple[str, list[str]]:
    """Return (title, member patch urls) for a pack page."""
    og = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
    title = og.group(1) if og else ""
    if not title:
        page_title = re.search(r"<title>(.*?)</title>", html, re.S)
        if page_title:
            title = re.sub(r"<[^>]+>", "", page_title.group(1))
    title = re.sub(r"\s+", " ", title).strip()
    if " by " in title:
        title = title.split(" by ", 1)[0]
    elif " - " in title and not og:
        title = title.split(" - ", 1)[-1]

    urls: list[str] = []
    seen: set[str] = set()
    for m in PATCH_HREF.finditer(html):
        path = m.group(1)
        if path in seen:
            continue
        seen.add(path)
        urls.append(BASE + path)
    return (title or "pack"), urls


def _get(path: str, params: dict | None = None) -> bytes:
    """Fetch a site path (or a full URL) and return the raw body.

    Relative paths are resolved against BASE so callers never rebuild the host,
    and ``params`` are encoded with urllib so Ransack's bracketed keys survive.
    """
    url = path if path.startswith("http") else BASE + path
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read()
    except Exception as exc:  # urllib raises a wide family
        raise Op1FunError(f"GET {url} failed: {exc}") from exc


def _text(raw: bytes | str) -> str:
    """Bodies are bytes in production and str in tests; accept both."""
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw


def list_patches(page: int = 1, query: str = "", kind: str = "") -> dict:
    """Return one page of the public patch list.

    The site's search form uses Ransack-style parameters:
    ``q[name_cont]`` for the name filter and ``q[patch_type_cont]`` for the
    engine type. ``?q=`` alone returns a 500, so the bracket form is required.
    """
    params: dict[str, str | int] = {"page": page}
    if query:
        params["q[name_cont]"] = query
    if kind:
        params["q[patch_type_cont]"] = kind
    html = _text(_get("/patches", params=params))

    patches: list[Patch] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a[^>]+href="(/users/([^"/]+)/patches/([^"/]+))"[^>]*>(.*?)</a>',
        html,
        re.S,
    ):
        path, user, slug, inner = m.groups()
        if path.endswith("/like") or path in seen:
            continue
        seen.add(path)
        title = re.sub(r"<[^>]+>", "", inner)
        title = re.sub(r"\s+", " ", title).strip() or slug
        kind = "unknown"
        if "drum" in slug:
            kind = "drum"
        elif "sample" in slug:
            kind = "sampler"
        patches.append(
            Patch(title=title, url=BASE + path, user=user, kind=kind)
        )

    last_page = max(
        (int(n) for n in re.findall(r"/patches\?page=(\d+)", html)), default=page
    )
    return {
        "page": page,
        "last_page": last_page,
        "patches": [p.to_dict() for p in patches],
        "total_visible": len(patches),
    }


def resolve_file_url(patch_url: str) -> str:
    """Find the real .aiff URL behind a patch page."""
    html = _text(_get(patch_url))
    m = S3_PREVIEW.search(html)
    if not m:
        raise Op1FunError(f"no downloadable AIFF on {patch_url}")
    return m.group(0)[: -len(".mp3")]


def patch_kind_from_page(patch_url: str) -> str:
    html = _text(_get(patch_url))
    m = re.search(r"Type</[^>]+>\s*<[^>]+>([a-zA-Z]+)", html)
    if m:
        return m.group(1).lower()
    return "unknown"


def read_aiff_metadata(path: Path) -> dict:
    """Parse the OP-1 ``APPL`` chunk out of an AIFF/AIFC file.

    The chunk signature is ``op-1`` and the payload is NUL-padded JSON with
    ``type``, ``start``/``end`` slices and engine parameters. This is the same
    information the original Electron app read to categorise patches.
    """
    data = Path(path).read_bytes()
    if data[:4] != b"FORM":
        raise Op1FunError(f"{path.name}: not an AIFF (missing FORM)")
    form = data[8:12]
    if form not in (b"AIFC", b"AIFF"):
        raise Op1FunError(f"{path.name}: unexpected form {form!r}")

    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos : pos + 4]
        size = struct.unpack(">I", data[pos + 4 : pos + 8])[0]
        body = data[pos + 8 : pos + 8 + size]
        if cid == b"APPL" and body[:4] == b"op-1":
            payload = body[4:].replace(b"\x00", b"").strip()
            try:
                return json.loads(payload.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                return {}
        pos += 8 + size + (size & 1)
    return {}


def download_patch(
    patch_url: str, cache_dir: Path, *, kind: str | None = None
) -> tuple[Path, dict]:
    """Download one patch into the cache and return (path, metadata).

    The file keeps its original name but is stored under a per-patch folder so
    two users' ``patch.aif`` never collide.
    """
    file_url = resolve_file_url(patch_url)
    name = Path(urllib.parse.unquote(file_url)).name
    digest = hashlib.sha1(patch_url.encode()).hexdigest()[:10]
    folder = cache_dir / "patches" / digest
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    if not dest.exists():
        dest.write_bytes(_get(file_url))

    meta = read_aiff_metadata(dest)
    meta.setdefault("type", kind or patch_kind_from_page(patch_url))
    meta["source_url"] = patch_url
    meta["file_url"] = file_url
    meta["file"] = str(dest)
    meta["bytes"] = dest.stat().st_size
    return dest, meta


def list_pack(pack_url: str) -> dict:
    """Expand a pack page into its member patches."""
    html = _text(_get(pack_url))
    title = ""
    og = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
    if og:
        title = og.group(1)
    else:
        page_title = re.search(r"<title>(.*?)</title>", html, re.S)
        if page_title:
            title = re.sub(r"<[^>]+>", "", page_title.group(1))
            title = title.split(" - ", 1)[-1].split(" by ")[0]
    title = re.sub(r"\s+", " ", title).strip() or "pack"

    members: list[dict] = []
    seen: set[str] = set()
    for m in PATCH_HREF.finditer(html):
        path = m.group(1)
        if path in seen:
            continue
        seen.add(path)
        members.append({"url": BASE + path})
    if not members:
        raise Op1FunError(f"no patches found in {pack_url}")
    return {"title": title, "url": pack_url, "patches": members}
