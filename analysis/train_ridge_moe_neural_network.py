import argparse
import collections
import sys
import math
import cPickle as pickle
from StringIO import StringIO

import scipy
import scipy.stats

import py_common
import sexpdata
import matplotlib
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from matplotlib.backends import backend_pdf
from sklearn.neural_network import MLPClassifier

import os

import numpy as np
import inlining_tree

from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis as LDA
from sklearn.metrics import roc_curve
from feature_loader import *
import feature_loader

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


def plot_pca(features, labels, title, fname, legend):
    pca = PCA(n_components=2)
    pca.fit(features)
    transformed = pca.transform(features)

    fig = plt.figure()
    plt.title(title)
    plt.xlabel("PCA Component 0")
    plt.ylabel("PCA Component 1")
    l1 = plt.scatter(transformed[np.array(labels), 0], transformed[np.array(labels), 1], color='r', marker='x', s=4)
    l2 = plt.scatter(transformed[np.logical_not(labels), 0], transformed[np.logical_not(labels), 1], color='b', marker='x', s=4)
    plt.legend([l1, l2], legend)
    plt.tight_layout()
    plt.grid()
    plt.savefig(fname)


def plot_lda(features, labels, title, fname, legend):
    lda = LDA(n_components=1)
    lda.fit(features, labels)
    pca = PCA(n_components=1)
    pca.fit(features)
    transformed = np.hstack((pca.transform(features), lda.transform(features)))

    fig = plt.figure()
    plt.xlabel("PCA primary component")
    plt.ylabel("LDA component")
    plt.title(title)
    l1 = plt.scatter(transformed[np.array(labels), 0], transformed[np.array(labels), 1], color='r', marker='x', s=4)
    l2 = plt.scatter(transformed[np.logical_not(labels), 0], transformed[np.logical_not(labels), 1], color='b', marker='x', s=4)
    plt.legend([l1, l2], legend)
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


parser = argparse.ArgumentParser()
parser.add_argument("--decision-model-file", type=str, help="file for decision model")
parser.add_argument("--feature-version", type=str, help="feature version")

feature_version = "v3"
log_dir = os.environ.get("TRAINING_LOG_DIR", None)
print log_dir


def learn_decisions(all_features, all_rewards, all_raw_features, all_exp_names, significance_diff):

    DOESNT_MATTER     = 0
    INLINE   = 1
    APPLY = 2
    assert isinstance(significance_diff, float)

    raw_features = []
    features = []
    labels   = []
    exp_names = []
    rewards   = []

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
        exp_names.append(all_exp_names[i])
        rewards.append(all_rewards[i])
        raw_features.append(all_raw_features[i])

    features = np.array(features)
    labels   = np.array(labels)

    dm_ratio     = np.mean(labels == DOESNT_MATTER)
    inline_ratio   = np.mean(labels == INLINE)
    apply_ratio = np.mean(labels == APPLY)

    print "- Dataset statistics:"
    print "  - Number of points =", len(features)
    print "  - doesnt matter = %d (%.6f)" %   (np.sum(labels == DOESNT_MATTER), dm_ratio)
    print "  - inline = %d (%.6f)"        %   (np.sum(labels == INLINE), inline_ratio)
    print "  - apply  = %d (%.6f)"        %   (np.sum(labels == APPLY), apply_ratio)

    model = MLPClassifier(
            solver='lbfgs', alpha=1e-5,
            hidden_layer_sizes=(32,),
            activation="relu",
            random_state=1)
    model.fit(features, labels)
    predicted_labels = model.predict(features)

    print "- ANN performance"
    print "  - predict dm =",     np.mean(predicted_labels == DOESNT_MATTER)
    print "  - predict inline =", np.mean(predicted_labels == INLINE)
    print "  - predict apply  =",  np.mean(predicted_labels == APPLY)
    print "  - decision model score:", model.score(features, labels)

    tbl = np.zeros((3, 3), dtype=np.int)

    for a in [DOESNT_MATTER, INLINE, APPLY]:
        for b in [DOESNT_MATTER, INLINE, APPLY]:
            tbl[a, b] += sum(np.logical_and((labels == a), (predicted_labels == b)))

    tbl = tbl / float(len(features))
    print tbl

    def label_to_string(l):
        if l == DOESNT_MATTER:
            return "DOESNT_MATTER"
        elif l == INLINE:
            return "INLINE"
        elif l == APPLY:
            return "APPLY"
        assert False

    def dump_classifications(fname, indices):
        d = os.path.dirname(fname)
        if not os.path.exists(d):
            os.makedirs(d)
        with open(fname, "w") as f:
            for i in indices:
                f.write(exp_names[i] + ",")
                f.write("correct = "   + label_to_string(labels[i]) + ",")
                f.write("predicted = " + label_to_string(predicted_labels[i]) + ",")
                if rewards[i].inline is None:
                    f.write(str(None) + ",")
                else:
                    f.write(str(rewards[i].inline.long_term) + ",")
                f.write(str(rewards[i].no_inline) + ",")
                f.write("metadata = "   + str(raw_features[i].metadata))
                f.write("\n")

    if log_dir is not None:
        dump_classifications(fname=os.path.join(log_dir, "correct.log"),   indices=np.where(labels == predicted_labels)[0])
        dump_classifications(fname=os.path.join(log_dir, "incorrect.log"), indices=np.where(labels != predicted_labels)[0])

    return model


