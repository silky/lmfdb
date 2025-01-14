# -*- coding: utf-8 -*-
import re
import tempfile
import os
from pymongo import ASCENDING, DESCENDING
from flask import url_for, make_response
import lmfdb.base
from lmfdb.utils import comma, make_logger, web_latex, encode_plot
from lmfdb.elliptic_curves import ec_page, ec_logger

import sage.all
from sage.all import EllipticCurve, latex, matrix, ZZ, QQ

cremona_label_regex = re.compile(r'(\d+)([a-z]+)(\d*)')
lmfdb_label_regex = re.compile(r'(\d+)\.([a-z]+)(\d*)')
sw_label_regex = re.compile(r'sw(\d+)(\.)(\d+)(\.*)(\d*)')

logger = make_logger("ec")

ecdb = None
padicdb = None

def db_ec():
    global ecdb
    if ecdb is None:
        ecdb = lmfdb.base.getDBConnection().elliptic_curves.curves
    return ecdb

def padic_db():
    global padicdb
    if padicdb is None:
        padicdb = lmfdb.base.getDBConnection().elliptic_curves.padic_db
    return padicdb

def trim_galois_image_code(s):
    return s[2:] if s[1].isdigit() else s[1:]

def parse_list(s):
    """
    parses a string representing a list of integers, e.g. '[1,2,3]'
    """
    s = s.replace(' ','')[1:-1]
    if s:
        return [int(a) for a in s.split(",")]
    return []

def parse_point(s):
    r""" Converts a string representing a point in affine or
    projective coordinates to a tuple of rationals.

    Sample input: '(-565,282)', '(4599/4,-4603/8)', '(555:10778:1)',
    '(1055:-32778:1)'
    """
    # strip parentheses and spaces
    s = s.replace(' ','')[1:-1]
    if ',' in s: # affine: comma-separated rationals
        return [QQ(str(c)) for c in s.split(',')]
    if ':' in s: # projective: colon-separated integers
        cc = [ZZ(str(c)) for c in s.split(':')]
        return [cc[0]/cc[2], cc[1]/cc[2]]
    return []

def parse_points(s):
    r""" Converts a list of strings representing points in affine or
    projective coordinates to a list of tuples of rationals.

    Sample input:  ['(-565,282)', '(4599/4,-4603/8)']
                   ['(555:10778:1)', '(1055:-32778:1)']
    """
    return [parse_point(P) for P in s]

