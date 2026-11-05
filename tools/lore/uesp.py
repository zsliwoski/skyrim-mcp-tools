"""Build the 'uesp' lore source from the UESP wiki (https://en.uesp.net), under its CC BY-SA license.

Two ways in: a MediaWiki XML export you downloaded yourself, or a polite crawl of the API.
"""
from tools.knowledge import mediawiki

SOURCE = "uesp"
LICENSE = "CC BY-SA 2.5"
ATTRIBUTION = "Text from the Unofficial Elder Scrolls Pages (UESP), https://en.uesp.net, CC BY-SA 2.5"
WIKI = mediawiki.Wiki(SOURCE, LICENSE, ATTRIBUTION, "https://en.uesp.net/w/api.php", "https://en.uesp.net/wiki/",
                      "skyrim-mcp-tools-lore/1.0 (local research index; respects rate limits)")
DEFAULT_NAMESPACES = ("Lore", "Skyrim")


def page_url(title):
    return WIKI.page_url(title)


def import_dump(con, path, namespaces=DEFAULT_NAMESPACES, progress=None):
    """Index pages of a MediaWiki XML export (.xml, .xml.gz or .xml.bz2) whose title is in a namespace."""
    return mediawiki.import_dump(con, WIKI, path, namespaces, progress)


def crawl(con, namespaces=DEFAULT_NAMESPACES, delay=1.0, limit=None, progress=None):
    """Fetch pages of the given namespaces through the API, one request per `delay` seconds."""
    return mediawiki.crawl(con, WIKI, namespaces, delay, limit, progress)
