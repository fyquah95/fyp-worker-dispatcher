import argparse
import logging
import os
import pickle
import sys
import StringIO

import numpy as np
from numpy import linalg
import sexpdata
import matplotlib.pyplot as plt

import constants
import inlining_constants
import inlining_overrides
import inlining_tree
import learn_problem
import learn_linear_general_reward
from learn_lasso_with_alpha import HyperParameters
import sexp_utils


parser = argparse.ArgumentParser(description="formulate the problem")
parser.add_argument("--experiment-dir", type=str, help="experiment dir")
parser.add_argument("--problem-dir", type=str, help="problem dir")
parser.add_argument("--output", type=str, help="output", default="/dev/stdout")
parser.add_argument("--skip-normalisation", action="store_true")
group1 = parser.add_mutually_exclusive_group(required=True)
group1.add_argument(
        "--opt-info", action="store_true",
        help="dump about how much the model has done something")
group1.add_argument(
        "--optimal-decision", action="store_true",
        help="dump about how much the model has done something")
group1.add_argument(
        "--inspect-run", type=int, default=None,
        help="dump about how much the model has done something")
group1.add_argument(
        "--inspect-rewards", action="store_true",
        help="Inspect values learnt for nodes in the inlining tree.")
group1.add_argument(
        "--dump-rewards", action="store_true",
        help="Inspect values learnt for nodes in the inlining tree.")
group1.add_argument(
        "--plot-rewards", action="store_true",
        help="Inspect values learnt for nodes in the inlining tree.")


def choose_left(a, b):
    if a is None:
        return False
    elif b is None:
        return True
    else:
        return a > b


def neg_inf_if_none(a):
    if a is None:
        return -np.inf
    else:
        return a


def prettify(n):
    if n == np.inf:
        return "   INF"
    elif n == -np.inf:
        return "  -INF"
    elif n > 0:
        return " %.3f" % n
    else:
        return "%.3f" % n


def format_trace_item(trace_item):
    if trace_item[0] == "declaration":
        return "{" + trace_item[1].closure_origin.id() + "}"
    elif trace_item[0] == "function":
        return "<" + trace_item[1].path[-1].id() + ">"
    else:
        assert False


def print_tree(tree, depth=0):
    print "(%s | %s)\t%s%s" % (
            prettify(neg_inf_if_none(tree.value[0])),
            prettify(neg_inf_if_none(tree.value[1])),
            str("--" * depth),
            str("<ROOT>" if len(tree.name.trace) == 0 else " " + str(format_trace_item(tree.name.trace[-1]))))

    for child in tree.children:
        print_tree(child, depth=depth + 1)


def build_optimal_tree(
        tree,
        hyperparams,
        normalise_with_num_children,
        record_path_long_term_rewards):
    """
    args:
        tree<name: path, value: (float * float)>
        hyperparams
        normalise_with_num_children: boolean
    returns:
        (tree<name: [ Inlined | Apply | Decl ], value: [ Function_call |  Closure_origin ]>, float)
    """
    assert isinstance(tree, inlining_tree.Node)

    optimal_children = []

    if len(tree.children) > 0:
        acc = []
        value_acc = 0.0

        for child in tree.children:
            acc.append(build_optimal_tree(
                child, hyperparams, normalise_with_num_children,
                record_path_long_term_rewards=record_path_long_term_rewards))
            value_acc += acc[-1][1]
            optimal_children.append(acc[-1][0])

        children_value = hyperparams.decay_factor * value_acc
        if normalise_with_num_children:
            children_value = (children_value / float(len(tree.children)))
    else:
        children_value = 0.0

    assert tree.value[0] is not None or tree.value[1] is not None
    if not tree.name.is_apply_node():
        assert tree.value[0] is not None
        assert tree.value[1] is None

    lhs_value = neg_inf_if_none(tree.value[0]) + children_value
    rhs_value = neg_inf_if_none(tree.value[1])

    assert isinstance(tree.name, inlining_tree.Absolute_path)
    if tree.value[0] is None:
        record_path_long_term_rewards[tree.name] = None
    else:
        record_path_long_term_rewards[tree.name] = lhs_value

    if len(tree.name.trace) == 0:
        tree = inlining_tree.Node(
                name="Top_level", value=None, children=optimal_children)
        return (tree, lhs_value)

    if not tree.name.is_apply_node():
        func = tree.name.trace[-1][1]
        assert isinstance(func, inlining_tree.Function_metadata)
        value = func
        tree = inlining_tree.Node(
                name="Decl", value=value, children=optimal_children)
        return (tree, lhs_value)

    local_path = tree.name.trace[-1][1]
    func = tree.name.trace[-1][2]
    assert isinstance(func, inlining_tree.Function_metadata)
    assert isinstance(local_path, inlining_tree.Local_path)
    value = inlining_tree.Function_call(function=func, path=local_path)
    if lhs_value > rhs_value:
        tree = inlining_tree.Node(
                name="Inlined", value=value, children=optimal_children)
        return (tree, lhs_value)
    else:
        tree = inlining_tree.Node(name="Apply", value=value, children=[])
        return (tree, rhs_value)


