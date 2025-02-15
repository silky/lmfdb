# -*- coding: utf8 -*-
from lmfdb.base import LmfdbTest
import math
import unittest2


class EllCurveTest(LmfdbTest):

    # All tests should pass
    #
    def test_int_points(self):
		L = self.tc.get('/EllipticCurve/Q/234446.a1')
		assert '4532, 302803' in L.data
	
    def test_Cremona_label(self):
		L = self.tc.get('/EllipticCurve/Q/?label=400.e3&jump=label+or+isogeny+class')
		assert '15, 50' in L.data

    def test_Cremona_label_mal(self):
		L = self.tc.get('/EllipticCurve/Q/?label=Cremona%3A12qx&jump=label+or+isogeny+class')
		assert 'Could not understand label 12qx' in L.data

    def test_Cond_search(self):
		L = self.tc.get('/EllipticCurve/Q/?start=0&conductor=1200&jinv=&rank=&torsion=&torsion_structure=&sha_an=&optimal=&surj_primes=&surj_quantifier=include&nonsurj_primes=&count=100')
		assert '[0, 1, 0, -2133408, 1198675188]' in L.data

    def test_Weierstrass_search(self):
		L = self.tc.get('/EllipticCurve/Q/?label=[1%2C2%2C3%2C4%2C5]&jump=label+or+isogeny+class')
		assert '2, 3' in L.data

    def test_j_search(self):
		L = self.tc.get('/EllipticCurve/Q/?start=0&conductor=&jinv=2000&rank=&torsion=&torsion_structure=&sha_an=&optimal=&surj_primes=&surj_quantifier=include&nonsurj_primes=&count=100')
		assert '41616.bi2' in L.data

    def test_jbad_search(self):
		L = self.tc.get('/EllipticCurve/Q/?start=0&conductor=&jinv=2.3&rank=&torsion=&torsion_structure=&sha_an=&optimal=&surj_primes=&surj_quantifier=include&nonsurj_primes=&count=100')
		assert 'Error' in L.data
		assert 'rational number' in L.data

    def test_tors_search(self):
		L = self.tc.get('/EllipticCurve/Q/?start=0&conductor=&jinv=&rank=&torsion=&torsion_structure=[7]&sha_an=&optimal=&surj_primes=&surj_quantifier=include&nonsurj_primes=&count=100')
		assert '858.k1' in L.data
		assert '[1, -1, 1, 9588, 2333199]' in L.data

    def test_SurjPrimes_search(self):
		L = self.tc.get('/EllipticCurve/Q/?start=0&conductor=&jinv=&rank=&torsion=&torsion_structure=&sha_an=&optimal=&surj_primes=2&surj_quantifier=include&nonsurj_primes=&count=100')
		assert '[0, 0, 1, -270, -1708]' in L.data

    def test_NonSurjPrimes_search(self):
		L = self.tc.get('/EllipticCurve/Q/?start=0&conductor=&jinv=&rank=&torsion=&torsion_structure=&sha_an=&optimal=&surj_primes=&surj_quantifier=exactly&nonsurj_primes=37&count=100')
		assert '[0, 0, 0, -36705844875, 2706767485056250]' in L.data

    def test_isogeny_class(self):
		L = self.tc.get('/EllipticCurve/Q/11.a')
		assert '[0, -1, 1, 0, 0]' in L.data

    def test_dl_qexp(self):
		L = self.tc.get('/EllipticCurve/Q/download_qexp/66.c3/100')
		assert '0,1,1,1,1,-4,1,-2,1,1,-4,1,1,4,-2,-4,1,-2,1,0,-4,-2,1,-6,1,11,4,1,-2,10,-4,-8,1,1,-2,8,1,-2,0,4,-4,2,-2,4,1,-4,-6,-2,1,-3,11,-2,4,4,1,-4,-2,0,10,0,-4,-8,-8,-2,1,-16,1,-12,-2,-6,8,2,1,-6,-2,11,0,-2,4,10,-4,1,2,4,-2,8,4,10,1,10,-4,-8,-6,-8,-2,0,1,-2,-3,1,11' in L.data

    def test_dl_all(self):
		L = self.tc.get('/EllipticCurve/Q/download_all/26.b2')
		assert '[1, -1, 1, -3, 3]' in L.data
