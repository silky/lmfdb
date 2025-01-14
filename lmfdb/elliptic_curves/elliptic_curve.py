# -*- coding: utf-8 -*-
import re

from pymongo import ASCENDING, DESCENDING
import lmfdb.base
from lmfdb.base import app
from flask import Flask, session, g, render_template, url_for, request, redirect, make_response
import tempfile
import os

from lmfdb.utils import ajax_more, image_src, web_latex, to_dict, parse_range2, web_latex_split_on_pm, comma, clean_input
from lmfdb.number_fields.number_field import parse_list
from lmfdb.elliptic_curves import ec_page, ec_logger
from lmfdb.elliptic_curves.ec_stats import get_stats
from lmfdb.elliptic_curves.isog_class import ECisog_class
from lmfdb.elliptic_curves.web_ec import WebEC, parse_list, parse_points

import sage.all
from sage.all import ZZ, QQ, EllipticCurve, latex, matrix, srange
q = ZZ['x'].gen()

#########################
#   Database connection
#########################
ecdb = None

def db_ec():
    global ecdb
    if ecdb is None:
        ecdb = lmfdb.base.getDBConnection().elliptic_curves.curves
    return ecdb


#########################
#   Utility functions
#########################

cremona_label_regex = re.compile(r'(\d+)([a-z]+)(\d*)')
lmfdb_label_regex = re.compile(r'(\d+)\.([a-z]+)(\d*)')
sw_label_regex = re.compile(r'sw(\d+)(\.)(\d+)(\.*)(\d*)')

LIST_RE = re.compile(r'^(\d+|(\d+-(\d+)?))(,(\d+|(\d+-(\d+)?)))*$')
TORS_RE = re.compile(r'^\[\]|\[\d+(,\d+)*\]$')
QQ_RE = re.compile(r'^-?\d+(/\d+)?$')
LIST_POSINT_RE = re.compile(r'^(\d+)(,\d+)*$')

def format_ainvs(ainvs):
    """
    The a-invariants are stored as a list of strings because mongodb doesn't
    have big-ints, and all strings are stored as unicode. However, printing
    a list of unicodes looks like [u'0', u'1', ...]
    """
    return [ZZ(a) for a in ainvs]



def cmp_label(lab1, lab2):
    from sage.databases.cremona import parse_cremona_label, class_to_int
    a, b, c = parse_cremona_label(lab1)
    id1 = int(a), class_to_int(b), int(c)
    a, b, c = parse_cremona_label(lab2)
    id2 = int(a), class_to_int(b), int(c)
    return cmp(id1, id2)

#########################
#    Top level
#########################


@app.route("/EC")
def EC_redirect():
    return redirect(url_for("ec.rational_elliptic_curves", **request.args))


@app.route("/EllipticCurve")
def EC_toplevel():
    return redirect(url_for("ec.rational_elliptic_curves", **request.args))

#########################
#  Search/navigate
#########################

@ec_page.route("/")
def rational_elliptic_curves(err_args=None):
    if err_args is None:
        if len(request.args) != 0:
            return elliptic_curve_search(**request.args)
        else:
            err_args = {}
            for field in ['conductor', 'jinv', 'torsion', 'rank', 'sha_an', 'optimal', 'torsion_structure', 'msg']:
                err_args[field] = ''
            err_args['count'] = '100'
    counts = get_stats().counts()

    conductor_list_endpoints = [1, 100, 1000, 10000, 100000, counts['max_N'] + 1]
    conductor_list = ["%s-%s" % (start, end - 1) for start, end in zip(conductor_list_endpoints[:-1],
                                                                       conductor_list_endpoints[1:])]
    rank_list = range(counts['max_rank'] + 1)
    torsion_list = range(1,11) + [12, 16]
    info = {
        'rank_list': rank_list,
        'torsion_list': torsion_list,
        'conductor_list': conductor_list,
        'counts': counts,
        'stats_url': url_for(".statistics")
    }
    credit = 'John Cremona and Andrew Sutherland'
    t = 'Elliptic curves over $\Q$'
    bread = [('Elliptic Curves', url_for("ecnf.index")), ('$\Q$', ' ')]
    return render_template("browse_search.html", info=info, credit=credit, title=t, bread=bread, **err_args)

