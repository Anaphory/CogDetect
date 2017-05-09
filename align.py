#!/usr/bin/env python3

"""Align words based on stepwise EM alignments with PMI scores."""

from collections import defaultdict, Counter
import itertools as it
import sys
import igraph, utils
import numpy as np
import random, codecs
import infomapcog.clustering as clust
import infomapcog.distances as distances

import argparse

import csv
import pickle

import lingpy
import infomapcog.ipa2asjp as ipa2asjp

import newick


def clean_word(w):
    w = w.replace("-","")
    w = w.replace(" ", "")
    w = w.replace("%","")
    w = w.replace("~","")
    w = w.replace("*","")
    w = w.replace("$","")
    w = w.replace("\"","")
    w = w.replace("|","")
    w = w.replace(".","")
    w = w.replace("+","")
    w = w.replace("·","")
    w = w.replace("?","")
    w = w.replace("’","")
    w = w.replace("]","")
    w = w.replace("[","")
    w = w.replace("=","")
    w = w.replace("_","")
    w = w.replace("<","")
    w = w.replace(">","")
    w = w.replace("‐","")
    w = w.replace("ᶢ","")
    w = w.replace("C","c")
    w = w.replace("K","k")
    w = w.replace("L","l")
    w = w.replace("W","w")
    w = w.replace("T","t")
    w = w.replace('dʒ͡', 'd͡ʒ')
    w = w.replace('ʤ', 'd͡ʒ')
    w = w.replace('Ɂ', 'Ɂ')
    return w


def read_data_ielex_type(datafile, char_list=set(),
                         cogids_are_cross_semantically_unique=False,
                         data='ASJP'):
    """Read an IELex style TSV file."""
    line_id = 0
    data_dict = defaultdict(lambda : defaultdict())
    cogid_dict = {}
    words_dict = defaultdict(lambda : defaultdict(list))
    langs_list = []

    # Ignore the header line of the data file.
    datafile.readline()
    for line in datafile:
        line = line.strip()
        arr = line.split("\t")
        lang = arr[0]

        concept = arr[2]
        cogid = arr[6]
        cogid = cogid.replace("-","")
        cogid = cogid.replace("?","")
        if data=='ASJP':
            asjp_word = clean_word(arr[5].split(",")[0])
        else:
            raise NotImplementedError

        for ch in asjp_word:
            if ch not in char_list:
                char_list.add(ch)

        if len(asjp_word) < 1:
            continue

        data_dict[concept][line_id,lang] = asjp_word
        cogid_dict.setdefault(cogid
                              if cogids_are_cross_semantically_unique
                              else (cogid, concept), set()).add(
            (lang, concept, asjp_word))
        words_dict[concept][lang].append(asjp_word)
        if lang not in langs_list:
            langs_list.append(lang)
        line_id += 1

    return (data_dict,
            list(cogid_dict.values()),
            words_dict,
            langs_list,
            char_list)


def read_data_cldf(datafile, sep="\t", char_list=set(),
                   cogids_are_cross_semantically_unique=True,
                   data='ASJP'):
    """Read a CLDF file in TSV or CSV format."""
    reader = csv.DictReader(
        datafile,
        dialect='excel' if sep == ',' else 'excel-tab')
    langs = set()
    data_dict = defaultdict(lambda : defaultdict())
    cogid_dict = defaultdict(lambda : defaultdict())
    words_dict = defaultdict(lambda : defaultdict(list))
    for line, row in enumerate(reader):
        lang = row["Language ID"]
        langs.add(lang)

        if data == 'ASJP':
            try:
                asjp_word = clean_word(row["ASJP"])
            except KeyError:
                asjp_word = ipa2asjp.ipa2asjp(row["IPA"])
        elif data == 'IPA':
            asjp_word = tuple(lingpy.ipa2tokens(row["IPA"], merge_vowels=False))
        else:
            asjp_word = row[data]

        if not asjp_word:
            continue

        for ch in asjp_word:
            if ch not in char_list:
                char_list.add(ch)

        concept = row["Feature ID"]
        cogid = row["Cognate Class"]

        data_dict[concept][line, lang] = asjp_word
        cogid_dict.setdefault(cogid
                              if cogids_are_cross_semantically_unique
                              else (cogid, concept), set()).add(
            (lang, concept, asjp_word))
        words_dict[concept].setdefault(lang, []).append(asjp_word)

    return (data_dict,
            list(cogid_dict.values()),
            words_dict,
            list(langs),
            char_list)


