"""An oversized write-ahead log is trimmed after a write even when the writing command holds its own read views,
and the trim waits up to the documented 0.2 seconds for another reader to finish."""
import sqlite3
import threading
import time
import unittest

import test_wal_compact as support  # first: it puts scripts/ on the path
import registry
from registry import Store

LIMIT = support.LIMIT


class WalTrim(unittest.TestCase):
    setUp = support.WalCompact.setUp
    write_batch = support.WalCompact.write_batch
    passive = support.WalCompact.passive
    assert_backfilled = support.WalCompact.assert_backfilled
    cli = support.WalCompact.cli

    def oversized(self, keep_reader=False):
        """Grow the WAL past the limit behind a reader, then end that read unless the caller keeps it."""
        # check_same_thread=False: the bounded-wait test ends this read from a timer thread.
        reader = sqlite3.connect(self.db_path, isolation_level=None, check_same_thread=False)
        self.addCleanup(reader.close)
        reader.execute('BEGIN'); reader.execute('SELECT count(*) FROM events').fetchone()
        filler = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            filler.execute('CREATE TABLE wal_filler(body BLOB)')
            for _ in range(70):
                filler.execute('INSERT INTO wal_filler VALUES (randomblob(1048576))')
            filler.execute('DROP TABLE wal_filler')
        finally:
            filler.close()
        self.assertGreater(self.wal.stat().st_size, LIMIT, 'precondition: a WAL above the limit')
        if keep_reader:
            return reader
        reader.execute('COMMIT')

    def test_a_cli_write_trims_the_log_its_own_views_held_during_the_commit(self):
        self.oversized()
        rc, out, err = self.cli('send', '--actor', 'C', '--to', 'A', '--id', 'after-oversize', '--body-file', str(self.note()))
        self.assertEqual(rc, 0, out + err)
        self.assertLessEqual(self.wal.stat().st_size, LIMIT, 'the writing command trimmed the WAL when it closed')
        self.assert_backfilled('after the CLI write')

    def test_a_store_with_open_views_trims_when_it_closes(self):
        self.oversized()
        writer = Store(self.root)
        writer.view()
        self.write_batch(1, writer)
        writer.close()
        self.assertLessEqual(self.wal.stat().st_size, LIMIT)

    def test_the_trim_waits_up_to_the_documented_bound_for_another_reader(self):
        self.assertEqual(registry.TRIM_BUSY_MS, 200)
        reader = self.oversized(keep_reader=True)
        timer = threading.Timer(0.05, lambda: reader.execute('COMMIT'))
        timer.start()
        try:
            started = time.monotonic()
            self.write_batch(1)
            self.assertLess(time.monotonic() - started, 5)
        finally:
            timer.join()
        self.assertLessEqual(self.wal.stat().st_size, LIMIT, 'a reader that ends within 0.2 s does not keep the WAL')

    def note(self):
        path = self.base / 'note.txt'; path.write_text('After the oversized log\n')
        return path


if __name__ == '__main__':
    unittest.main()