@ec_page.route("/stats")
def statistics():
    info = {
        'counts': get_stats().counts(),
        'stats': get_stats().stats(),
    }
    credit = 'John Cremona'
    t = 'Elliptic curves over $\Q$: statistics'
    bread = [('Elliptic Curves', url_for("ecnf.index")),
             ('$\Q$', url_for(".rational_elliptic_curves")),
             ('statistics', ' ')]
    return render_template("statistics.html", info=info, credit=credit, title=t, bread=bread)


@ec_page.route("/<int:conductor>")
def by_conductor(conductor):
    return elliptic_curve_search(conductor=conductor, **request.args)


def elliptic_curve_jump_error(label, args, wellformed_label=False, cremona_label=False):
    err_args = {}
    for field in ['conductor', 'torsion', 'rank', 'sha_an', 'optimal', 'torsion_structure']:
        err_args[field] = args.get(field, '')
    err_args['count'] = args.get('count', '100')
    if wellformed_label:
        err_args['err_msg'] = "No curve or isogeny class in database with label %s" % label
    elif cremona_label:
        err_args['err_msg'] = "To search for a Cremona label use 'Cremona:%s'" % label
    else:
        err_args['err_msg'] = "Could not understand label %s" % label
    return rational_elliptic_curves(err_args)


