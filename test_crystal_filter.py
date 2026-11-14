"""Testes geométricos sem gravação de assets; executáveis com unittest."""
import copy
import json
import unittest
from pathlib import Path

import numpy as np

import crystal_filter as core

BASE = json.loads((Path(__file__).parent/'config.json').read_text(encoding='utf-8'))


class CrystalFilterGeometryTests(unittest.TestCase):
    def test_each_template_is_connected_and_protects_focus(self):
        for template in core.TEMPLATES:
            with self.subTest(template=template):
                cfg = copy.deepcopy(BASE)
                cfg['geometry']['template'] = template
                cells = core.cells_in_canvas(1500,1200,cfg)
                neighbors = core.adjacency(cells)
                self.assertTrue(core.connected(set(cells),neighbors))
                protected = core.protected_cells(cells,1500,1200,cfg)
                rng = np.random.default_rng(123)
                selected,removed = core.prune_cells(cells,protected,neighbors,cfg,rng)
                holes,dark,light = core.choose_states(selected,protected,neighbors,cells,cfg,rng)
                self.assertFalse(set(removed)&protected)
                self.assertFalse(set(holes)&protected)
                self.assertEqual(len(holes),cfg['facets']['holes'])
                self.assertFalse(set(dark)&set(light))

    def test_reproducible_seeds(self):
        cfg = copy.deepcopy(BASE)
        cells = core.cells_in_canvas(1500,1200,cfg)
        neighbors = core.adjacency(cells)
        protected = core.protected_cells(cells,1500,1200,cfg)
        outcomes=[]
        for _ in range(2):
            rng=np.random.default_rng(555)
            selected,removed=core.prune_cells(cells,protected,neighbors,cfg,rng)
            holes,dark,light=core.choose_states(selected,protected,neighbors,cells,cfg,rng)
            outcomes.append((removed,holes,dark,light))
        self.assertEqual(outcomes[0],outcomes[1])

    def test_independent_size_presets(self):
        cfg=copy.deepcopy(BASE)
        cfg['geometry']['template']='square'
        self.assertEqual(core.dimensions(1500,1200,cfg),(110,110))
        cfg['geometry']['template']='rhombus'
        self.assertEqual(core.dimensions(1500,1200,cfg),(135,197))

    def test_legacy_sizes_key(self):
        cfg=copy.deepcopy(BASE)
        cfg['geometry']['sizes']=cfg['geometry'].pop('shapes')
        self.assertEqual(core.dimensions(1500,1200,cfg),(135,197))

    def test_legacy_v2_dimensions(self):
        cfg=copy.deepcopy(BASE)
        cfg['geometry'].pop('shapes')
        cfg['geometry']['horizontal_diagonal']=0.08977
        cfg['geometry']['vertical_diagonal']=0.16393
        cw,ch=core.dimensions(1500,1200,cfg)
        self.assertAlmostEqual(cw,134.655)
        self.assertAlmostEqual(ch,196.716)


if __name__ == '__main__':
    unittest.main()
