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
from mpl_toolkits.mplot3d import Axes3D
from matplotlib.backends import backend_pdf

import os

import numpy as np
import inlining_tree
import py_common
# import fast_analysis

from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis as LDA
from sklearn.metrics import roc_curve
from sklearn.cluster import KMeans
from sklearn.neural_network import MLPClassifier

from feature_loader import Features, Reward, DualReward

B = 5.0


def sgn(x):
    if x < 0:
        return -1
    else:
        return 1


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


def plot_pca(features, labels, title, fname, legend, num_classes):
    pca = PCA(n_components=2)
    pca.fit(features)
    transformed = pca.transform(features)

    fig = plt.figure()
    plt.title(title)
    plt.xlabel("PCA Component 0")
    plt.ylabel("PCA Component 1")
    ls = []

    for cls in range(num_classes):
        ls.append(plt.scatter(transformed[labels == cls, 0], transformed[labels == cls, 1], marker='x', s=4))
    plt.legend(ls, legend)
    plt.tight_layout()
    plt.grid()
    plt.savefig(fname)


def plot_lda(features, labels, title, fname, legend, num_classes=2):
    lda = LDA(n_components=num_classes - 1)
    lda.fit(features, labels)
    pca = PCA(n_components=1)
    pca.fit(features)
    transformed = np.hstack((pca.transform(features), lda.transform(features)))

    fig = plt.figure()

    if num_classes <= 2:
        plt.xlabel("PCA primary component")
        plt.ylabel("LDA component")
        plt.title(title)
        l1 = plt.scatter(transformed[np.array(labels), 0], transformed[np.array(labels), 1], color='r', marker='x', s=4)
        l2 = plt.scatter(transformed[np.logical_not(labels), 0], transformed[np.logical_not(labels), 1], color='b', marker='x', s=4)
        plt.legend([l1, l2], legend)
    else:
        plt.xlabel("LDA component 0")
        plt.ylabel("LDA component 1")
        plt.title(title)
        ls = []
        for cls in range(num_classes):
            ls.append(plt.scatter(transformed[np.array(labels) == cls, 0], transformed[np.array(labels) == cls, 1], marker='x', s=4))
        plt.legend(ls, legend)

    plt.tight_layout()
    plt.grid()
    plt.savefig(fname)


def plot_lda_3_classes(features, labels, title, fname, legend):
    lda = LDA(n_components=2)
    lda.fit(features, labels)
    transformed = lda.transform(features)

    fig = plt.figure()
    plt.xlabel("LDA Component 0")
    plt.ylabel("LDA Component 1")
    plt.title(title)
    labels = np.array(labels)

    if transformed.shape[1] >= 2:
        l1 = plt.scatter(transformed[labels == 0, 0], transformed[labels == 0, 1], color='r', marker='x', s=4)
        l2 = plt.scatter(transformed[labels == 1, 0], transformed[labels == 1, 1], color='g', marker='x', s=4)
        l3 = plt.scatter(transformed[labels == 2, 0], transformed[labels == 2, 1], color='b', marker='x', s=4)
    
        plt.legend([l1, l2, l3], legend)
        plt.tight_layout()
        plt.grid()
        plt.savefig(fname)
    

def compute_heatmap(transformed, side_bins):
    x_min = transformed[:, 0].min()
    x_max = transformed[:, 0].max()
    y_min = transformed[:, 1].min()
    y_max = transformed[:, 1].max()

    x_gap = float(x_max - x_min) / side_bins
    y_gap = float(y_max - y_min) / side_bins
    density = np.zeros((side_bins, side_bins), dtype=np.int)

    for (x, y) in transformed:
        i = int(math.floor((y - y_min) / y_gap))
        j = int(math.floor((x - x_min) / x_gap))
        if i == side_bins:
            i = side_bins - 1
        if j == side_bins:
            j = side_bins - 1
        assert 0 <= i and i < side_bins
        assert 0 <= j and j < side_bins
        i = side_bins - 1 - i  # because image increases from top to bottom, but our axes is bottom to top
        density[i, j] += 1
    return density / float(len(transformed))


