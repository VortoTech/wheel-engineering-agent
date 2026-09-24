import unittest
import numpy as np
from generate import sections


class ControlsTest(unittest.TestCase):
    def test_pair_symmetry_and_positive_sections(self):
        a,b=sections()
        self.assertEqual(len(a),15)
        for left,right in zip(a,b):
            np.testing.assert_allclose(left['center'],[-right['center'][0],*right['center'][1:]],atol=1e-10)
            self.assertGreater(left['width'],0)
            self.assertGreater(left['height'],0)
            self.assertLess(left['width'],20)
            self.assertAlmostEqual(np.linalg.norm(left['normal']),1)
            self.assertAlmostEqual(np.dot(left['normal'],left['tangent']),0)


if __name__=='__main__': unittest.main()