def project_benefit_tree(
        root, hyperparams, id_to_tree_path, adjacency_list, contributions, mask, indent=0, normalise_with_num_children=False, visited=None):
    space = " " * indent
    assert not (mask[2 * root] and mask[2 * root + 1])
    visited.add(root)
    if mask[2 * root]:
        logging.info(
                "%sLooking into %d(%s)" % (space, root, id_to_tree_path[root]))
        base = contributions[2 * root]
        acc = 0.0
        num_children = len(adjacency_list[root])
        for child in adjacency_list[root]:
            assert isinstance(child, int)
            child_value = project_benefit_tree(
                    child, hyperparams, id_to_tree_path, adjacency_list,
                    contributions, mask,
                    indent=indent+1,
                    normalise_with_num_children=normalise_with_num_children,
                    visited=visited)
            if child_value is None:
                num_children -= 1
            else:
                acc += child_value
        if num_children:
            if normalise_with_num_children:
                norm_factor = float(num_children)
            else:
                norm_factor = 1.0
            return base + (hyperparams.decay_factor * acc / norm_factor)
        else:
            return base
    elif mask[2 * root + 1]:
        logging.info(
                "%sTerminating at %d(%s)" % (space, root, id_to_tree_path[root]))
        return contributions[2 * root + 1]
    else:
        logging.info(
                "%sFailed to project benefit at %d(%s)" % (space, root, id_to_tree_path[root]))
        return None


