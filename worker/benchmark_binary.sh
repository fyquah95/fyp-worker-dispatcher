#!/bin/bash

PATH_TO_BINARY="$1"
NUM_RUNS="$2"
ARGS="$3"

function extract_time {
  grep "real" \
    | perl -e 'while(<>){ /(\d+)m((\d+\.)?\d+)s/; print ($1 * 60.0 + $2); }'
}

for i in $(seq 1 "$NUM_RUNS"); do
  { time taskset 0x1 "$PATH_TO_BINARY" $ARGS 1>/dev/null 2>/dev/null; } 2>out_time.txt
  cat out_time.txt | extract_time
  echo ""
done