def do_it(model_choice, bound, decision_model_file):

    all_data = feature_loader.read_pickle(reward_model=model_choice, feature_version=feature_version)

    print "No Information about rewards", len([t for (_, t) in all_data if t is None])
    print "Both inline and termination", len([t for (_, t) in all_data if t is not None and t.inline is not None and t.no_inline is not None])
    print "Just inline", len([t for (_, t) in all_data if t is not None and t.inline is not None and t.no_inline is None])
    print "Just termination", len([t for (_, t) in all_data if t is not None and t.inline is None and t.no_inline is not None])
    print "Total", len(all_data)

    all_numeric_features  = np.zeros((len(all_data), len(all_data[0][0].numeric_features)))
    all_bool_features     = np.zeros((len(all_data), len(all_data[0][0].bool_features)))
    raw_features          = [a for (a, _) in all_data]
    raw_targets           = [b for (_, b) in all_data]
    numeric_feature_names = [a for (a, _) in all_data[0][0].numeric_features]
    bool_feature_names    = [a for (a, _) in all_data[0][0].bool_features]
    exp_names             = [f.exp_name for (f, _) in all_data]

    for i, (features, raw_target) in enumerate(all_data):
        all_numeric_features[i, :] = [a for (_, a) in features.numeric_features]
        all_bool_features[i, :]    = [a for (_, a) in features.bool_features]

    relevant_numeric_features_indices = np.std(all_numeric_features,  axis=0) > 0.0001
    relevant_bool_features_indices    = np.mean(all_bool_features,  axis=0) > 0.0001

    relevant_numeric_features = all_numeric_features[:, relevant_numeric_features_indices]
    relevant_bool_features    = all_bool_features[:, relevant_bool_features_indices]

    normalised_numeric_features = (relevant_numeric_features - np.mean(relevant_numeric_features, axis=0))
    normalised_numeric_features = normalised_numeric_features / np.std(relevant_numeric_features, axis=0)

    features = np.concatenate([normalised_numeric_features, relevant_bool_features], axis=1)

    decision_model = learn_decisions(
            features, raw_targets,
            all_raw_features=raw_features, all_exp_names=exp_names, significance_diff=bound)
    if decision_model_file is not None:
        with open(decision_model_file, "a") as f:
            codegen_model(
                    f=f,
                    model=decision_model,
                    numeric_feature_names=numeric_feature_names,
                    bool_feature_names=bool_feature_names,
                    numeric_feature_indices=relevant_numeric_features_indices,
                    bool_feature_indices=relevant_bool_features_indices,
                    numeric_feature_means=np.mean(relevant_numeric_features, axis=0),
                    numeric_feature_std=np.std(relevant_numeric_features, axis=0))


import itertools

def main():
    args = parser.parse_args()
    global feature_version
    if args.feature_version is not None:
        feature_version = args.feature_version
    decision_model_file = args.decision_model_file

    bounds = ["0.00005", "0.0001", "0.0005", "0.001", "0.005", "0.05", "0.01"]
    # bounds = ["0.0001"]
    reward_models = ["ridge-hand", "ridge-general", "ridge-star"]

    for i, (model, bound) in enumerate(itertools.product(reward_models, bounds)):
        if i == 0:
            mode = "w"
        else:
            mode = "a"
        with open(decision_model_file, mode) as f:
            f.write("module Expert_%d = struct\n" % i)
        do_it(model, float(bound), decision_model_file)
        with open(decision_model_file, "a") as f:
            f.write("\n")
            f.write("end\n")

    with open(decision_model_file, "a") as f:
        f.write("let feature_version = `%s\n" % feature_version.upper())
        f.write("let model ~int_features ~numeric_features ~bool_features =\n")
        f.write("  Tf_lib.choose_expert [|\n")
        for i in range(len(reward_models) * len(bounds)):
            f.write("    Expert_%d.model ~int_features ~numeric_features ~bool_features;\n" % i)
        f.write("  |]\n")
        f.write(";;")


def float_to_string(x):
    if x < 0:
        return "(-." + str(abs(x)) + ")"
    else:
        return str(x)


