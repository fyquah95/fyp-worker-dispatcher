import argparse
import collections
import concurrent.futures
import csv
import logging
import os
import pickle
import sys
import shutil
import subprocess32 as subprocess

import numpy as np
import sexpdata
import scipy.sparse

import inlining_tree
import py_common


def iterate_rundirs(rundirs):
    for rundir in rundirs:
        opt_data_dir = os.path.join(rundir, "opt_data")

        # initial (can have up to 9 sub steps)
        for substep in range(0, 9):
            substep_dir = os.path.join(opt_data_dir, "initial", str(substep))
            output_dir = os.path.join(
                    os.path.basename(rundir), "opt_data", "initial", str(substep))
            if not os.path.exists(substep_dir):
                continue
            yield (substep_dir, output_dir)

        # parse every step
        for step in range(0, 299):
            for substep in ["current"] + list(range(0, 3)):
                substep_dir = os.path.join(
                        opt_data_dir, str(step), str(substep))
                output_dir = os.path.join(
                        os.path.basename(rundir), "opt_data", str(step), str(substep))
                if not os.path.exists(substep_dir):
                    continue
                yield (substep_dir, output_dir)


def collect_unique_nodes(acc, trace, tree):
    assert isinstance(tree, inlining_tree.Node)

    if tree.name == "Top_level":
        assert trace == []
        acc.add(inlining_tree.Absolute_path([]))
        for child in tree.children:
            collect_unique_nodes(acc, trace, child)
    elif tree.name == "Inlined" or tree.name == "Apply":
        new_trace = trace + [
                ("function", tree.value.path, tree.value.function)
        ]
        acc.add(inlining_tree.Absolute_path(new_trace))
        for child in tree.children:
            collect_unique_nodes(acc, new_trace, child)
    elif tree.name == "Decl":
        new_trace = trace + [("declaration", tree.value)]
        acc.add(inlining_tree.Absolute_path(new_trace))
        for child in tree.children:
            collect_unique_nodes(acc, new_trace, child)
    else:
        print(tree.name)
        assert False


def relabel_to_paths(tree, trace):
    assert isinstance(tree, inlining_tree.Node)

    if tree.name == "Top_level":
        assert trace == []
        children = [relabel_to_paths(child, trace) for child in tree.children]
        return inlining_tree.Node(
                name=tree.name,
                children=children,
                value=inlining_tree.Absolute_path([])
        )
            
    elif tree.name == "Inlined" or tree.name == "Apply":
        new_trace = trace + [(
            "function",
            tree.value.path,
            tree.value.function.closure_origin,
        )]
        children = [
                relabel_to_paths(child, new_trace)
                for child in tree.children
        ]
        return inlining_tree.Node(
                name=tree.name, children=children,
                value=inlining_tree.Absolute_path(new_trace)
        )

    elif tree.name == "Decl":
        new_trace = trace + [("declaration", tree.value.closure_origin)]
        children = [
                relabel_to_paths(child, new_trace)
                for child in tree.children
        ]
        return inlining_tree.Node(
                name=tree.name, children=children,
                value=inlining_tree.Absolute_path(new_trace)
        )
    else:
        print(tree.name)
        assert False


def count_nodes(tree):
    acc = 1

    if tree.name == "Top_level":
        for child in tree.children:
            acc += count_nodes(child)

    elif tree.name == "Inlined" or tree.name == "Apply":
        for child in tree.children:
            acc += count_nodes(child)
    elif tree.name == "Decl":
        for child in tree.children:
            acc += count_nodes(child)
    else:
        print(tree.name)
        assert False

    return acc


def get_tree_depth(tree):
    d = 0
    for child in tree.children:
        d = max(d, 1 + get_tree_depth(child))
    return d


def filling_vertices(tree, tree_path_to_ids, vertices):
    assert isinstance(tree, inlining_tree.Node)

    src_path = tree.value
    src = tree_path_to_ids[src_path]

    index_dispatch = {
            "Inlined": 0,
            "Apply": 1,
            "Decl": 2,
            "Top_level":   3,
    }
    vertices[tree_path_to_ids[tree.value], index_dispatch[tree.name]] = 1
    for child in tree.children:
        filling_vertices(child, tree_path_to_ids, vertices)


def populate_edge_list(edge_list, tree):

    for child in tree.children:
        dest_path = child.value
        dest = dest_path
        src = tree.value
        edge_list.append((hash(src), hash(dest), child.name))  # use hash here to save memory

        populate_edge_list(edge_list, child)


def formulate_problem(results):
    execution_times = []
    execution_directories = []

    tree_paths = set()
    tree_path_to_ids = {}
    tree_path_hash_to_id = {}
    edge_lists = []
    tree_depth = 0

    print type(results)

    for execution_directory, raw_tree, execution_time in results:
        collect_unique_nodes(tree_paths, [], raw_tree)
        tree = relabel_to_paths(raw_tree, [])
        edges = []
        populate_edge_list(edges, tree)
        edge_lists.append(edges)
        tree_depth = max(get_tree_depth(tree), tree_depth)

        execution_directories.append(execution_directory)
        execution_times.append(execution_time)

    for i, tree_path in enumerate(tree_paths):
        tree_path_to_ids[tree_path] = i
        tree_path_hash_to_id[hash(tree_path)] = i

    for edge_list in edge_lists:
        for i, (src_hash, dest_hash, kind) in enumerate(edge_list):
            edge_list[i] = (
                    tree_path_hash_to_id[src_hash],
                    tree_path_hash_to_id[dest_hash],
                    kind
            )

    print "Loaded %d training examples" % len(execution_directories)

    return inlining_tree.Problem(
            tree_path_to_ids=tree_path_to_ids,
            depth=tree_depth,
            node_labels=None,
            execution_times=execution_times,
            execution_directories=execution_directories,
            edges_lists=edge_lists)

