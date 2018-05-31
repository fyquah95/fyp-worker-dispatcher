import argparse
import collections
import sys
import math
import cPickle as pickle

import scipy
import scipy.stats

import sexpdata
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.backends import backend_pdf

import os

import numpy as np
import inlining_tree

from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_curve

Features = collections.namedtuple("Features", ["int_features", "bool_features", "numeric_features"])
Reward = collections.namedtuple("Reward", ["inline", "no_inline"])
DualReward = collections.namedtuple("DualReward", ["long_term", "immediate"])

option_of_sexp = inlining_tree.option_of_sexp

B = 5.0

def sgn(x):
    if x < 0:
        return -1
    else:
        return 1


def parse(sexp):
    def parse_dual_reward(sexp):
        m = inlining_tree.sexp_to_map(sexp)
        return DualReward(
                long_term=float(m["long_term"]),
                immediate=float(m["immediate"]))

    def parse_reward(sexp):
        m = inlining_tree.sexp_to_map(sexp)
        inline = option_of_sexp(m["inline"], f=parse_dual_reward)
        no_inline = option_of_sexp(m["no_inline"], f=float)
        return Reward(inline=inline, no_inline=no_inline)

    def parse_feature_list(sexp, f):
        return {inlining_tree.unpack_atom(k) : f(inlining_tree.unpack_atom(v)) for k, v in sexp}

    def parse_bool(s):
        if s == "true":
            return True
        elif s == "false":
            return False
        else:
            assert False


    def parse_features(sexp):
        m = inlining_tree.sexp_to_map(sexp)
        int_features = parse_feature_list(m["int_features"], f=int)
        numeric_features = parse_feature_list(m["numeric_features"], f=float)
        bool_features = parse_feature_list(m["bool_features"], f=parse_bool)
        return Features(int_features=int_features, bool_features=bool_features, numeric_features=numeric_features)

    assert isinstance(sexp, list)
    return [(parse_features(a), option_of_sexp(b, f=parse_reward)) for (a, b) in sexp]

def fmap(x, f):
    if x is not None:
        return f(x)
    else:
        return None


# xs.append(sgn(a) * (1 + math.log(abs(a))))

def plot_best_fit(xs, ys):
    slope, intercept, r_value, p_value, std_err = scipy.stats.linregress(
            xs, ys)
    eqn = "%.4f x + %.4f" % (slope, intercept)
    diff = (max(xs) - min(xs)) / 20.0
    xs = [(min(xs) + diff * i) for i in range(0, 21)]
    ys = [slope * x + intercept for x in xs]
    plt.plot(xs, ys, "r", label=eqn)
    plt.legend()

def plot_immediate_and_long_term_correlation(all_data):
    xs = []
    ys = []
    for d in all_data:
        if d.inline is not None:
            xs.append(d.inline.immediate)
            ys.append(d.inline.long_term)

    plt.title("Immediate Reward vs Long Term Reward")
    plt.scatter(xs, ys, marker="x")
    plt.xlabel("Immediate Reward")
    plt.ylabel("Long Term Reward")
    plt.grid()
    plt.scatter(xs, ys, marker="x")
    plot_best_fit(xs, ys)


def plot_immediate_and_no_inline_correlation(all_data):
    xs = []
    ys = []
    for d in all_data:
        if d.inline is not None and d.no_inline is not None:
            xs.append(d.inline.immediate)
            ys.append(d.no_inline)

    plt.title("Immediate vs Termination Reward")
    plt.scatter(xs, ys, marker="x")
    plt.xlabel("Immediate Reward")
    plt.ylabel("Termination Reward")
    plt.grid()
    plot_best_fit(xs, ys)


def plot_immediate_reward_histrogram(all_data):
    xs = []

    for d in all_data:
        if d.inline is not None and d.no_inline is not None:
            xs.append(d.inline.long_term)

    plt.title("Immediate Reward Histogram (%d samples)" % len(xs))
    plt.hist(xs, bins=300)
    plt.xlabel("Long Term Reward")
    plt.ylabel("Normalised Frequency")
    plt.grid()


def plot_long_term_and_no_inline_correlation(all_data):
    xs = []
    ys = []
    for d in all_data:
        if d.inline is not None and d.no_inline is not None:
            xs.append(d.inline.long_term)
            ys.append(d.no_inline)

    plt.title("Long Term vs Termination Reward")
    plt.scatter(xs, ys, marker="x")
    plt.xlabel("Long Term Reward")
    plt.ylabel("Termination Reward")
    plt.grid()
    plot_best_fit(xs, ys)


def plot_immediate_reward_log_histrogram(all_data):

    def f(x):
        return sgn(x) * math.log(1 + abs(x))

    xs = []

    for d in all_data:
        if d.inline is not None and d.no_inline is not None:
            xs.append(f(d.inline.immediate))

    plt.title("Imm. Reward Log-Space Histogram")
    plt.hist(xs, normalised=True, bins=50)
    plt.xlabel("Immediate Reward")
    plt.ylabel("Normalised Frequency")
    plt.grid()