class WebEC(object):
    """
    Class for an elliptic curve over Q
    """
    def __init__(self, dbdata):
        """
        Arguments:

            - dbdata: the data from the database
        """
        logger.info("Constructing an instance of ECisog_class")
        self.__dict__.update(dbdata)
        # Next lines because the hyphens make trouble
        self.xintcoords = parse_list(dbdata['x-coordinates_of_integral_points'])
        self.non_surjective_primes = dbdata['non-surjective_primes']
        self.make_curve()

    @staticmethod
    def by_label(label):
        """
        Searches for a specific elliptic curve in the curves
        collection by its label, which can be either in LMFDB or
        Cremona format.
        """
        print "curve label = %s" % label
        try:
            N, iso, number = lmfdb_label_regex.match(label).groups()
            data = db_ec().find_one({"lmfdb_label" : label})
        except AttributeError:
            try:
                N, iso, number = cremona_label_regex.match(label).groups()
                data = db_ec().find_one({"label" : label})
            except AttributeError:
                return "Invalid label" # caller must catch this and raise an error

        if data:
            return WebEC(data)
        return "Curve not found" # caller must catch this and raise an error

    def make_curve(self):
        # To start with the data fields of self are just those from
        # the database.  We need to reformat these, construct the
        # actual elliptic curve E, and compute some further (easy)
        # data about it.
        #

        # Weierstrass equation

        data = self.data = {}
        data['ainvs'] = [int(ai) for ai in self.ainvs]
        self.E = EllipticCurve(data['ainvs'])
        data['equation'] = web_latex(self.E)

        # conductor, j-invariant and discriminant

        data['conductor'] = N = ZZ(self.conductor)
        bad_primes = N.prime_factors()
        try:
            data['j_invariant'] = QQ(str(self.jinv))
        except KeyError:
            data['j_invariant'] = self.E.j_invariant()
        data['j_inv_factor'] = latex(0)
        if data['j_invariant']:
            data['j_inv_factor'] = latex(data['j_invariant'].factor())
        data['j_inv_str'] = unicode(str(data['j_invariant']))
        data['j_inv_latex'] = web_latex(data['j_invariant'])
        data['disc'] = self.E.discriminant()
        data['disc_latex'] = web_latex(data['disc'])
        data['disc_factor'] = latex(data['disc'].factor())
        data['cond_factor'] =latex(N.factor())
        data['cond_latex'] = web_latex(N)

        # CM and endomorphism ring

        data['CMD'] = 0
        data['CM'] = "no"
        data['EndE'] = "\(\Z\)"
        if self.E.has_cm():
            data['CMD'] = self.E.cm_discriminant()
            data['CM'] = "yes (\(D=%s\))" % data['CMD']
            if data['CMD']%4==0:
                d4 = ZZ(data['CMD'])//4
                data['EndE'] = "\(\Z[\sqrt{%s}]\)" % d4
            else:
                data['EndE'] = "\(\Z[(1+\sqrt{%s})/2]\)" % data['CMD']

        # modular degree

        try:
            data['degree'] = self.degree
        except AttributeError:
            try:
                data['degree'] = self.E.modular_degree()
            except RuntimeError:
                data['degree']  # invalid, but will be displayed nicely

        # Minimal quadratic twist

        E_pari = self.E.pari_curve(prec=200)
        from sage.libs.pari.all import PariError
        try:
            minq = self.E.minimal_quadratic_twist()[0]
        except PariError:  # this does occur with 164411a1
            ec.debug("PariError computing minimal quadratic twist of elliptic curve %s" % lmfdb_label)
            minq = self.E
        if self.E == minq:
            data['minq_label'] = self.lmfdb_label
        else:
            minq_ainvs = [str(c) for c in minq.ainvs()]
            data['minq_label'] = db_ec().find_one({'ainvs': minq_ainvs})['lmfdb_label']

        # rational and integral points

        mw = self.mw = {}

        xintpoints_projective = [self.E.lift_x(x) for x in self.xintcoords]
        xintpoints = [P.xy() for P in xintpoints_projective]
        mw['int_points'] = ', '.join(web_latex(P) for P in xintpoints)

        # Generators of infinite order

        mw['rank'] = self.rank
        try:
            mw['generators'] = ', '.join(web_latex(self.E(g).xy()) for g in parse_points(self.gens))
        except AttributeError:
            mw['generators'] = ''

        # Torsion subgroup: order, structure, generators

        mw['tor_order'] = self.torsion
        tor_struct = [int(c) for c in self.torsion_structure]
        if mw['tor_order'] == 1:
            mw['tor_struct'] = '\mathrm{Trivial}'
            mw['tor_gens'] = ''
        else:
            mw['tor_struct'] = ' \\times '.join(['\Z/{%s}\Z' % n
                                                 for n in tor_struct])
            mw['tor_gens'] = ', '.join(web_latex(self.E(g).xy()) for g in parse_points(self.torsion_generators))

        # Images of Galois representations

        try:
            data['galois_images'] = [trim_galois_image_code(s) for s in self.galois_images]
            data['non_surjective_primes'] = self.non_surjective_primes
        except AttributeError:
            print "No Galois image data"
            data['galois_images'] = []
            data['non_surjective_primes'] = []

        data['galois_data'] = [{'p': p,'image': im }
                               for p,im in zip(data['non_surjective_primes'],
                                               data['galois_images'])]

        # Leading term of L-function & BSD data

        bsd = self.bsd = {}

        if mw['rank'] >= 2:
            bsd['lder_name'] = "L^{(%s)}(E,1)" % mw['rank']
        elif mw['rank']:
            bsd['lder_name'] = "L'(E,1)"
        else:
            bsd['lder_name'] = "L(E,1)"

        bsd['reg'] = self.regulator
        bsd['omega'] = self.real_period
        bsd['sha'] = int(0.1+self.sha_an)
        bsd['lder'] = self.special_value

        # Optimality (the optimal curve in the class is the curve
        # whose Cremona label ends in '1' except for '990h' which was
        # labelled wrongly long ago)

        if self.iso == '990h':
            data['Gamma0optimal'] = bool(self.number == 3)
        else:
            data['Gamma0optimal'] = bool(self.number == 1)


        data['p_adic_data_exists'] = False
        if data['Gamma0optimal']:
            data['p_adic_data_exists'] = (padic_db().find({'lmfdb_iso': self.lmfdb_iso}).count()) > 0
        data['p_adic_primes'] = [p for p in sage.all.prime_range(5, 100)
                                 if self.E.is_ordinary(p) and not p.divides(N)]

        # Local data

        local_data = self.local_data = []
        # if we use E.tamagawa_numbers() it calls E.local_data(p) which
        # crashes on some curves e.g. 164411a1
        tamagawa_numbers = []
        for p in bad_primes:
            local_info = self.E.local_data(p, algorithm="generic")
            local_data_p = {}
            local_data_p['p'] = p
            local_data_p['tamagawa_number'] = local_info.tamagawa_number()
            tamagawa_numbers.append(ZZ(local_info.tamagawa_number()))
            local_data_p['kodaira_symbol'] = web_latex(local_info.kodaira_symbol()).replace('$', '')
            local_data_p['reduction_type'] = local_info.bad_reduction_type()
            local_data.append(local_data_p)

        bsd['tamagawa_factors'] = r' \cdot '.join(str(c.factor()) for c in tamagawa_numbers)
        bsd['tamagawa_product'] = sage.misc.all.prod(tamagawa_numbers)

        mod_form_iso = lmfdb_label_regex.match(self.lmfdb_iso).groups()[1]
        data['newform'] =  web_latex(self.E.q_eigenform(10))

        self.friends = [
            ('Isogeny class ' + self.lmfdb_iso, url_for(".by_ec_label", label=self.lmfdb_iso)),
            ('Minimal quadratic twist ' + data['minq_label'], url_for(".by_ec_label", label=data['minq_label'])),
            ('All twists ', url_for(".rational_elliptic_curves", jinv=self.jinv)),
            ('L-function', url_for("l_functions.l_function_ec_page", label=self.lmfdb_label)),
            ('Symmetric square L-function', url_for("l_functions.l_function_ec_sym_page", power='2', label=self.lmfdb_iso)),
            ('Symmetric 4th power L-function', url_for("l_functions.l_function_ec_sym_page", power='4', label=self.lmfdb_iso)),
            ('Modular form ' + self.lmfdb_iso.replace('.', '.2'), url_for("emf.render_elliptic_modular_forms", level=int(N), weight=2, character=0, label=mod_form_iso))]

        self.downloads = [('Download coeffients of q-expansion', url_for(".download_EC_qexp", label=self.lmfdb_label, limit=100)),
                          ('Download all stored data', url_for(".download_EC_all", label=self.lmfdb_label))]

        self.plot = encode_plot(self.E.plot())
        self.plot_link = '<img src="%s" width="200" height="150"/>' % self.plot
        self.properties = [('Label', self.lmfdb_label),
                           (None, self.plot_link),
                           ('Conductor', '\(%s\)' % data['conductor']),
                           ('Discriminant', '\(%s\)' % data['disc']),
                           ('j-invariant', '%s' % data['j_inv_latex']),
                           ('CM', '%s' % data['CM']),
                           ('Rank', '\(%s\)' % mw['rank']),
                           ('Torsion Structure', '\(%s\)' % mw['tor_struct'])
                           ]

        if self.lmfdb_iso == self.iso:
            self.title = "Elliptic Curve %s" % self.lmfdb_label
        else:
            self.title = "Elliptic Curve %s (Cremona label %s)" % (self.lmfdb_label, self.label)

        self.bread = [('Elliptic Curves ', url_for(".rational_elliptic_curves")), ('isogeny class %s' % self.lmfdb_iso, ' ')]