def run(argv):
    logging.getLogger().setLevel(logging.INFO)
    args = parser.parse_args(argv)
    hyperparams_path = os.path.join(args.experiment_dir, "hyperparams.pkl")

    problem = inlining_tree.Problem.load(args.problem_dir)
    with open(hyperparams_path, "rb") as f:
        hyperparams = pickle.load(f)

    normalise_with_num_children = not args.skip_normalisation
    assert not normalise_with_num_children
    problem_matrices = learn_problem.construct_problem_matrices(
            problem, hyperparams,
            normalise_with_num_children=normalise_with_num_children)
    target_benefit = learn_linear_general_reward.construct_benefit_from_exec_time(
            hyperparams.benefit_function, problem)
    num_nodes = problem_matrices.participation_mask.shape[1] / 2
    participation_count = np.sum(problem_matrices.participation_mask, axis=0)
    w = np.load(
            os.path.join(args.experiment_dir, "contributions.npy"))

    def fill_node_values(node):
        node_id = node.name
        if participation_count[node_id * 2] > 0:
            lhs = w[node_id * 2]
        else:
            lhs = None
        if participation_count[node_id * 2 + 1] > 0:
            rhs = w[node_id * 2 + 1]
        else:
            rhs = None
        return (node.name, (lhs, rhs))

    def rename_id_to_path(node):
        return (id_to_tree_path[node.name], node.value)

    if args.opt_info:
        A = problem_matrices.benefit_relations
        squared_errors = np.power(target_benefit - np.matmul(A, w), 2)
        mse = np.mean(squared_errors)
        projected_benefits = np.matmul(A, w)

        print "Mean squared error:", mse
        print "Mimimum projected:", min(projected_benefits)
        print "Maximum projected:", max(projected_benefits)
        print "Mimimum error:", min(squared_errors)
        print "Maximum error:", max(squared_errors)

        obtained = np.matmul(A, w)
        target = target_benefit

    elif args.dump_rewards:
        A = problem_matrices.benefit_relations
        adjacency_list = inlining_tree.adjacency_list_from_edge_lists(
                num_nodes=num_nodes,
                edge_lists=problem.edges_lists)
        tree_path_to_ids = problem.properties.tree_path_to_ids
        id_to_tree_path = {v: k for k, v in tree_path_to_ids.iteritems()}
        root = tree_path_to_ids[inlining_tree.Absolute_path([])]
        tree = inlining_tree.build_from_adjacency_list(
                [None] * num_nodes, root, adjacency_list)
        tree = tree.map(f=fill_node_values) 
        tree = tree.map(f=rename_id_to_path)
        record_path_long_term_rewards = {}
        (optimal_tree, value) = build_optimal_tree(
                tree, hyperparams, normalise_with_num_children,
                record_path_long_term_rewards=record_path_long_term_rewards)

        arr = []
        for i in range(num_nodes):
            if participation_count[2 * i] > 0:
                long_term = record_path_long_term_rewards[id_to_tree_path[i]]
                inline_reward = [[
                        ["immediate", w[2 * i]],
                        ["long_term", long_term]
                ]]
            else:
                inline_reward = []

            if participation_count[2 * i + 1] > 0:
                no_inline_reward = w[2 * i + 1]
            else:
                no_inline_reward = None

            no_inline_reward = inlining_tree.sexp_of_option(
                    no_inline_reward, f=str)

            arr.append([
                ["path", id_to_tree_path[i].to_sexp()],
                ["inline_reward", inline_reward],
                ["no_inline_reward", no_inline_reward],
            ])
        print sexpdata.dumps(arr)

    elif args.inspect_rewards:
        A = problem_matrices.benefit_relations
        adjacency_list = inlining_tree.adjacency_list_from_edge_lists(
                num_nodes=num_nodes,
                edge_lists=problem.edges_lists)
        tree_path_to_ids = problem.properties.tree_path_to_ids
        id_to_tree_path = {v: k for k, v in tree_path_to_ids.iteritems()}
        root = tree_path_to_ids[inlining_tree.Absolute_path([])]
        tree = inlining_tree.build_from_adjacency_list(
                [None] * num_nodes, root, adjacency_list)
        tree = tree.map(f=fill_node_values) 
        tree = tree.map(f=rename_id_to_path)
        print_tree(tree)

    elif args.optimal_decision:

        tree_path_to_ids = problem.properties.tree_path_to_ids
        id_to_tree_path = {v: k for k, v in tree_path_to_ids.iteritems()}
        adjacency_list = inlining_tree.adjacency_list_from_edge_lists(
                num_nodes=num_nodes,
                edge_lists=problem.edges_lists)
        root = tree_path_to_ids[inlining_tree.Absolute_path([])]
        tree = inlining_tree.build_from_adjacency_list(
                [None] * num_nodes, root, adjacency_list)
        tree = tree.map(f=fill_node_values)
        tree = tree.map(f=rename_id_to_path)
        (optimal_tree, value) = build_optimal_tree(
                tree, hyperparams, normalise_with_num_children,
                record_path_long_term_rewards={})
        sexp_optimal_tree = inlining_tree.sexp_of_top_level(
                optimal_tree)
        logging.info("Optimal decision has a value of %f" % value)
        sexp_buffer = StringIO.StringIO()
        sexp_utils.dump_without_quotes(sexp_buffer, sexp_optimal_tree)
        with open(args.output, "w") as f:
            f.write(sexp_buffer.getvalue())

    elif args.inspect_run is not None:
        index = args.inspect_run

        adjacency_list = inlining_tree.adjacency_list_from_edge_lists(
                num_nodes=num_nodes,
                edge_lists=problem.edges_lists)
        tree_path_to_ids = problem.properties.tree_path_to_ids
        id_to_tree_path = {v: k for k, v in tree_path_to_ids.iteritems()}

        A = problem_matrices.benefit_relations
        target_benefit = target_benefit[index]
        projected_benefit = np.matmul(A, w)[index]
        participation_mask = problem_matrices.participation_mask[index, :]
        assert participation_mask.shape == (num_nodes * 2,)
        visited = set()
        projected_benefit_with_dfs = project_benefit_tree(
                root=tree_path_to_ids[inlining_tree.Absolute_path([])],
                hyperparams=hyperparams,
                adjacency_list=adjacency_list,
                id_to_tree_path=id_to_tree_path,
                contributions=w,
                mask=participation_mask,
                normalise_with_num_children=normalise_with_num_children,
                visited=visited)

        visited_count = 0
        for i in range(num_nodes):
            if participation_mask[i * 2] or participation_mask[i * 2 + 1]:
                visited_count += 1
                if i not in visited:
                    print "DID NOT VISIT", i, id_to_tree_path[i]

        print "--- Information on run %d ---" % index
        print "Execution directory =", problem.execution_directories[index]
        print "Target benefit =", target_benefit
        print "Execution time =", problem.execution_times[index]
        print "Projected benefit (with matmul) =", projected_benefit
        print "Projected benefit (with DFS) =", projected_benefit_with_dfs
        print "Number of visited nodes =", visited_count
        print "Number of nodes in problem =", num_nodes

        adjacency_list = []
        for _ in range(num_nodes):
            adjacency_list.append(set())
        for edge in problem.edges_lists[index]:
            adjacency_list[edge[0]].add((edge[1]))
        bfs_edge_list(adjacency_list, id_to_tree_path)

    elif args.plot_rewards:
        arr = []
        for i in range(len(w)):
            if participation_count[i] > 0 and abs(w[i]) > 1e-10:
                arr.append(w[i])
        arr = np.log(abs(np.array(arr)))
        print len(arr)
        print abs(arr).min()
        plt.hist(arr)
        plt.show()

    else:
        assert False


def bfs_edge_list(adjacency_list, id_to_tree_path):
    def loop(root):
        print "%d\t%s" % (root, id_to_tree_path[root])
        for child in adjacency_list[root]:
            loop(child)

    loop(0)


def main():
    run(sys.argv[1:])

if __name__ == "__main__":
    main()