def codegen_model(
        f, model,
        numeric_feature_names,
        bool_feature_names,
        numeric_feature_indices,
        bool_feature_indices,
        numeric_feature_means,
        numeric_feature_std):

    f.write("let feature_version = `%s\n" % feature_version.upper())
    f.write("let numeric_features_names    = [| %s |]\n"
                % "; ".join('"' + x + '"' for x in numeric_feature_names))
    f.write("let bool_features_names       = [| %s |]\n"
            % "; ".join('"' + x + '"' for x in bool_feature_names))
    f.write("\n\n")
    if isinstance(model, LogisticRegression):
        weights = model.coef_
        intercept = model.intercept_

        assert weights.shape[0] == 1
        assert intercept.shape == (1,)

        f.write("let weights = [| " + "; ".join(float_to_string(x) for x in weights[0]) + " |]\n")
        f.write("let intercept = %s\n" % float_to_string(intercept[0]))
        f.write("let numeric_features_means   = [| %s |]\n" % "; ".join(float_to_string(x) for x in numeric_feature_means))

        f.write("let numeric_features_std    = [| %s |]\n" % "; ".join(str(x) for x in numeric_feature_std))
        f.write("let numeric_features_indices = [| %s |]\n" % "; ".join(str(x) for x in np.where(numeric_feature_indices)[0]))
        f.write("let bool_features_indices    = [| %s |]\n" % "; ".join(str(x) for x in np.where(bool_feature_indices)[0]))
        f.write("let model ~int_features ~numeric_features ~bool_features =\n")
        f.write("  Tf_lib.check_names ~names:numeric_features_names numeric_features;")
        f.write("  Tf_lib.check_names ~names:bool_features_names bool_features;")
        f.write("  let features = Tf_lib.features_to_t\n")
        f.write("    ~int_features ~numeric_features ~bool_features\n")
        f.write("    ~numeric_features_indices ~bool_features_indices\n")
        f.write("    ~numeric_features_means ~numeric_features_std\n")
        f.write("  in\n")
        f.write("  let output = \n")
        f.write("    Tf_lib.matmul (Tf_lib.Vec weights) features\n")
        f.write("    |> Tf_lib.add (Tf_lib.Scalar intercept)\n")
        f.write("    |> Tf_lib.unpack_scalar_exn\n")
        f.write("    |> Tf_lib.sigmoid\n")
        f.write("  in\n")
        f.write("  Tf_lib.Vec [| 1.0 -. output; output; |]\n")
        f.write(";;")

    elif isinstance(model, MLPClassifier):
        weights = model.coefs_
        intercepts = model.intercepts_
        activation = model.activation

        for i, (weight, intercept) in enumerate(zip(weights, intercepts)):
            f.write("let weights_%d = [|\n" % i)
            for weight_vector in weight:
                f.write("  [|" + "; ".join(float_to_string(x) for x in weight_vector) + " |];\n")
            f.write("  |]\n")
            f.write(";;\n")
            f.write("let intercept_%d = [| %s |]\n" % (i, "; ".join(float_to_string(x) for x in intercept)))

        f.write("let numeric_features_means   = [| %s |]\n" % "; ".join(float_to_string(x) for x in numeric_feature_means))
        f.write("let numeric_features_std    = [| %s |]\n" % "; ".join(str(x) for x in numeric_feature_std))
        f.write("let numeric_features_indices = [| %s |]\n" % "; ".join(str(x) for x in np.where(numeric_feature_indices)[0]))
        f.write("let bool_features_indices    = [| %s |]\n" % "; ".join(str(x) for x in np.where(bool_feature_indices)[0]))
        f.write("\n")
        f.write("let model ~int_features ~numeric_features ~bool_features =\n")
        f.write("  Tf_lib.check_names ~names:numeric_features_names numeric_features;\n")
        f.write("  Tf_lib.check_names ~names:bool_features_names bool_features;\n")
        f.write("  let features = Tf_lib.features_to_t\n")
        f.write("    ~int_features ~numeric_features ~bool_features\n")
        f.write("    ~numeric_features_indices ~bool_features_indices\n")
        f.write("    ~numeric_features_means ~numeric_features_std\n")
        f.write("  in\n")
        f.write("  let output = \n")
        f.write("    features\n")

        for i in range(len(weights)):
            f.write("    |> (fun v -> Tf_lib.matmul v (Tf_lib.Mat weights_%d))\n" % i)
            f.write("    |> Tf_lib.add (Tf_lib.Vec intercept_%d)\n" % i)

            if i == len(weights) - 1:
                f.write("    |> Tf_lib.%s\n" % model.out_activation_)
            else:
                f.write("    |> Tf_lib.%s\n" % activation)

        f.write("  in\n")
        f.write("  output\n")
        f.write(";;")

    else:
        assert False

    print "Test cases outputs:"
    for i in range(10):
        f.write("(* Test case %d *)\n" % i)
        feature_loader.codegen_single_test_case(
                f=f,
                model=model,
                num_numeric_features=len(numeric_feature_names),
                num_bool_features=len(bool_feature_names),
                numeric_feature_indices=numeric_feature_indices,
                bool_feature_indices=bool_feature_indices,
                numeric_feature_means=numeric_feature_means,
                numeric_feature_std=numeric_feature_std)
    f.write("let () = Format.printf \"Passed all test cases!\\n\"\n")


if __name__ == "__main__":
    main()