def remove_annomalises(all_data):
    ret = []
    for d in all_data:
        if (fmap(d.inline, f=lambda x : abs(x.immediate) > B)
                or fmap(d.inline, f=lambda x : abs(x.long_term) >B)
                or fmap(d.no_inline, f=lambda x : abs(x) > B)):
            pass
        else:
            ret.append(d)
    return ret


parser = argparse.ArgumentParser()
parser.add_argument("name", type=str, help="output file name")
parser.add_argument("--pdf", type=str, help="pdf output file name (optional)")


def main():
    # args = parser.parse_args()
    # with open(args.name, "r") as f:
    #     all_data = parse(sexpdata.load(f))

    # with open("./report_plots/machine_learning/v1_data.pickle", "wb") as f:
    #     pickle.dump(all_data, f)

    minimal = float(sys.argv[1])
    print "Minimal:", minimal
    with open("./report_plots/machine_learning/v1_data.pickle", "rb") as f:
        all_data = pickle.load(f)

    all_numeric_features  = np.zeros((len(all_data), len(all_data[0][0].numeric_features)))
    all_bool_features     = np.zeros((len(all_data), len(all_data[0][0].bool_features)))
    raw_targets           = [b for (_, b) in all_data]

    for i, (features, raw_target) in enumerate(all_data):
        all_numeric_features[i, :] = [a for (_, a) in features.numeric_features.iteritems()]
        all_bool_features[i, :]    = [a for (_, a) in features.bool_features.iteritems()]

    relevant_numeric_features = all_numeric_features[:, (np.std(all_numeric_features,  axis=0) > 0.0001)]
    relevant_bool_features    = all_bool_features[:, (np.mean(all_bool_features,  axis=0) > 0.0001)]

    normalised_numeric_features = (relevant_numeric_features - np.mean(relevant_numeric_features, axis=0))
    normalised_numeric_features = normalised_numeric_features / np.std(relevant_numeric_features, axis=0)

    features = np.concatenate([normalised_numeric_features, relevant_bool_features], axis=1)
    labels = []
    for t in raw_targets:
        labels.append(
                t is not None
                and t.inline is not None
                and t.no_inline is not None
                and (abs(t.inline.long_term > minimal) or abs(t.no_inline) > minimal)
        )
    familiarity_labels = np.array(labels)

    print "familiarity label mean:", np.mean(labels)
    model = LogisticRegression()
    model.fit(features, labels)
    print "familiarity model score:", model.score(features, labels)
    fpr, tpr, thresholds = roc_curve(labels, model.predict_proba(features)[:, 1])

    plt.subplot(1, 2, 1)
    plt.title("Familiarity Model (%s samples)" % len(features))
    plt.plot(fpr, tpr)
    plt.plot([0, 1], [0, 1],'r--')
    plt.grid()
    plt.xlabel("False Positive Rate")
    plt.ylabel("True Positive Rate")

    decision_features = []
    decision_labels = []
    for i in range(len(labels)):
        if labels[i]:
            decision_features.append(features[i, :])
            decision_labels.append(raw_targets[i].inline.long_term > raw_targets[i].no_inline)
    decision_features = np.array(decision_features)
    decision_labels = np.array(decision_labels)
    print "decision training examples:", len(decision_labels)
    print "decision label mean:", np.mean(decision_labels)
    decision_model = LogisticRegression()
    decision_model.fit(decision_features, decision_labels)
    print "decision model score:", decision_model.score(decision_features, decision_labels)

    fpr, tpr, thresholds = roc_curve(decision_labels, model.predict_proba(decision_features)[:, 1])
    plt.subplot(1, 2, 2)
    plt.plot(fpr, tpr)
    plt.plot([0, 1], [0, 1],'r--')
    plt.title("Decision Model (%s samples)" % len(decision_features))
    plt.grid()
    plt.xlabel("False Positive Rate")
    plt.ylabel("True Positive Rate")

    # plt.show()
    plt.tight_layout()
    plt.savefig(fname=os.path.join("roc_plots", ("%.4f" % minimal)) + ".pdf", format='pdf')

    return

    # pca = PCA(n_components=2, svd_solver='full')
    # pca.fit(features)
    # transformed = pca.transform(features)

    # fig = plt.figure()
    # plt.title("PCA")

    # plt.scatter(transformed[np.logical_not(labels), 0], transformed[np.logical_not(labels), 1], color='b', marker='x', s=4)
    # plt.scatter(transformed[labels, 0], transformed[labels, 1], color='r', marker='x', s=4)

    # plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    # plt.grid()
    # plt.show()
    # plt.savefig(fname=os.path.join(PLOT_DIR, filename) + ".pdf", format='pdf')

if __name__ == "__main__":
    main()