def plot_pca_3_classes(features, labels, title, fname, legend):
    pca = PCA(n_components=2)
    pca.fit(features)
    transformed = pca.transform(features)

    fig = plt.figure()
    plt.xlabel("PCA Component 0")
    plt.ylabel("PCA Component 1")
    plt.title(title)
    labels = np.array(labels)

    l1 = plt.scatter(transformed[labels == 0, 0], transformed[labels == 0, 1], color='r', marker='x', s=4)
    l2 = plt.scatter(transformed[labels == 1, 0], transformed[labels == 1, 1], color='g', marker='x', s=4)
    l3 = plt.scatter(transformed[labels == 2, 0], transformed[labels == 2, 1], color='b', marker='x', s=4)

    plt.legend([l1, l2, l3], legend)
    plt.tight_layout()
    plt.grid()
    plt.savefig(fname)


def plot_lda_density(features, labels, title, fname):
    lda = LDA(n_components=2)
    lda.fit(features, labels)
    transformed = lda.transform(features)
    heat_map = compute_heatmap(transformed, side_bins=20)

    plt.figure()
    plt.title(title)
    plt.imshow(heat_map)
    plt.savefig(fname)


def plot_pca_density(features, title, fname):
    pca = PCA(n_components=2)
    pca.fit(features)
    transformed = pca.transform(features)

    side_bins = 20
    heat_map = compute_heatmap(transformed, side_bins=side_bins)

    plt.figure()

    xlabels = []
    ylabels = []

    x_min = transformed[:, 0].min()
    x_max = transformed[:, 0].max()
    x_gap = (x_max - x_min) / 20.0

    y_min = transformed[:, 1].min()
    y_max = transformed[:, 1].max()
    y_gap = (y_max - y_min) / 20.0

    for i in range(20):
        xlabels.append("%.2f" % (x_min + (i + 0.5) * x_gap))
        ylabels.append("%.2f" % (y_min + (18.5 - i) * y_gap))

    ax = plt.gca()
    plt.title(title)
    im = ax.imshow(heat_map)
    cbar = ax.figure.colorbar(im, ax=ax)
    ax.set_xticks(np.arange(side_bins))
    ax.set_yticks(np.arange(side_bins))
    ax.set_xticklabels(xlabels, rotation="60")
    ax.set_yticklabels(ylabels)
    plt.savefig(fname)


import feature_loader

def plot_reward_sparsity(all_features, all_rewards, cluster):

    BOTH_TRIVIAL     = 0
    BOTH_IMPORTANT   = 1
    INLINE_IMPORTANT = 2
    APPLY_IMPORTANT  = 3

    features = []
    labels   = []

    for i, r in enumerate(all_rewards):
        if r is None or r.inline is None or r.no_inline is None:
            continue
        
        inline    = np.log(abs(r.inline.long_term))
        terminate = np.log(abs(r.no_inline))

        if inline > -25 and terminate > -25:
            label = BOTH_IMPORTANT
        elif inline > -25:
            label = INLINE_IMPORTANT
        elif terminate > -25:
            label = APPLY_IMPORTANT
        else:
            label = BOTH_TRIVIAL

        features.append(all_features[i, :])
        labels.append(label)

    features = np.array(features)
    labels   = np.array(labels)

    both_trivial_ratio = np.mean(labels == BOTH_TRIVIAL)
    both_important_ratio = np.mean(labels == BOTH_IMPORTANT)
    inline_important_ratio = np.mean(labels == INLINE_IMPORTANT)
    apply_important_ratio = np.mean(labels == APPLY_IMPORTANT)

    print "- Dataset statistics:"
    print "  - Numberf of points =", len(features)
    print "  - Both important =",   both_important_ratio
    print "  - Both trivial =",     both_trivial_ratio
    print "  - inline important =", inline_important_ratio
    print "  - apply important =",  apply_important_ratio


    # model = MLPClassifier(
    #         solver='lbfgs', alpha=1e-5,
    #         hidden_layer_sizes=(32,),
    #         activation="relu",
    #         random_state=1)
    # model.fit(features, labels)

    from sklearn import svm
    model = svm.SVC()
    model.fit(features, labels)
    svm_labels = model.predict(features)
    
    print "- SVM performance"
    print "  - Both important =",   np.mean(svm_labels == BOTH_IMPORTANT)
    print "  - Both trivial =",     np.mean(svm_labels == BOTH_TRIVIAL)
    print "  - inline important =", np.mean(svm_labels == INLINE_IMPORTANT)
    print "  - apply important =",  np.mean(svm_labels == APPLY_IMPORTANT)
    print "  - sparsity training model score:", model.score(features, labels)

    plot_lda(features, labels,
            title="PCA Scatter Plot of All Features (%d samples)" % len(features),
            legend=["Both trivial", "Both important", "Inline important", "Apply important"],
            num_classes=4,
            fname="report_plots/machine_learning/lasso/v3/triviality_plots-cluster-%d.pdf" % cluster)