def elliptic_curve_search(**args):
    info = to_dict(args)
    query = {}
    bread = [('Elliptic Curves', url_for(".rational_elliptic_curves")),
             ('Search Results', '.')]
    if 'jump' in args:
        label = info.get('label', '').replace(" ", "")
        m = lmfdb_label_regex.match(label)
        if m:
            try:
                return by_ec_label(label)
            except ValueError:
                return elliptic_curve_jump_error(label, info, wellformed_label=True)
        elif label.startswith("Cremona:"):
            label = label[8:]
            m = cremona_label_regex.match(label)
            if m:
                try:
                    return by_ec_label(label)
                except ValueError:
                    return elliptic_curve_jump_error(label, info, wellformed_label=True)
        elif cremona_label_regex.match(label):
            return elliptic_curve_jump_error(label, info, cremona_label=True)
        elif label:
            # Try to parse a string like [1,0,3,2,4]
            lab = re.sub(r'\s','',label)
            lab = re.sub(r'^\[','',lab)
            lab = re.sub(r']$','',lab)
            try:
                labvec = lab.split(',')
                labvec = [QQ(str(z)) for z in labvec] # Rationals allowed
                E = EllipticCurve(labvec)
                ainvs = [str(c) for c in E.minimal_model().ainvs()]
                data = db_ec().find_one({'ainvs': ainvs})
                if data is None:
                    return elliptic_curve_jump_error(label, info)
                return by_ec_label(data['lmfdb_label'])
            except (ValueError, ArithmeticError):
                return elliptic_curve_jump_error(label, info)
        else:
            query['label'] = ''

    if info.get('jinv'):
        j = clean_input(info['jinv'])
        j = j.replace('+', '')
        if not QQ_RE.match(j):
            info['err'] = 'Error parsing input for the j-invariant.  It needs to be a rational number.'
            return search_input_error(info, bread)
        query['jinv'] = j

    for field in ['conductor', 'torsion', 'rank', 'sha_an']:
        if info.get(field):
            info[field] = clean_input(info[field])
            ran = info[field]
            ran = ran.replace('..', '-').replace(' ', '')
            if not LIST_RE.match(ran):
                names = {'conductor': 'conductor', 'torsion': 'torsion order', 'rank':
                         'rank', 'sha_an': 'analytic order of &#1064;'}
                info['err'] = 'Error parsing input for the %s.  It needs to be an integer (such as 5), a range of integers (such as 2-10 or 2..10), or a comma-separated list of these (such as 2,3,8 or 3-5, 7, 8-11).' % names[field]
                return search_input_error(info, bread)
            # Past input check
            tmp = parse_range2(ran, field)
            # work around syntax for $or
            # we have to foil out multiple or conditions
            if tmp[0] == '$or' and '$or' in query:
                newors = []
                for y in tmp[1]:
                    oldors = [dict.copy(x) for x in query['$or']]
                    for x in oldors:
                        x.update(y)
                    newors.extend(oldors)
                tmp[1] = newors
            if field=='sha_an': # database sha_an values are not all exact!
                query[tmp[0]] = { '$gt': tmp[1]-0.1, '$lt': tmp[1]+0.1}
            else:
                query[tmp[0]] = tmp[1]

    if 'optimal' in info and info['optimal'] == 'on':
        # fails on 990h3
        query['number'] = 1

    if 'torsion_structure' in info and info['torsion_structure']:
        info['torsion_structure'] = clean_input(info['torsion_structure'])
        if not TORS_RE.match(info['torsion_structure']):
            info['err'] = 'Error parsing input for the torsion structure.  It needs to be one or more integers in square brackets, such as [6], [2,2], or [2,4].  Moreover, each integer should be bigger than 1, and each divides the next.'
            return search_input_error(info, bread)
        query['torsion_structure'] = [str(a) for a in parse_list(info['torsion_structure'])]

    if info.get('surj_primes'):
        info['surj_primes'] = clean_input(info['surj_primes'])
        format_ok = LIST_POSINT_RE.match(info['surj_primes'])
        if format_ok:
            surj_primes = [int(p) for p in info['surj_primes'].split(',')]
            format_ok = all([ZZ(p).is_prime(proof=False) for p in surj_primes])
        if format_ok:
            query['non-surjective_primes'] = {"$nin": surj_primes}
        else:
            info['err'] = 'Error parsing input for surjective primes.  It needs to be a prime (such as 5), or a comma-separated list of primes (such as 2,3,11).'
            return search_input_error(info, bread)

    if info.get('nonsurj_primes'):
        info['nonsurj_primes'] = clean_input(info['nonsurj_primes'])
        format_ok = LIST_POSINT_RE.match(info['nonsurj_primes'])
        if format_ok:
            nonsurj_primes = [int(p) for p in info['nonsurj_primes'].split(',')]
            format_ok = all([ZZ(p).is_prime(proof=False) for p in nonsurj_primes])
        if format_ok:
            if info['surj_quantifier'] == 'exactly':
                nonsurj_primes.sort()
                query['non-surjective_primes'] = nonsurj_primes
            else:
                if 'non-surjective_primes' in query:
                    query['non-surjective_primes'] = { "$nin": surj_primes, "$all": nonsurj_primes }
                else:
                    query['non-surjective_primes'] = { "$all": nonsurj_primes }
        else:
            info['err'] = 'Error parsing input for nonsurjective primes.  It needs to be a prime (such as 5), or a comma-separated list of primes (such as 2,3,11).'
            return search_input_error(info, bread)

    info['query'] = query

    count_default = 100
    if info.get('count'):
        try:
            count = int(info['count'])
        except:
            count = count_default
    else:
        count = count_default
    info['count'] = count

    start_default = 0
    if info.get('start'):
        try:
            start = int(info['start'])
            if(start < 0):
                start += (1 - (start + 1) / count) * count
        except:
            start = start_default
    else:
        start = start_default

    cursor = db_ec().find(query)
    nres = cursor.count()
    if(start >= nres):
        start -= (1 + (start - nres) / count) * count
    if(start < 0):
        start = 0
    res = cursor.sort([('conductor', ASCENDING), ('lmfdb_iso', ASCENDING), ('lmfdb_number', ASCENDING)
                       ]).skip(start).limit(count)
    info['curves'] = res
    info['format_ainvs'] = format_ainvs
    info['number'] = nres
    info['start'] = start
    if nres == 1:
        info['report'] = 'unique match'
    else:
        if nres > count or start != 0:
            info['report'] = 'displaying matches %s-%s of %s' % (start + 1, min(nres, start + count), nres)
        else:
            info['report'] = 'displaying all %s matches' % nres
    credit = 'John Cremona'
    if 'non-surjective_primes' in query:
        credit += 'and Andrew Sutherland'
    t = 'Elliptic Curves search results'
    return render_template("search_results.html", info=info, credit=credit, bread=bread, title=t)


