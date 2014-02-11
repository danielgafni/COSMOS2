from . import web, db


def shell(session):
    import IPython
    from kosmos import *

    executions = session.query(Execution).all()
    ex = session.query(Execution).first()
    s = session.query(Stage).first()
    t = session.query(Task).first()
    IPython.embed()


def parse_args():
    import os
    from kosmos.db import get_scoped_session

    default_db_url = os.environ.get('KOSMOS_DB', None)
    import argparse

    parser = argparse.ArgumentParser(prog='kosmos', description=__doc__,
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    sps = parser.add_subparsers()

    sp = sps.add_parser('initdb', help=db.initdb.__doc__)
    sp.add_argument('-d', '--database_url', help='sqlalchemy database_url,'
                                                 'default is the environment variable `KOSMOS_DB` (%s)' % default_db_url,
                    default=default_db_url)
    sp.set_defaults(func=db.initdb)

    sp = sps.add_parser('resetdb', help=db.resetdb.__doc__)
    sp.add_argument('-d', '--database_url', help='sqlalchemy database_url,'
                                                 'default is the environment variable `KOSMOS_DB` (%s)' % default_db_url,
                    default=default_db_url)
    sp.set_defaults(func=db.resetdb)

    sp = sps.add_parser('shell', help=shell.__doc__)
    sp.add_argument('-d', '--database_url', help='sqlalchemy database_url,'
                                                 'default is the environment variable `KOSMOS_DB` (%s)' % default_db_url,
                    default=default_db_url)
    sp.set_defaults(func=shell)

    sp = sps.add_parser('runweb', help=web.runweb.__doc__)
    sp.add_argument('-p', '--port', type=int, default=4848,
                    help='port to bind the server to')
    sp.add_argument('-H', '--host', default='localhost',
                    help='host to bind the server to')
    sp.add_argument('-d', '--database_url', help='sqlalchemy database_url,'
                                                 'default is the environment variable `KOSMOS_DB` (%s)' % default_db_url,
                    default=default_db_url)
    sp.set_defaults(func=web.runweb)

    args = parser.parse_args()
    kwargs = dict(args._get_kwargs())
    del kwargs['func']

    debug = kwargs.pop('debug', False)
    db_url = kwargs.pop('database_url',None)
    if db_url:
        kwargs['session'] = get_scoped_session(database_url=db_url)()
    if debug:
        import ipdb

        with ipdb.launch_ipdb_on_exception():
            args.func(**kwargs)
    else:
        args.func(**kwargs)