def print_stats(all_features, all_rewards, significance_diff, reward_model):

    DOESNT_MATTER     = 0
    INLINE   = 1
    APPLY = 2

    features = []
    labels   = []

    for i, r in enumerate(all_rewards):
        if r is None or r.inline is None or r.no_inline is None:
            continue

        inline = r.inline.long_term
        termination = r.no_inline
        label = None

        if abs(inline - termination) < significance_diff:
            label = DOESNT_MATTER
        elif inline > termination:
            label = INLINE
        elif inline < termination:
            label = APPLY
        else:
            assert False
        assert label is not None

        features.append(all_features[i, :])
        labels.append(label)

    features = np.array(features)
    labels   = np.array(labels)

    dm_ratio     = np.mean(labels == DOESNT_MATTER)
    inline_ratio   = np.mean(labels == INLINE)
    apply_ratio = np.mean(labels == APPLY)

    print reward_model, ",", significance_diff, ",", dm_ratio, ",", inline_ratio, ",", apply_ratio


def do_it(reward_model):
    all_data = feature_loader.read_pickle(feature_version="V3", reward_model=reward_model)

    print >>sys.stderr, "No Information about rewards", len([t for (_, t) in all_data if t is None])
    print >>sys.stderr, "Both inline and termination", len([t for (_, t) in all_data if t is not None and t.inline is not None and t.no_inline is not None])
    print >>sys.stderr, "Just inline", len([t for (_, t) in all_data if t is not None and t.inline is not None and t.no_inline is None])
    print >>sys.stderr, "Just termination", len([t for (_, t) in all_data if t is not None and t.inline is None and t.no_inline is not None])
    print >>sys.stderr, "Total", len(all_data)

    all_numeric_features  = np.zeros((len(all_data), len(all_data[0][0].numeric_features)))
    all_bool_features     = np.zeros((len(all_data), len(all_data[0][0].bool_features)))
    raw_targets           = [b for (_, b) in all_data]

    for i, (features, raw_target) in enumerate(all_data):
        all_numeric_features[i, :] = [a for (_, a) in features.numeric_features]
        all_bool_features[i, :]    = [a for (_, a) in features.bool_features]

    relevant_numeric_features = all_numeric_features[:, (np.std(all_numeric_features,  axis=0) > 0.0001)]
    relevant_bool_features    = all_bool_features[:, (np.mean(all_bool_features,  axis=0) > 0.0001)]

    normalised_numeric_features = (relevant_numeric_features - np.mean(relevant_numeric_features, axis=0))
    normalised_numeric_features = normalised_numeric_features / np.std(relevant_numeric_features, axis=0)

    features = np.concatenate([normalised_numeric_features, relevant_bool_features], axis=1)

    print>>sys.stderr, "Reduced %d numeric features to %d" % (all_numeric_features.shape[1], normalised_numeric_features.shape[1])
    print>>sys.stderr, "Reduced %d boolean features to %d" % (all_bool_features.shape[1],    relevant_bool_features.shape[1])

    for d in [ 0.00001 , 0.00005, 0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05 ]: 
        print_stats(features, list(np.array(raw_targets)), d, reward_model)

    return (features, raw_targets, all_data)


def main():
    matplotlib.rc("text", usetex=True)
    do_it("ridge-general")
    do_it("ridge-star")
    do_it("ridge-hand")



if __name__ == "__main__":
    main()
