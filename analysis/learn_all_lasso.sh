#!/bin/bash

set -euo pipefail

# learns everything in all non ill-defined experiments.
TASK_FILE=$(mktemp)
PROBLEMS="almabench bdd fft floats-in-functor hamming kahan-sum kb lens lexifi quicksort sequence sequence-cps"

for problem in $PROBLEMS; do
  echo "./learn_lasso.sh out-$VERSION/$problem &>tmp/$VERSION-$problem-learn-lasso.log" >>$TASK_FILE
done

echo "Task file: $TASK_FILE" >/dev/stderr
cat $TASK_FILE | parallel --jobs 7 --verbose
