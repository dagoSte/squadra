"""Seams of the 3.5.1 integration: the task time line reads at the page's reading size."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TaskTimeLine(unittest.TestCase):
    def test_the_task_time_line_uses_the_reading_size(self):
        page = (ROOT / "assets/dashboard.html").read_text()
        self.assertIn(".sq-line.sq-task-time{font-size:16px;line-height:24px}", page)


if __name__ == "__main__":
    unittest.main()