def read_data_lingpy(datafile, sep="\t", char_list=set(),
                     cogids_are_cross_semantically_unique=True,
                     data='ASJP'):
    """Read a Lingpy file in TSV or CSV format."""
    reader = csv.DictReader(
        datafile,
        dialect='excel' if sep == ',' else 'excel-tab')
    langs = set()
    data_dict = defaultdict(defaultdict)
    cogid_dict = {}
    words_dict = defaultdict(lambda : defaultdict(list))
    for line, row in enumerate(reader):
        lang = row.get("DOCULECT_ID", row["DOCULECT"])
        langs.add(lang)

        if data == 'ASJP':
            try:
                asjp_word = clean_word(row["ASJP"])
            except KeyError:
                asjp_word = ipa2asjp.ipa2asjp(row["IPA"])
        elif data == 'IPA':
            asjp_word = tuple(ipa2asjp.tokenize_word_reversibly(
                clean_word(row["IPA"])))
        else:
            asjp_word = row[data]

        if not asjp_word:
            continue

        for ch in asjp_word:
            if ch not in char_list:
                char_list.add(ch)

        concept = row["CONCEPT"]
        cogid = row["COGID"]

        data_dict[concept][line, lang] = asjp_word
        cogid_dict.setdefault(cogid
                              if cogids_are_cross_semantically_unique
                              else (cogid, concept), set()).add(
            (lang, concept, asjp_word))
        words_dict[concept].setdefault(lang, []).append(asjp_word)

    return (data_dict,
            list(cogid_dict.values()),
            words_dict,
            list(langs),
            char_list)


def calc_pmi(alignments, scores=None):
    """Calculate a pointwise mutual information dictionary from alignments.

    Given a sequence of pairwaise alignments and their relative
    weights, calculate the logarithmic pairwise mutual information
    encoded for the character pairs in the alignments.

    """
    if scores is None:
        scores = it.cycle([1])

    sound_dict = defaultdict(float)
    relative_align_freq = 0.0
    relative_sound_freq = 0.0
    count_dict = defaultdict(float)

    for alignment, score in zip(alignments, scores):
        for a1, a2 in alignment:
            count_dict[a1, a2] += 1.0*score
            count_dict[a2, a1] += 1.0*score
            sound_dict[a1] += 2.0*score
            sound_dict[a2] += 2.0*score

    log_weight = 2 * np.log(sum(list(
        sound_dict.values()))) - np.log(sum(list(
            count_dict.values())))

    for (c1, c2) in count_dict.keys():
        m = count_dict[c1, c2]
        assert m > 0

        num = np.log(m)
        denom = np.log(sound_dict[c1]) + np.log(sound_dict[c2])
        val = num - denom + log_weight
        count_dict[c1, c2] = val

    return count_dict


class OnlinePMIIterator:
    """A persistent object able to run multiple alignment iterations."""

    def __init__(self, margin=1, alpha=0.75, char_list=[]):
        self.margin = margin
        self.alpha = alpha
        self.n_updates = 0
        self.pmidict = {}

    def align_pairs(self, word_pairs, local=False):
        """Align a list of word pairs, removing those that align badly."""
        algn_list, scores = [], []
        n_zero = 0
        for w in range(len(word_pairs)-1, -1, -1):
            ((c1, l1, w1), (c2, l2, w2)) = wl[w]
            s, alg = distances.needleman_wunsch(
                w1, w2, gop=None, gep=-1.75, lodict=self.pmidict,
                local=local)
            margin = max(
                sum([self.pmidict.get((x, ''), -1.75) for x in w1]) + len(w1),
                sum([self.pmidict.get(('', x), -1.75) for x in w2]) + len(w2))
            print(w1, w2, s, margin)
            if self.margin * s < margin:
                n_zero += 1
                word_pairs.pop(w)
                continue
            algn_list.append(alg)
            scores.append(s)
        self.update_pmi_dict(algn_list, scores=None)
        return algn_list, n_zero

    def update_pmi_dict(self, algn_list, scores=None):
        eta = (self.n_updates + 2) ** (-self.alpha)
        for k, v in calc_pmi(algn_list, scores).items():
            pmidict_val = self.pmidict.get(k, 0.0)
            self.pmidict[k] = (eta * v) + ((1.0 - eta) * pmidict_val)


class MinPairDict(dict):
    def __getitem__(self, key):
        key1, key2 = key
        max_val = -float('inf')
        if type(key1) != tuple:
            key1 = [key1]
        if type(key2) != tuple:
            key2 = [key2]
        for k1 in key1:
            for k2 in key2:
                v = dict.__getitem__(self, (k1, k2))
                if v > max_val:
                    max_val = v
            return max_val
    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