def search_input_error(info, bread):
    return render_template("search_results.html", info=info, title='Elliptic Curve Search Input Error', bread=bread)

##########################
#  Specific curve pages
##########################

# The following function determines whether the given label is in
# LMFDB or Cremona format, and also whether it is a curve label or an
# isogeny class label, and calls the appropriate function

@ec_page.route("/<label>")
def by_ec_label(label):
    ec_logger.debug(label)
    try:
        N, iso, number = lmfdb_label_regex.match(label).groups()
    except AttributeError:
        ec_logger.info("%s not a valid lmfdb label, trying cremona")
        try:
            N, iso, number = cremona_label_regex.match(label).groups()
        except AttributeError:
            return elliptic_curve_jump_error(label, {})

        # We permanently redirect to the lmfdb label
        if number:
            data = db_ec().find_one({'label': label})
            if data is None:
                return elliptic_curve_jump_error(label, {})
            ec_logger.debug(url_for(".by_ec_label", label=data['lmfdb_label']))
            return redirect(url_for(".by_ec_label", label=data['lmfdb_label']), 301)
        else:
            data = db_ec().find_one({'iso': label})
            if data is None:
                return elliptic_curve_jump_error(label, {})
            ec_logger.debug(url_for(".by_ec_label", label=data['lmfdb_label']))
            return redirect(url_for(".by_ec_label", label=data['lmfdb_iso']), 301)
    if number:
        return render_curve_webpage_by_label(label=label)
    else:
        return render_isogeny_class(str(N) + '.' + iso)


def render_isogeny_class(iso_class):
    credit = 'John Cremona'
    class_data = ECisog_class.by_label(iso_class)
    if class_data == "Invalid label":
        return elliptic_curve_jump_error(iso_class, {}, wellformed_label=False)
    if class_data == "Class not found":
        return elliptic_curve_jump_error(iso_class, {}, wellformed_label=True)
    return render_template("iso_class.html",
                           properties2=class_data.properties,
                           info=class_data,
                           bread=class_data.bread,
                           credit=credit,
                           title=class_data.title,
                           friends=class_data.friends,
                           downloads=class_data.downloads)

@ec_page.route("/modular_form_display/<label>/<number>")
def modular_form_display(label, number):
    try:
        number = int(number)
    except:
        number = 10
    if number < 10:
        number = 10
    # if number > 100000:
    #     number = 20
    # if number > 50000:
    #     return "OK, I give up."
    # if number > 20000:
    #     return "This incident will be reported to the appropriate authorities."
    # if number > 9600:
    #     return "You have been banned from this website."
    # if number > 4800:
    #     return "Seriously."
    # if number > 2400:
    #     return "I mean it."
    # if number > 1200:
    #     return "Please stop poking me."
    if number > 1000:
        number = 1000
    data = db_ec().find_one({'lmfdb_label': label})
    if data is None:
        return elliptic_curve_jump_error(label, {})
    ainvs = [int(a) for a in data['ainvs']]
    E = EllipticCurve(ainvs)
    modform = E.q_eigenform(number)
    modform_string = web_latex_split_on_pm(modform)
    return modform_string

# This function is now redundant since we store plots as
# base64-encoded pngs.
@ec_page.route("/plot/<label>")
def plot_ec(label):
    C = lmfdb.base.getDBConnection()
    data = C.elliptic_curves.curves.find_one({'lmfdb_label': label})
    if data is None:
        return elliptic_curve_jump_error(label, {})
    ainvs = [int(a) for a in data['ainvs']]
    E = EllipticCurve(ainvs)
    P = E.plot()
    _, filename = tempfile.mkstemp('.png')
    P.save(filename)
    data = open(filename).read()
    os.unlink(filename)
    response = make_response(data)
    response.headers['Content-type'] = 'image/png'
    return response


