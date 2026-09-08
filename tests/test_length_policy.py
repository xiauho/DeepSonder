from unittest import TestCase

from core.length_policy import (
    LENGTH_OVER,
    LENGTH_QUALIFIED,
    LENGTH_SEVERELY_OVER,
    LENGTH_SEVERELY_UNDER,
    LENGTH_UNDER,
    assess_length,
)


class LengthPolicyTests(TestCase):
    def test_three_thousand_character_ranges_are_stable(self) -> None:
        assessment = assess_length(3000, 3000)
        self.assertEqual(assessment.preferred_min, 2850)
        self.assertEqual(assessment.preferred_max, 3150)
        self.assertEqual(assessment.review_min, 2700)
        self.assertEqual(assessment.review_max, 3300)
        self.assertEqual(assessment.status, LENGTH_QUALIFIED)

    def test_every_length_band_is_classified(self) -> None:
        self.assertEqual(assess_length(2497, 3000).status, LENGTH_SEVERELY_UNDER)
        self.assertEqual(assess_length(2800, 3000).status, LENGTH_UNDER)
        self.assertEqual(assess_length(3000, 3000).status, LENGTH_QUALIFIED)
        self.assertEqual(assess_length(3200, 3000).status, LENGTH_OVER)
        self.assertEqual(assess_length(3400, 3000).status, LENGTH_SEVERELY_OVER)
