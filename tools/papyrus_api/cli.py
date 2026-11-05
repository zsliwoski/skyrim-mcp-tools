"""Command line for building and querying the local Papyrus API database (separate from the lore database).

    python -m tools.papyrus_api build scripts [--source-dir ORIGIN=FOLDER ...] [--all-scripts]
    python -m tools.papyrus_api build actor-values [--plugins Skyrim.esm ...]
    python -m tools.papyrus_api build ckwiki-dump <file.xml[.gz|.bz2]> [--namespaces ""]
    python -m tools.papyrus_api build ckwiki-crawl [--delay 1.0] [--limit N]
    python -m tools.papyrus_api build vectors [--model BAAI/bge-small-en-v1.5]   (optional, needs fastembed)
    python -m tools.papyrus_api status
    python -m tools.papyrus_api get <Script> [Member]
    python -m tools.papyrus_api search <query> [--limit 10] [--source psc|actor-values|ckwiki] [--mode ...]
"""
import argparse
import json
import sys

from tools.knowledge import vectors

from . import api

def _print(msg):
    print(msg, file=sys.stderr, flush=True)


def default_script_dirs():
    """CK-extracted vanilla sources plus any SKSE source folders, as (origin, folder) pairs."""
    from tools.papyrus.papyrus_build import CompileError, ensure_sources, skse_source_dirs
    dirs = [('skse', d) for d in skse_source_dirs()]
    try:
        dirs.insert(0, ('skyrim', ensure_sources()))
    except CompileError:
        if not dirs:
            raise
    return dirs


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.papyrus_api", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build', help="build or refresh one source (replaces that source's rows)")
    bs = b.add_subparsers(dest='what', required=True)
    s = bs.add_parser('scripts', help="index .psc sources: signatures of functions, events and properties")
    s.add_argument('--source-dir', nargs='+', metavar='ORIGIN=FOLDER',
                   help="explicit folders, origin 'skyrim' or 'skse' (skse copies win and flag SKSE-only members)")
    s.add_argument('--all-scripts', action='store_true',
                   help="also index content scripts and quest fragments (default: engine API only, scripts with native functions)")
    a = bs.add_parser('actor-values', help="index actor values from your installed plugins")
    a.add_argument('--plugins', nargs='+')
    d = bs.add_parser('ckwiki-dump', help="import a Creation Kit wiki XML export you downloaded")
    d.add_argument('file')
    c = bs.add_parser('ckwiki-crawl', help="fetch Creation Kit wiki pages through the API (slow)")
    c.add_argument('--delay', type=float, default=1.0)
    c.add_argument('--limit', type=int)
    for p in (d, c):
        p.add_argument('--namespaces', nargs='+', default=[''], help="'' is the main namespace")
    v = bs.add_parser('vectors', help="embed chunks for semantic search (adds only chunks without a vector)")
    v.add_argument('--model', default=vectors.DEFAULT_MODEL)
    v.add_argument('--batch', type=int, default=64)
    sub.add_parser('status', help="show what is indexed")
    g = sub.add_parser('get', help="exact lookup of a script or one of its members")
    g.add_argument('script')
    g.add_argument('member', nargs='?')
    q = sub.add_parser('search', help="query the database")
    q.add_argument('query')
    q.add_argument('--limit', type=int, default=10)
    q.add_argument('--source')
    q.add_argument('--mode', default='auto', choices=['auto', 'keyword', 'semantic', 'hybrid'])
    args = ap.parse_args(argv)

    if args.cmd == 'build':
        con = api.connect(create=True)
        if args.what == 'scripts':
            dirs = ([tuple(x.split('=', 1)) for x in args.source_dir] if args.source_dir
                    else default_script_dirs())
            n = api.index_scripts(con, dirs, native_only=not args.all_scripts)
        elif args.what == 'actor-values':
            import server
            n = api.index_actor_values(con, server.get_model(args.plugins))
        elif args.what == 'ckwiki-dump':
            n = api.import_ckwiki_dump(con, args.file, args.namespaces, _print)
        elif args.what == 'ckwiki-crawl':
            n = api.crawl_ckwiki(con, args.namespaces, args.delay, args.limit, _print)
        else:
            n = vectors.build(con, model=args.model, batch=args.batch, progress=_print)
        print(f"indexed {n} chunks into {api.db_path()}")
        return
    con = api.connect()
    if args.cmd == 'status':
        print(json.dumps(api.status(con), indent=2))
    elif args.cmd == 'get':
        print(json.dumps(api.get_script(con, args.script, args.member), indent=2))
    else:
        print(json.dumps(api.search(con, args.query, args.limit, args.source, args.mode), indent=2))


if __name__ == "__main__":
    main()
