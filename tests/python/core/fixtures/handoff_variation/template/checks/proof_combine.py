import unittest

from app.combine import combine


class Proof(unittest.TestCase):
    def test_requirement(self):
        self.assertEqual(combine(2), 9)