def multi_align(
        similarity_sets, guide_tree, pairwise=distances.needleman_wunsch,
        **kwargs):
    """Align multiple sequences according to a give guide tree."""
    languages = {leaf.name: leaf for leaf in guide_tree.get_leaves()}
    for s, similarityset in enumerate(similarity_sets):
        for (language, concept, form) in similarityset:
            try:
                leaf = languages[language]
            except KeyError:
                continue
            try:
                leaf.forms
            except AttributeError:
                leaf.forms = {}
            leaf.forms.setdefault(s, []).append(
                    ((language,), (concept,), tuple((x,) for x in form)))

    for node in guide_tree.walk('postorder'):
        print(node.name)
        try:
            entries_by_group = node.forms
        except AttributeError:
            entries_by_group = {}
            for child in node.descendants:
                try:
                    for group, alignment in child.alignment.items():
                        entries_by_group.setdefault(group, []).append(alignment)
                except AttributeError:
                    pass
        aligned_groups = {}
        for group in entries_by_group:
            forms = entries_by_group[group]

            already_aligned = None
            for (new_languages, new_concepts, new_alignment) in forms:
                if not already_aligned:
                    languages = new_languages
                    concepts = new_concepts
                    already_aligned = new_alignment
                else:
                    gap1 = ('',) * len(languages)
                    gap2 = ('',) * len(new_alignment[0])

                    languages += new_languages
                    concepts += new_concepts

                    print("Aligning:")
                    print(already_aligned)
                    print(new_alignment)
                    s, combined_alignment = pairwise(
                        already_aligned, new_alignment, **kwargs)
                    print(combined_alignment)
                    already_aligned = tuple(
                        (x if x else gap1) + (y if y else gap2)
                        for x, y in combined_alignment)
                    print(already_aligned)
            aligned_groups[group] = languages, concepts, already_aligned
        node.alignment = aligned_groups
    return node.alignment


readers = {
    "ielex": read_data_ielex_type,
    "cldf": read_data_cldf,
    "lingpy": read_data_lingpy,
    }

