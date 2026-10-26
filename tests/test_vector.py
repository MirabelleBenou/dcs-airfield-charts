"""Masque des batiments : trous comblés (lot parkings-v2)."""
import numpy as np

from render import vector


def _scene(holes, mpp=0.5, size=200):
    img = np.full((size, size, 3), 255, np.uint8)
    img[40:140, 40:140] = vector.BUILDING_RGB
    for (r0, r1, c0, c1) in holes:
        img[r0:r1, c0:c1] = 255
    pav = np.zeros((size, size), bool)
    pav[145:150, 40:140] = True
    return img, pav, mpp


def test_hole_of_30_m2_is_filled_and_60_m2_is_kept():
    # 0,5 m / px : 1 px = 0,25 m2 ; trou de 11 x 11 = 30,25 m2 ; trou de 16 x 15 = 60 m2
    img, pav, mpp = _scene([(50, 61, 50, 61), (80, 96, 80, 95)])
    m, n = vector.building_mask(img, pav, mpp)
    assert n == 1
    assert m[50:61, 50:61].all()
    assert not m[80:96, 80:95].any()


def test_hole_touching_the_edge_of_the_mosaic_is_not_filled():
    img = np.full((120, 120, 3), 255, np.uint8)
    img[0:100, 0:100] = vector.BUILDING_RGB
    img[0:6, 40:46] = 255                                  # le trou touche le bord haut
    pav = np.zeros((120, 120), bool)
    pav[105:110, 0:100] = True
    m, n = vector.building_mask(img, pav, 0.5)
    assert n == 1 and not m[0:6, 40:46].any()


def test_hole_exactly_40_m2_is_filled_and_count_is_unchanged():
    img, pav, mpp = _scene([(60, 70, 60, 76)])             # 10 x 16 px = 160 px = 40 m2
    m, n = vector.building_mask(img, pav, mpp)
    assert n == 1 and m[60:70, 60:76].all()
