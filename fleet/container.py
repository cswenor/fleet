"""Central-only container entrypoint; project supervisors remain separate services."""
import os
import tempfile

from fleet import central


def main():
    os.umask(0o077)
    raw = os.environ.get('FLEET_CENTRAL_CONFIG', '')
    if not raw.strip():
        raise SystemExit('fleet central: FLEET_CENTRAL_CONFIG is required')
    try:
        # The loader remains the single validator for project names and token envs.
        # Never include YAML parser errors: they can contain configuration values.
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         prefix='fleet-central-', suffix='.yml') as config:
            config.write(raw)
            config.flush()
            c = central.load(config.name)
        if (c['host'] != '0.0.0.0' or c.get('behind_proxy') is not True or
                c['port'] != 8787 or c['database'] != '/data/snapshots.sqlite3'):
            raise ValueError('invalid container binding or storage')
    except Exception:
        raise SystemExit('fleet central: invalid container configuration; requires '
                         'behind_proxy: true, host: 0.0.0.0, port: 8787, '
                         'database: /data/snapshots.sqlite3, and valid project token environments') from None
    central.serve(c)


if __name__ == '__main__':
    main()