if __name__ == "__main__":

    # TODO:
    # - Add a ML based estimation of distance or a JC model for distance
    #   between two sequences
    # - Separate clustering code.
    # - Add doculect distance as regularization

    parser = argparse.ArgumentParser(
        description=__doc__)
    parser.add_argument(
        "--seed",
        type=int,
        default=1234,
        help="Random seed")
    parser.add_argument(
        "--max-iter",
        type=int,
        default=15,
        help="Maximum number of iterations")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.001,
        help="TODO tolerance")
    parser.add_argument(
        "--infomap-threshold",
        type=float,
        default=0.5,
        help="Threshold for the INFOMAP algorithm")
    parser.add_argument(
        "--max-batch",
        type=int,
        default=256,
        help="Maximum number of word pairs to align in one updating step")
    parser.add_argument(
        "--margin",
        type=float,
        default=1.0,
        help="TODO margin")
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.75,
        # Citation: Liang & Klein: Online EM for unsupervised models.
        help="""Stepsize reduction power α. Any 0.5 < α ≤ 1 is valid. The smaller
        the α, the larger the updates, and the more quickly old
        sufficient statistics decay. This can lead to swift progress
        but also generates instability.""")
    parser.add_argument(
        "--method",
        default='infomap')
    parser.add_argument(
        "--guide-tree",
        type=argparse.FileType("r"),
        help="""A Newick file containing a single guide tree to combine multiple
        alignments. (Separate guide trees for different families are
        not supported yet.)""")
    parser.add_argument(
        "data",
        type=argparse.FileType("r"),
        help="IELex-style data file to read")
    parser.add_argument(
        "--transcription",
        default='ASJP',
        help="The transcription convention (IPA, ASJP, …) used in the data file")
    parser.add_argument(
        "--pmidict",
        type=argparse.FileType("wb"),
        help="Write PMI dictionary to this (pickle) file.")
    parser.add_argument(
        "--reader",
        choices=list(readers.keys()),
        default="ielex",
        help="Data file format")

    args = parser.parse_args()

    if args.alpha <= 0.5 or args.alpha > 1:
        raise ValueError("ALPHA must be in (0.5, 1].")

    random.seed(args.seed)
    tolerance = args.tolerance
    infomap_threshold = args.infomap_threshold
    alpha = args.alpha

    data_dict, cogid_dict, words_dict, langs_list, char_list = (
        readers[args.reader](args.data, data=args.transcription))
    print("Character list:", char_list, "({:d})".format(len(char_list)))

    word_pairs = []
    for concept in data_dict:
        print(concept, end=", ")
        words = data_dict[concept].items()
        for ((i1, l1), w1), ((i2, l2), w2) in it.combinations(words, r=2):
            if distances.normalized_leventsthein(w1, w2) <= 0.5:
                word_pairs.append(((concept, l1, w1), (concept, l2, w2)))
    print()

    print("Size of initial list ", len(word_pairs))

    online = OnlinePMIIterator(
        alpha=args.alpha,
        margin=0.79)

    # We might miss some cognate pairs because they look dissimilar,
    # but they are actually connected strongly when taking regular
    # sound correspondences into account. So we run the iterator once
    # on the reduced word list to obtain an initial PMI score
    # matrix. Then we split the word list again into chunks that are
    # similar according to *that* scorer.

    print("Calculating PMIs from very similar words.")
    random.shuffle(word_pairs)
    idx = 0
    while idx < len(word_pairs):
        print(len(word_pairs), idx)
        wl = word_pairs[idx:idx+args.max_batch]
        algn_list, n_zero = online.align_pairs(wl)
        word_pairs[idx:idx+args.max_batch] = wl
        idx += len(wl)

    print(Counter(online.pmidict).most_common())

    word_pairs = []
    for concept in data_dict:
        words = data_dict[concept].items()
        for ((i1, l1), w1), ((i2, l2), w2) in it.combinations(words, r=2):
            s, alg = distances.needleman_wunsch(
                w1, w2, gop=None, gep=-1.75, lodict=online.pmidict)
            if s >= args.margin + args.margin * len(alg):
                word_pairs.append(((concept, l1, w1), (concept, l2, w2)))

    for n_iter in range(1, args.max_iter):
        random.shuffle(word_pairs)
        print("Iteration", n_iter)
        idx = 0
        while idx < len(word_pairs):
            wl = word_pairs[idx:idx+args.max_batch]
            algn_list, n_zero = online.align_pairs(wl)
            word_pairs[idx:idx+args.max_batch] = wl
            idx += len(wl)
        for (c1, l1, w1), (c2, l2, w2) in word_pairs:
            if distances.normalized_leventsthein(
                    w1, w2) > 0.5:
                print(''.join(w1), ''.join(w2))

        print("Non zero examples ", len(word_pairs), len(word_pairs)-n_zero,
              " number of updates ", online.n_updates)
        print(Counter(online.pmidict).most_common())

    if args.pmidict:
        pickle.dump(online.pmidict, args.pmidict)

    nodes = []
    G = igraph.Graph()
    for concept, words in data_dict.items():
        for (i, l), w in words.items():
            G.add_vertex(len(nodes))
            nodes.append((concept, l, w))

    G.add_edges([(nodes.index(i), nodes.index(j))
                 for i, j in word_pairs])

    groups = []
    for cluster in G.clusters(igraph.WEAK):
        subgraph = G.subgraph(cluster)
        if len(subgraph.vs) <= 2:
            comps = subgraph

        elif args.method == 'infomap':
            comps = G.community_infomap(vertex_weights=None)

        elif args.method == 'labelprop':
            comps = G.community_label_propagation(initial=None, fixed=None)

        elif args.method == 'ebet':
            dg = G.community_edge_betweenness()
            oc = dg.optimal_count
            comps = False
            while oc <= len(G.vs):
                try:
                    comps = dg.as_clustering(dg.optimal_count)
                    break
                except:
                    oc += 1
            if not comps:
                print('Failed...')
                comps = list(range(len(G.sv)))
                input()
        elif args.method == 'multilevel':
            comps = G.community_multilevel(return_levels=False)
        elif args.method == 'spinglass':
            comps = G.community_spinglass()
        else:
            raise ValueError("Not a valid clustering algorithm")

        groups = groups + [[nodes[v] for v in sims]
                           for sims in comps.clusters()]

    for group in groups:
        print(len(group))
        print(group)
        
    if args.guide_tree:
        tree = newick.load(args.guide_tree)[0]
        for group, (languages, concepts, alignment) in multi_align(
                groups, tree,
                lodict=MinPairDict(online.pmidict),
                gop=None, gep=-1.75).items():
            if len(languages) > 1:
                print(languages)
                print(concepts)
                print(alignment)
