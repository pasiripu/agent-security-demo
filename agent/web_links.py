"""Find ordinary web links in issue text without requiring a special issue format."""
import html
import re
from .http_tool import normalize_public_url


def issue_links(body):
    links = []
    for match in re.finditer(r"\b(?:https?://|www\.)[^\s<>\"'`]+", body, re.IGNORECASE):
        candidate = html.unescape(match.group()).rstrip(".,;")
        for opening, closing in (("(", ")"), ("[", "]")):
            while candidate.endswith(closing) and candidate.count(closing) > candidate.count(opening):
                candidate = candidate[:-1]
        try:
            url = normalize_public_url(candidate)
        except ValueError:
            continue
        if url not in links:
            links.append(url)
    return links
