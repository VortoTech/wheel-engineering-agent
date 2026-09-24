import copy
import json
import unittest

import numpy as np
from prepare import ANNOTATION,camera_fit,lift,project,smooth_boundary


class SectorEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.data=json.loads(ANNOTATION.read_text())
        self.pose=camera_fit(self.data)

    def test_six_groups_not_previous_five(self):
        self.assertEqual(self.data['structure']['spoke_groups'],6)
        self.assertEqual(len(self.data['structure']['lug_centers']),6)
        self.assertEqual(self.data['validation']['group_rotation_deg'],60)

    def test_camera_is_rotation_not_shear(self):
        r=np.array(self.pose['rotation'])
        np.testing.assert_allclose(r@r.T,np.eye(3),atol=1e-12)
        self.assertAlmostEqual(np.linalg.det(r),1)
        self.assertGreater(self.pose['sag'],0)

    def test_boundary_lift_roundtrip_is_construction_only(self):
        p=smooth_boundary(self.data['master']['boundary'])
        xyz=lift(p,self.pose)
        self.assertTrue(np.isfinite(xyz).all())
        np.testing.assert_allclose(project(xyz,self.pose),p,atol=1e-8)

    def test_diagnostic_points_do_not_fit_camera(self):
        modified=copy.deepcopy(self.data)
        modified['validation']['points']=[[0,0]]
        self.assertEqual(camera_fit(modified),self.pose)
        self.assertTrue(self.data['validation']['used_for_topology_diagnosis'])
        self.assertFalse(self.data['validation']['used_for_optimization'])

    def test_input_vertices_retained(self):
        p=np.array(self.data['master']['boundary'])
        dense=smooth_boundary(p.tolist())
        error=np.linalg.norm(p[:,None]-dense[None],axis=2).min(axis=1)
        self.assertLess(error.max(),1e-8)


if __name__=='__main__':unittest.main()