parser = argparse.ArgumentParser(description="formulate the problem")
parser.add_argument("--output-dir", type=str, help="output dir", required=True)
parser.add_argument("--experiment-name", type=str, required=True, help="")
parser.add_argument("--debug", action="store_true", help="debugging mode")
parser.add_argument("--dry-run", action="store_true", help="print all the tasks that wil be processed")

def remove_prefix(s, prefix):
    if s.startswith(prefix):
        return str(s[len(prefix):])
    else:
        return None

def script_name_to_exp_name(script_name):
    basename = os.path.basename(script_name)
    prefixes = ["mcts-", "simulated-annealing-", "random-walk-"]
    suffix = None
    for prefix in prefixes:
        suffix = remove_prefix(basename, prefix=prefix)
        if suffix is not None:
            break
    assert suffix is not None

    if suffix.startswith("generic"):
        l = suffix.split(" ")
        assert len(l) == 2
        assert l[1] in py_common.EXPERIMENT_TO_PARAMETERS
        return l[1]
    else:
        return py_common.SCRIPT_SUFFIX_TO_EXP_NAME[suffix]


def read_log_file(interested_exp_name, filename):
    rundirs = []
    with open(filename) as batch_f:
        for line in csv.reader(batch_f):
            (script_name, sub_rundir) = line
            exp_name = script_name_to_exp_name(script_name)
            if exp_name == interested_exp_name:
                rundir = "/media/usb" + sub_rundir
                if os.path.exists(rundir):
                    rundirs.append(rundir)

                rundir = "/media/usb2" + sub_rundir
                if os.path.exists(rundir):
                    rundirs.append(rundir)

                rundir = "/media/usb3/prod/rundir/" + remove_prefix(sub_rundir, prefix="/home/fyquah/fyp/prod/rundir/")
                if os.path.exists(rundir):
                    rundirs.append(rundir)
    return rundirs


def main():
    logging.getLogger().setLevel(logging.INFO)
    args = parser.parse_args()
    rundirs = []
    rundirs.extend(
            read_log_file(args.experiment_name, "../important-logs/v0-data.log"))

    # rundirs.extend(read_log_file(
    #     args.experiment_name, "../important-logs/batch_executor_before_proof.log"))
    # rundirs.extend(read_log_file(
    #     args.experiment_name, "../important-logs/batch_executor_before_module_paths.log"))
    # rundirs.extend(read_log_file(
    #   args.experiment_name, "../important-logs/batch_executor_before_specialise_for.log"))

    tasks = list(set(list(iterate_rundirs(rundirs))))
    tasks.sort()

    if args.dry_run:
        print "Rundirs:"
        print "\n".join(rundirs)
        print "Tasks:"
        print "\n".join(str(x) for x in tasks)
        return 0

    if args.debug:
        tasks = tasks[:10]
        num_threads = 1
    else:
        # TODO: Using multiple threads causes sexp file to be read only
        #       partially -- why?
        num_threads = 1

    bin_name = py_common.EXPERIMENT_TO_PARAMETERS[args.experiment_name].bin_name
    exp_subdir = py_common.EXPERIMENT_TO_PARAMETERS[args.experiment_name].subdir
    logging.info("Found %d tasks to perform data extraction" % len(tasks))
    logging.info("bin_name = %s" % bin_name)
    logging.info("exp_subdir = %s" % exp_subdir)
    tasks.sort()

    if num_threads > 1:
        pool = concurrent.futures.ThreadPoolExecutor(num_threads)
        futures = [
                pool.submit(inlining_tree.load_tree_from_rundir, task, bin_name,
                    ("path_patching", exp_subdir))
                for task in tasks
        ]
        results = [r.result() for r in concurrent.futures.as_completed(futures)]
    elif num_threads == 1:
        def _loop():
            for task, rel_output_dir in tasks:
                output_dir = os.path.join(
                        "/media/usb/home/fyquah/fyp/prod/processed-data",
                        args.experiment_name,
                        rel_output_dir)
                if not os.path.exists(output_dir):
                    os.makedirs(output_dir)
                result = inlining_tree.load_tree_from_rundir(
                        task,
                        bin_name,
                        preprocessing=("path_patching", exp_subdir),
                        output_dir=output_dir)
                if result is not None:
                    yield (task, result[0], result[1])
        results = _loop()
    else:
        assert False

    # loop = asyncio.get_event_loop()
    # done, pending = loop.run_until_complete(asyncio.wait(tasks))
    # assert len(pending) == 0
    # for task in done:
    #     result = task.result()
    #     if result is not None:
    #         results.append(result)
    # loop.close()
    problem = formulate_problem(results)
    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir)
    problem.dump(args.output_dir)


if __name__  == "__main__":
    main()
