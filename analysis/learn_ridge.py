import argparse
import collections
import sympy
import logging
import os
import cPickle as pickle
import sys

import numpy as np
from scipy import linalg
import sklearn.linear_model

import inlining_tree
import learn_problem



parser = argparse.ArgumentParser(description="formulate the problem")
parser.add_argument("directory", type=str, help="experiment dir")
parser.add_argument("--decay-factor", type=float, default=None, required=True)
parser.add_argument("--ridge-factor", type=float, default=None, required=True)
parser.add_argument("--benefit-function", type=str, default=None, required=True)
parser.add_argument("--skip-normalisation", action="store_true")


HyperParametersBase = collections.namedtuple("HyperParametersBase",
        ["decay_factor", "ridge_factor", "benefit_function"])

class HyperParameters(HyperParametersBase):

    def directory_name(self):
        return "decay-%f-ridge-%f-benefit-%s" % (
                self.decay_factor, self.ridge_factor, self.benefit_function)

def is_very_small(x):
    return x < 1e-10


def compute_vector_space_of_solutions(R, rhs):
    logging.info("Computing vector space of solutions")

    w = list(sympy.symbols("w:" + str(num_features)))
    assert len(w) == num_features

    for rev_i in range(num_features):
        i = num_features - 1 - rev_i

        if all(is_very_small(x) for x in R[i, i:num_features]):
            continue

        left_most_index = None
        left_most = None
        acc = 0

        for j in range(i, num_features):
            if not is_very_small(R[i][j]):
                if left_most is None:
                    left_most_index = j
                    left_most = R[i][j]
                else:
                    acc += -w[j] * R[i][j]
        assert left_most is not None
        w[left_most_index] = acc / left_most
        print("w[%d] = %s" % (left_most_index, str(w[left_most_index])))

    # w is now the symbol of all things
    # The solution is in the form of
    #   soln = bias + a1 * v1 + a2 * v2 + ... + an * vn
    # We are primarily interested in the components in the bias
    # term
    print(w)


def _sigmoid_speedup_over_mean(execution_times, problem):
    baseline = np.mean(problem.execution_times)
    return learn_problem.sigmoid(
            -20 * (execution_times - baseline) / baseline)

def _sigmoid_speedup_over_baseline(execution_times, problem):
    baseline = get_baseline_execution_time(problem)
    return learn_problem.sigmoid(
            -20 * (execution_times - baseline) / baseline)


def _linear_speedup_over_mean(execution_times, problem):
    baseline = np.mean(problem.execution_times)
    return -(execution_times - baseline) / baseline


def _linear_speedup_over_baseline(execution_times, problem):
    baseline = get_baseline_execution_time(problem)
    return -(execution_times - baseline) / baseline


def _log_speedup_over_mean(execution_times, problem):
    baseline = np.mean(problem.execution_times)
    return -np.log(execution_times / baseline)


def _log_speedup_over_baseline(execution_times, problem):
    baseline = get_baseline_execution_time(problem)
    return -np.log(execution_times / baseline)


def _tanh_speedup_over_mean(execution_times, problem):
    baseline = np.mean(problem.execution_times)
    return np.tanh(
            -10 * (execution_times - baseline) / baseline)


def _tanh_speedup_over_baseline(execution_times, problem):
    baseline = get_baseline_execution_time(problem)
    return np.tanh(
            -10 * (execution_times - baseline) / baseline)


def construct_benefit_from_exec_time(kind, problem):
    execution_times = problem.execution_times
    dispatch = {
            "sigmoid_speedup_over_mean": _sigmoid_speedup_over_mean,
            "linear_speedup_over_mean":  _linear_speedup_over_mean,
            "log_speedup_over_mean":     _log_speedup_over_mean,
            "tanh_speedup_over_mean":     _tanh_speedup_over_mean,

            "sigmoid_speedup_over_baseline": _sigmoid_speedup_over_baseline,
            "linear_speedup_over_baseline":  _linear_speedup_over_baseline,
            "log_speedup_over_baseline":     _log_speedup_over_baseline,
            "tanh_speedup_over_baseline": _tanh_speedup_over_baseline,
    }
    return dispatch[kind](execution_times, problem=problem)


def geometric_mean(arr):
    acc = 1.0
    for a in arr:
        acc *= a
    return acc ** (1.0 / len(arr))


def get_baseline_execution_time(problem):
    times = []
    for i, dir in enumerate(problem.execution_directories):
        if "initial" in dir:
            times.append(problem.execution_times[i])
    assert len(times) > 0
    logging.info("Geometric  mean initial time = %f" % geometric_mean(times))
    logging.info("Arithmetic mean initial time = %f" % np.mean(times))
    logging.info("Aithmetic mean time over everything = %f" % np.mean(problem.execution_times))
    return geometric_mean(times)


def run(args):
    logging.getLogger().setLevel(logging.INFO)
    args = parser.parse_args(args)
    problem_directory = args.directory
    logging.info("Loading problem definition ...")
    hyperparams = HyperParameters(
            decay_factor=args.decay_factor,
            ridge_factor=args.ridge_factor,
            benefit_function=args.benefit_function)
    if args.skip_normalisation:
        experiment_name = "ridge"
    else:
        assert False
    exp_directory = os.path.join(
            problem_directory, experiment_name, hyperparams.directory_name())

    if os.path.exists(os.path.join(exp_directory, "contributions.npy")):
        logging.info("A solution already exist for %s/%s/%s! Pass --force to recompute"
                % (problem_directory, experiment_name, hyperparams.directory_name()))
        return

    problem = inlining_tree.Problem.load(problem_directory)
    execution_times = problem.execution_times

    if not os.path.exists(exp_directory):
        os.makedirs(exp_directory)

    with open(os.path.join(exp_directory, "hyperparams.pkl"), "wb") as f:
        pickle.dump(hyperparams, f)

    normalise_with_num_children = not args.skip_normalisation
    problem_matrices = learn_problem.construct_problem_matrices(
            problem, hyperparams, normalise_with_num_children)

    target_benefit = construct_benefit_from_exec_time(
            args.benefit_function, problem)
    num_features = problem_matrices.benefit_relations.shape[1]

    logging.info("Computing analytical solution for %s." % (experiment_name))
    logging.info("  decay factor = %.6f" % (args.decay_factor))
    logging.info("  ridge factor (aka l2 reg) = %.6f" % (args.ridge_factor))
    logging.info("  benefit function = %s" % args.benefit_function)

    lambda_ = args.ridge_factor
    A = problem_matrices.benefit_relations

    model = sklearn.linear_model.Ridge(alpha=lambda_, fit_intercept=False)
    model.fit(A, target_benefit)
    w = model.coef_
    assert w.shape == (A.shape[1],)

    # old impl: this uses matrix multiplication, which uses cubic memory.
    # w = np.linalg.solve(
    #         np.matmul(A.T , A) + (lambda_ * np.identity(A.shape[1])),
    #         np.matmul(A.T, target_benefit)
    # )

    logging.info("Found analytical solution, saving to %s!" % exp_directory)
    with open(os.path.join(exp_directory, "contributions.npy"), "wb") as f:
        np.save(f, w)

def main():
    run(sys.argv[1:])

if __name__ == "__main__":
    main()
