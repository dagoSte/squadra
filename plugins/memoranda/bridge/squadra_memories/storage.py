"""Private mutable storage outside Store-managed bridge files."""
import os
from pathlib import Path

import plugins as p
import plugin_settings as files
from .source import Refusal


def output_directory(path):
    """Open the private directory through the same protected walk used by migration."""
    from migrate import directory
    try:
        return directory(path)
    except (p.Refused, OSError):
        raise Refusal('E-OUTPUT') from None


def data_root(home):
    try:
        with files.installed(home, 'memoranda', 'actions') as (parent, _):
            marker = files.optional(parent, '.migration-pending')
            if marker is not None:
                os.close(marker)
                raise Refusal('E-MIGRATION')
            try:
                os.mkdir('data', 0o700, dir_fd=parent)
                os.fsync(parent)
            except FileExistsError:
                pass
            fd = files.optional(parent, 'data', True)
            os.close(fd)
        return Path(home) / '.squadra/plugins/memoranda/data'
    except (p.Refused, OSError):
        raise Refusal('E-OUTPUT') from None
