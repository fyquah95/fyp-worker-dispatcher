# Analysis

*I assume you have looked through the main README. Throughout this document,
unless otherwise stated, the scripts assumes that you are in `ROOT/analysis`.
ROOT will be used as a variable indicating the path to this repo.*

All the python programs (excluding some very very simply scripts, which can
live in ROOT/scripts/) used in the project lives in this directory. The
main "theoretical" components that lives in this part of the world are:

- call site reward assignment
- inlining policy machine learning
- plotting scripts

There is a lot of shell scripts and arbitrary scripts. The scripts were
not structured in organised directories.

## Structure of Raw Data

The rundirs in raw data is expected to live in one of (which one exactly
doesn't matter):

- `/media/usb/home/fyquah/fyp/prod/rundir/`
- `/media/usb2/home/fyquah/fyp/prod/rundir/`
- `/media/usb3/prod/rundir/`

*The weird naming is due to a weird initial design decision to write raw
data directly into the same disk, rather than writing to an external hard
disk symlinked to `~/fyp/prod/`.*

The following sections below will discuss how the results in the reports
were produced, and the roles of scripts throughout the directory.

Prior to doing anything, run `make` in the `analysis/` directory. This
compiles the shared libraries for faster inlining tree construction from
adjacency lists.

## 4. Call Site Reward Assignment

tl; dr : To reproduce this stage of the project's results, run the following:

```bash
cd path/to/fyp/analysis

# Note: Every script uses all core in the machine, so DO NOT parallelise
#       these 3 scripts.

# Data extraction. Do this only for reproduction level (B) and (A)
VERSION="out-v0-reproduce-relabel" ./extract_all.sh

# Fitting by linear regression. These scripts performs automatic
#   hyperparameter search.
VERSION="out-v0-reproduce-relabel" ./learn_all_lasso.sh
VERSION="out-v0-reproduce-relabel" ./learn_all_ridge.sh

# Print the "optimal" inlining decisions
VERSION="out-v0-reproduce-relabel" ./print_all_lasso.sh
VERSION="out-v0-reproduce-relabel" ./print_all_ridge.sh
```

The main data structure used here is the `expanded-tree`, as described in
the report. This data structure is provided by the module `inlining_tree.py`.
Albeit it's name, it actually provides the expanded tree as described in the
report. The name is as such because I found out about this need for expanded
trees after programming this part of the project.

### Data Extraction

The first stage of the pipeline for reward assignment is to extract all
the inlining trees from every execution (or rundirs). To extract all data,
run:

```bash
# Run this only if you have /media/usb mounted, and want to go from raw data.
# Data extraction. Do this only for reproduction level (B) and (A)
VERSION="out-v0-reproduce-relabel" ./extract_all.sh
```

The script simply runs extraction in parallel, invoking the following files:

- `extract_data_from_experiments.py` - extracts expanded tree into a set of
  adjacency lists, and the execution time of programs.
- `extract_all.sh` and `extract_all_commands.txt` - parallelise data
  extraction automatically throghout data benchmarks.

Running each extraction script using `extract_data_from_experiment.py` creates
a directory called out-$VERSION-reproduce-relabel/<experiment-name>,
which contains the adjacency lists of inlining trees and execution times
(stored in pickle format). The VERSION variable indicates which version of
the data is extracted. For reproduction purposes, you can just assume
that this is always `out-v0-reproduce-relabel`.

One of the early mistakes made in the project was not to have a stable
labelling algorithm with a proof. Some of the data used in the project were
not stably proven. `scripts/clean-with-path-patching` converts from the
unproven labels to the proven in the inlining decision files, and is
automatically invoked during feature extraction. The resultant "cleaned"
and "fixed" inlining decisions are stored in
`/media/usb/home/fyquah/processed-data/`


### Optimisation / "Learning" Reward Values

There is no real notion for selecting gamma (decay factor), choice of
preprocessing function and lambda (regularisation factor, in the case of
ridge regression). There are shell scripts that automatically select
"sensible" values to perform a grid search on.

Run the following script:

```bash
VERSION="out-v0-reproduce-relabel" ./learn_all_lasso.sh
VERSION="out-v0-reproduce-relabel" ./learn_all_ridge.sh
```

The said shell scripts invoke the following scripts:

- `learn_lasso.py` and `learn_ridge.py` construct the problem as a linear
  MSE problem (with matrices) and write the learnt reward values into
  relevant output directories.
- `learn_all_lasso.sh` and `learn_all_ridge.sh`  Runs the python scripts
  with the appropriate command line arguments to specify the directory
  that contains problem definitions.

The results from reward assignment are then written to
`out-$VERSION/exp-name/<lasso | ridge>/hyperparameter-name/rewards.npy`.

### Print Predictive Modelling Inlining Decisions

```bash
VERSION="out-v0-reproduce-relabel" ./print_all_lasso.sh
VERSION="out-v0-reproduce-relabel" ./print_all_ridge.sh
```

These shell script invokes `print_lasso.sh` and `print_ridge.sh` on all
experiments. The 2 said shell scripts in turn invokes `print_lasso.py` and
`print_ridge.py` and an `tree-tools v1 expanded-to-decisions` which
converts the optimal expanded tree and a decision set.

The results are written to
`out-$VERSION/exp-name/<lasso | ridge>/hyperparameter-name/optimal-expanded.sexp`
and `out-$VERSION/exp-name/<lasso | ridge>/hyperparameter-name/optimal.sexp`,
containing the "optimal" expanded tree and complete decision set, as defined
by the optimisation procedure.

### Benchmarking Inlining Decisions

To benchmark the predicting modeling decisions, and assuming you have made
the necessary setups, run:

```bash
cd ../  # Go back to root directory of this repo.
./scripts/run-all-benchmarks ridge-v0-reproduce-relabel
./scripts/run-all-benchmarks lasso-v0-reproduce-relabel
```

This will take awhile ....

### Selecting `h_general` and `H_star` + Printing Results

```bash
python select_best_model_hyperparams.py --model ridge-v0-reproduce-relabel/ \
         --prefix ridge --output-dir out-v0-reproduce-relabel/
python select_best_model_hyperparams.py --model lasso-v0-reproduce-relabel/ \
         --prefix lasso --output-dir out-v0-reproduce-relabel/
```

The output printed to stdout is the latex table of the results. You can paste
the results in (pipe the results into `xclip -sel clip` and paste it in
some latex rendering tool).

The selected hyperparameters are written to
`out-v0-reproduce-relabel/*-best-hyperparams.sexp` and
`out-v0-reproduce-relabel/*-general-hyperparams.txt` respectively. They
are used for deriving labels used in the inlining policy.

### Misc.

The report mentioned the study of the effects of the regularisation factor
in lasso regression, ceteris peribus. The scripts used to study that are

```
learn_lasso_with_alpha.py
learn_lasso_with_alpha.sh
learn_all_lasso_with_alpha.sh  # probably not very useful, you don't want to
                               # study for all experiments.
print_lasso_with_alpha.sh
```

They can be used similarly to the instructions above.

## 5. Learning an Inlining Policy

This is the final stage of the optimisation pipeline, that is to turn
rewards into a model that decides when to inline a function.

The following bash snippets assume that you are in the `analysis` directory,
similar to the previous section.

tl; dr - To reproduce everything from raw data and reward assignments to
inlining policies, run the following code snippet:

```bash
# Run the following two only if you want to go from raw data. They can take
# a long long time (up to 12 hours on a reasonably powerful 8-core machine)
# Data extraction. Do this only for reproduction level (B) and (A)
./scripts/extract-features-from-all-experiments  # assumes /media/usb/ is mounted.
./scripts/dump_features_from_all_experiments.sh  # assumes /media/usb/ is mounted.

./scripts/gen-merged-features-and-rewards
cd ../tools/fyp_compiler_plugins/

# Benchmarks all the models / inlining policies that were discussed in the
# report.
./bulk_bench.sh
```

### Extracting Inlining Query

The following two scripts take the "cleaned" inlining decisions (described
above) and run them with the compilation flags that generates the set of
inlining decisions that were taken in the first round of Flambda inlining.
To do that, run:

```bash
# Call site feature extraction. Do this only for reproduction level (B) and (A)
./scripts/extract-features-from-all-experiments  # assumes /media/usb/ is mounted.
./scripts/dump_features_from_all_experiments.sh  # assumes /media/usb/ is mounted.
```

The first script recompiles the programs with a flag that dumps the features
of function call sites, and stores them in `/media/usb/home/fyquah/processed-data/`
in a file called `queries-v0.bin`.

The second script takes all the queries extracted from the first script,
concatenates them and stores them in `../w/<exp-name>/queries-v0.bin`

These two scripts, combined, can take around a day to run on a 8-core machine.

### Generating Feature-Reward Pairs

There were several different kinds of reward assignment schemes, namely
`H_star`, `h_general` and `h_hand` from both ridge and lasso regression.
The following script generate the combined feature-reward pairs.

```bash
./scripts/gen-merged-features-and-rewards
```

The script invokes `scripts/dump_rewards_from_all_experiments.sh` and
`scripts/merge_feature_and_rewards_from_all_experiments`. The former dumps
the learnt reward vector into
`../w/reward-v0-reproduce-relabel/<lasso | ridge>/<exp-name>/rewards.sexp`.
The latter runs the feature-reward merge procedure (by mathcing labels) and
stores the results in
`report_plots/reward_assignment/data/$REWARD_MODEL/$exp/feature_reward_pair_v3.sexp`.

After invoking the said two scripts, the script invokes `feature_loader.py`,
which concats all the features for a given reward-assignment model together.

_Note: there has been 4 iterations of feature selection. The version used
here (and the report) is V3. The parameter can be configured by modifying
`scripts/gen-merged-features-and-rewards`. V1 to V3 features are
constructed from inlining queries, whereas V0 is constructed directly
from compilation, ie, compilation generates `features-v0.sexp` rather than
`queries-v0.bin`._

### Learning and Benchmarking

As the dataset is not extremely big (only around 4.3k data points), training
is pretty fast (runs under 20 seconds). For that reason, there was no
infrastructure setup to execute machine learning.

To benchmark all models disucssed, run the following (from `analysis`
subdirectory).

```bash
cd ../tools/fyp_compiler_plugins/
./bulk_bench.sh
```

`bulk_bench.sh` benchmarks the lasso regression models (3)
and the regression models whilist logarithimically varying the
uncertainty threshold / bound (3 * 8) and lasso CMoE and ridge CMoE. In
addition to this, the script also benchmarks a "nothing" plugin, that
compiles the program without overidding inlining decisions (as a baseline).

### Printing Benchmark Results

The raw results are stored in
`../results/<exp-name>/plugins/plugin_<model-name>.sexp`.
To print the results, use the `report_plots/machine_learning/print_exec_stats_table.py`
script. The first argument to the script is the name of the the model which
you are interested in inspecting results.

```bash
cd analysis/  # Only if you are not in the analysis subdirectory

# Some examples of printing results:
PYTHONPATH=. python report_plots/machine_learning/print_exec_stats_table.py v1_neural_network_lasso_star
PYTHONPATH=. python report_plots/machine_learning/print_exec_stats_table.py v1_neural_network_ridge_star_0.0005
PYTHONPATH=. python report_plots/machine_learning/print_exec_stats_table.py v1_neural_network_lasso_moe
PYTHONPATH=. python report_plots/machine_learning/print_exec_stats_table.py v1_neural_network_ridge_moe
```
