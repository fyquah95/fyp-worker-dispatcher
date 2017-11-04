#!/bin/bash

nmcli conn down "eduroam"
nmcli conn down "Imperial-WPA"

sudo /sbin/ifup enp2s0
sleep 1.0

RUNDIR=../prod/controller-rundir/1

nohup jbuilder exec -- controller \
  -config deploy_config.sexp \
  -rundir "$RUNDIR" \
  1>"$RUNDIR/stdout.log" \
  2>"$RUNDIR/stderr.log" &

PID="$!"
echo "Process with PID = $PID"