def render_curve_webpage_by_label(label):
    credit = 'John Cremona and Andrew Sutherland'
    data = WebEC.by_label(label)
    if data is "Invalid label":
        return elliptic_curve_jump_error(label, {}, wellformed_label=False)
    if data is "Curve not found":
        return elliptic_curve_jump_error(label, {}, wellformed_label=True)

    return render_template("curve.html",
                           properties2=data.properties,
                           credit=credit,
                           data=data,
                           bread=data.bread, title=data.title,
                           friends=data.friends,
                           downloads=data.downloads)

@ec_page.route("/padic_data")
def padic_data():
    info = {}
    label = request.args['label']
    p = int(request.args['p'])
    info['p'] = p
    print "label = %s; p = %s" % (label,p)
    N, iso, number = lmfdb_label_regex.match(label).groups()
    # print N, iso, number
    if request.args['rank'] == '0':
        info['reg'] = 1
    elif number == '1':
        C = lmfdb.base.getDBConnection()
        data = C.elliptic_curves.curves.find_one({'lmfdb_iso': N + '.' + iso})
        data = C.elliptic_curves.padic_db.find_one({'lmfdb_iso': N + '.' + iso, 'p': p})
        info['data'] = data
        if data is None:
            info['reg'] = 'no data'
        else:
            val = int(data['val'])
            aprec = data['prec']
            reg = sage.all.Qp(p, aprec)(int(data['unit']), aprec - val) << val
            info['reg'] = web_latex(reg)
    else:
        info['reg'] = "no data"
    return render_template("padic_data.html", info=info)


@ec_page.route("/download_qexp/<label>/<limit>")
def download_EC_qexp(label, limit):
    ec_logger.debug(label)
    CDB = lmfdb.base.getDBConnection().elliptic_curves.curves
    N, iso, number = lmfdb_label_regex.match(label).groups()
    if number:
        data = CDB.find_one({'lmfdb_label': label})
    else:
        data = CDB.find_one({'lmfdb_iso': label})
    ainvs = data['ainvs']
    ec_logger.debug(ainvs)
    E = EllipticCurve([int(a) for a in ainvs])
    response = make_response(','.join(str(an) for an in E.anlist(int(limit), python_ints=True)))
    response.headers['Content-type'] = 'text/plain'
    return response


@ec_page.route("/download_all/<label>")
def download_EC_all(label):
    CDB = lmfdb.base.getDBConnection().elliptic_curves.curves
    N, iso, number = lmfdb_label_regex.match(label).groups()
    if number:
        data = CDB.find_one({'lmfdb_label': label})
        if data is None:
            return elliptic_curve_jump_error(label, {})
        data_list = [data]
    else:
        data_list = sorted(list(CDB.find({'lmfdb_iso': label})), key=lambda E: E['number'])
        if len(data_list) == 0:
            return elliptic_curve_jump_error(label, {})

    # titles of all entries of curves
    dump_data = []
    titles = [str(c) for c in data_list[0]]
    titles = [t for t in titles if t[0] != '_']
    titles.sort()
    dump_data.append(titles)
    for data in data_list:
        data1 = []
        for t in titles:
            d = data[t]
            if t == 'ainvs':
                data1.append(format_ainvs(d))
            elif t in ['torsion_generators', 'torsion_structure']:
                data1.append([eval(g) for g in d])
            elif t == 'x-coordinates_of_integral_points':
                data1.append(parse_list(d))
            elif t == 'gens':
                data1.append(parse_points(d))
            elif t in ['iso', 'label', 'lmfdb_iso', 'lmfdb_label']:
                data1.append(str(d))
            else:
                data1.append(d)
        dump_data.append(data1)
    response = make_response('\n'.join(str(an) for an in dump_data))
    response.headers['Content-type'] = 'text/plain'
    return response

#@ec_page.route("/download_Rub_data")
# def download_Rub_data():
#    import gridfs
#    label=(request.args.get('label'))
#    type=(request.args.get('type'))
#    C = base.getDBConnection()
#    fs = gridfs.GridFS(C.elliptic_curves,'isogeny' )
#    isogeny=C.ellcurves.isogeny.files
#    filename=isogeny.find_one({'label':str(label),'type':str(type)})['filename']
#    d= fs.get_last_version(filename)
#    response = make_response(d.readline())
#    response.headers['Content-type'] = 'text/plain'
#    return response
