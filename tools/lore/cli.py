"""Command line for building and querying the local lore database.

    python -m tools.lore build uesp-dump <file.xml[.gz|.bz2]> [--namespaces Lore Skyrim]
    python -m tools.lore build uesp-crawl [--namespaces Lore Skyrim] [--delay 1.0] [--limit N]
    python -m tools.lore build game-books [--plugins Skyrim.esm ...]
    python -m tools.lore build vectors [--model BAAI/bge-small-en-v1.5]   (optional, needs fastembed)
    python -m tools.lore status
    python -m tools.lore search <query> [--limit 10] [--source uesp|game] [--mode auto|keyword|semantic|hybrid]
"""
import argparse
import json
import sys

from . import db, uesp, vectors


def _print(msg):
    print(msg, file=sys.stderr, flush=True)


def build_game_books(con, plugins=None):
    import server
    from .game import index_books
    return index_books(con, server.get_model(plugins))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.lore", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build', help="build or refresh one source (replaces that source's rows)")
    bs = b.add_subparsers(dest='what', required=True)
    d = bs.add_parser('uesp-dump', help="import a UESP MediaWiki XML export you downloaded")
    d.add_argument('file')
    c = bs.add_parser('uesp-crawl', help="fetch pages from the UESP API (slow; prefer a dump)")
    c.add_argument('--delay', type=float, default=1.0, help="seconds between requests")
    c.add_argument('--limit', type=int, help="stop after about this many pages")
    for p in (d, c):
        p.add_argument('--namespaces', nargs='+', default=list(uesp.DEFAULT_NAMESPACES))
    g = bs.add_parser('game-books', help="index book text from your installed Skyrim plugins")
    g.add_argument('--plugins', nargs='+')
    v = bs.add_parser('vectors', help="embed chunks for semantic search (adds only chunks without a vector)")
    v.add_argument('--model', default=vectors.DEFAULT_MODEL)
    v.add_argument('--batch', type=int, default=64)
    sub.add_parser('status', help="show what is indexed")
    s = sub.add_parser('search', help="query the database")
    s.add_argument('query')
    s.add_argument('--limit', type=int, default=10)
    s.add_argument('--source')
    s.add_argument('--mode', default='auto', choices=['auto', 'keyword', 'semantic', 'hybrid'])
    a = ap.parse_args(argv)

    if a.cmd == 'build':
        con = db.connect(create=True)
        if a.what == 'uesp-dump':
            n = uesp.import_dump(con, a.file, a.namespaces, _print)
        elif a.what == 'uesp-crawl':
            n = uesp.crawl(con, a.namespaces, a.delay, a.limit, _print)
        elif a.what == 'vectors':
            n = vectors.build(con, model=a.model, batch=a.batch, progress=_print)
        else:
            n = build_game_books(con, a.plugins)
        print(f"indexed {n} chunks into {db.db_path()}")
        return
    con = db.connect()
    if a.cmd == 'status':
        print(json.dumps(db.status(con), indent=2))
    else:
        print(json.dumps(vectors.search(con, a.query, a.limit, a.source, a.mode), indent=2))


if __name__ == "__main__":
    main()
